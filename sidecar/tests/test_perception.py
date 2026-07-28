"""感知 v1：加密观察存储与低成本 sensors。"""
from __future__ import annotations

import os
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet

from yibao_brain import perception
from yibao_brain.perception import (
    LoadUserActivitySkill,
    PerceptionKeyUnavailable,
    PerceptionSensors,
    PerceptionStore,
    build_activity_segments,
)
from yibao_brain.ipc import RiskLevel
from yibao_brain.skills import SkillContext


def _store(tmp_path):
    return PerceptionStore(
        str(tmp_path / "private" / "observations.db"),
        key=Fernet.generate_key(),
    )


def test_store_round_trip_is_encrypted_at_rest(tmp_path):
    store = _store(tmp_path)
    oid = store.append(
        "app",
        "frontmost",
        {"app": "Xcode", "title": "Secret Project"},
        "S1",
        ts=100.0,
    )

    assert oid == 1
    assert store.list(limit=10) == [
        {
            "id": 1,
            "ts": 100.0,
            "source": "app",
            "kind": "frontmost",
            "payload": {"app": "Xcode", "title": "Secret Project"},
            "sensitivity": "S1",
        }
    ]
    raw = (tmp_path / "private" / "observations.db").read_bytes()
    assert b"Secret Project" not in raw
    assert b"Xcode" not in raw
    assert os.stat(tmp_path / "private").st_mode & 0o777 == 0o700
    assert os.stat(tmp_path / "private" / "observations.db").st_mode & 0o777 == 0o600


def test_store_lists_newest_first_and_pages_before_id(tmp_path):
    store = _store(tmp_path)
    first = store.append("activity", "active", {"idle_seconds": 0}, "S1", ts=10)
    second = store.append("activity", "idle", {"idle_seconds": 70}, "S1", ts=20)
    third = store.append("app", "frontmost", {"app": "Finder", "title": ""}, "S1", ts=30)

    assert [x["id"] for x in store.list(limit=2)] == [third, second]
    assert [x["id"] for x in store.list(limit=2, before_id=third)] == [second, first]
    assert store.sources() == ["activity", "app"]


def test_store_tolerates_corrupt_ciphertext(tmp_path):
    store = _store(tmp_path)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "INSERT INTO observations (ts, source, kind, payload, sensitivity) VALUES (?, ?, ?, ?, ?)",
            (5.0, "app", "frontmost", "not-a-fernet-token", "S1"),
        )

    assert store.list()[0]["payload"] == {}


def test_store_query_window_is_inclusive_ordered_and_uses_latest_seed(tmp_path):
    store = _store(tmp_path)
    store.append("app", "frontmost", {"app": "Seed App", "title": "Before"}, "S1", ts=90)
    store.append("app", "frontmost", {"app": "Chrome", "title": "Docs"}, "S1", ts=100)
    store.append("activity", "active", {"idle_seconds": 0}, "S1", ts=150)
    store.append("app", "frontmost", {"app": "Terminal", "title": "yibao"}, "S1", ts=200)
    store.append("app", "frontmost", {"app": "After", "title": "Outside"}, "S1", ts=201)

    rows = store.query_window(100, 200)

    assert [row["ts"] for row in rows] == [100.0, 150.0, 200.0]
    assert store.latest_before("app", 100)["payload"]["app"] == "Seed App"
    assert store.latest_before("activity", 100) is None


def test_store_query_window_keeps_corrupt_rows_for_skip_count(tmp_path):
    store = _store(tmp_path)
    store.append("app", "frontmost", {"app": "Chrome", "title": "Docs"}, "S1", ts=100)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "INSERT INTO observations (ts, source, kind, payload, sensitivity) VALUES (?, ?, ?, ?, ?)",
            (150.0, "activity", "idle", "not-a-fernet-token", "S1"),
        )

    rows = store.query_window(100, 200)

    assert len(rows) == 2
    assert rows[1]["payload"] == {}


def test_store_delete_and_clear_return_real_counts(tmp_path):
    store = _store(tmp_path)
    first = store.append("app", "frontmost", {"app": "A"}, "S1", ts=1)
    store.append("activity", "active", {"idle_seconds": 0}, "S1", ts=2)

    assert store.delete(first) is True
    assert store.delete(first) is False
    assert store.clear() == 1
    assert store.list() == []


def test_store_purge_uses_source_retention(tmp_path):
    day = 86400
    now = 40 * day
    store = _store(tmp_path)
    store.append("app", "frontmost", {"app": "old"}, "S1", ts=now - 31 * day)
    store.append("activity", "active", {"idle_seconds": 0}, "S1", ts=now - 29 * day)
    store.append("screen", "summary", {"text": "old"}, "S3", ts=now - 8 * day)
    store.append("clipboard", "text", {"text": "old"}, "S2", ts=now - 2 * day)

    assert store.purge(now=now) == 3
    assert [x["source"] for x in store.list()] == ["activity"]


def test_build_activity_segments_uses_seeds_and_splits_on_each_state_change():
    segments, truncated = build_activity_segments(
        rows=[
            {
                "ts": 120.0,
                "source": "app",
                "kind": "frontmost",
                "payload": {"app": "Terminal", "title": "yibao"},
            },
            {
                "ts": 150.0,
                "source": "activity",
                "kind": "idle",
                "payload": {"idle_seconds": 60},
            },
        ],
        seeds=[
            {
                "source": "app",
                "kind": "frontmost",
                "payload": {"app": "Chrome", "title": "Docs"},
            },
            {
                "source": "activity",
                "kind": "active",
                "payload": {"idle_seconds": 0},
            },
        ],
        start_ts=100.0,
        end_ts=200.0,
    )

    assert segments == [
        {
            "start_ts": 100.0,
            "end_ts": 120.0,
            "app": "Chrome",
            "title": "Docs",
            "activity": "active",
        },
        {
            "start_ts": 120.0,
            "end_ts": 150.0,
            "app": "Terminal",
            "title": "yibao",
            "activity": "active",
        },
        {
            "start_ts": 150.0,
            "end_ts": 200.0,
            "app": "Terminal",
            "title": "yibao",
            "activity": "idle",
        },
    ]
    assert truncated is False


def test_build_activity_segments_merges_duplicates_and_omits_unknown_app():
    segments, truncated = build_activity_segments(
        rows=[
            {"ts": 110.0, "source": "activity", "kind": "active", "payload": {"idle_seconds": 2}},
            {"ts": 150.0, "source": "activity", "kind": "idle", "payload": {"idle_seconds": 60}},
        ],
        seeds=[
            {"source": "activity", "kind": "active", "payload": {"idle_seconds": 0}},
        ],
        start_ts=100.0,
        end_ts=200.0,
    )

    assert segments == [
        {"start_ts": 100.0, "end_ts": 150.0, "activity": "active"},
        {"start_ts": 150.0, "end_ts": 200.0, "activity": "idle"},
    ]
    assert truncated is False
    assert all("app" not in item and "title" not in item for item in segments)


def test_build_activity_segments_keeps_newest_120_segments():
    rows = [
        {
            "ts": float(i + 1),
            "source": "app",
            "kind": "frontmost",
            "payload": {"app": f"App {i}", "title": f"Window {i}"},
        }
        for i in range(130)
    ]

    segments, truncated = build_activity_segments(
        rows=rows,
        seeds=[],
        start_ts=0.0,
        end_ts=131.0,
    )

    assert len(segments) == 120
    assert segments[0]["app"] == "App 10"
    assert segments[-1] == {
        "start_ts": 130.0,
        "end_ts": 131.0,
        "app": "App 129",
        "title": "Window 129",
    }
    assert truncated is True


def test_load_user_activity_contract_authorization_and_structured_result(tmp_path):
    tz = timezone(timedelta(hours=8))
    now = datetime(2026, 7, 28, 14, 0, tzinfo=tz)
    start = now - timedelta(hours=1)
    store = _store(tmp_path)
    store.append(
        "app", "frontmost", {"app": "Chrome", "title": "Docs"}, "S1", ts=start.timestamp() - 60
    )
    store.append(
        "activity", "active", {"idle_seconds": 0}, "S1", ts=start.timestamp() - 30
    )
    store.append(
        "app", "frontmost", {"app": "Terminal", "title": "yibao"}, "S1", ts=start.timestamp() + 1800
    )
    store.append(
        "activity", "idle", {"idle_seconds": 60}, "S1", ts=start.timestamp() + 2700
    )
    settings = {"perception.model_access": False}
    skill = LoadUserActivitySkill(store, settings, now_provider=lambda: now)
    params = {"start_at": start.isoformat(), "end_at": now.isoformat()}

    schema = skill.openai_schema()
    assert skill.id == "load_user_activity"
    assert skill.default_risk == RiskLevel.L0_READONLY
    assert schema["parameters"]["required"] == ["start_at", "end_at"]
    assert skill.precheck(params) == "模型读取感知记录未开启，请先在设置的感知区域开启"
    assert skill.run(params, SkillContext()).success is False

    settings["perception.model_access"] = True
    assert skill.precheck(params) is None
    result = skill.run(params, SkillContext())

    assert result.success is True
    assert result.data["observation_count"] == 2
    assert result.data["skipped_count"] == 0
    assert result.data["segments"] == [
        {
            "start_at": "2026-07-28T13:00:00+08:00",
            "end_at": "2026-07-28T13:30:00+08:00",
            "app": "Chrome",
            "title": "Docs",
            "activity": "active",
        },
        {
            "start_at": "2026-07-28T13:30:00+08:00",
            "end_at": "2026-07-28T13:45:00+08:00",
            "app": "Terminal",
            "title": "yibao",
            "activity": "active",
        },
        {
            "start_at": "2026-07-28T13:45:00+08:00",
            "end_at": "2026-07-28T14:00:00+08:00",
            "app": "Terminal",
            "title": "yibao",
            "activity": "idle",
        },
    ]
    assert skill.safe_result(result).data == {
        "window": result.data["window"],
        "observation_count": 2,
        "segment_count": 3,
        "truncated": False,
    }
    assert skill.post_reply_notice(result) == "已参考最近活动"


@pytest.mark.parametrize(
    ("start_at", "end_at", "error"),
    [
        ("2026-07-28T13:00:00", "2026-07-28T14:00:00+08:00", "时区"),
        ("2026-07-28T14:00:00+08:00", "2026-07-28T13:00:00+08:00", "早于"),
        ("2026-07-27T12:59:59+08:00", "2026-07-28T14:00:00+08:00", "24 小时"),
        ("2026-07-28T14:00:00+08:00", "2026-07-28T14:06:00+08:00", "未来"),
    ],
)
def test_load_user_activity_rejects_invalid_windows(tmp_path, start_at, end_at, error):
    now = datetime(2026, 7, 28, 14, 0, tzinfo=timezone(timedelta(hours=8)))
    skill = LoadUserActivitySkill(
        _store(tmp_path),
        {"perception.model_access": True},
        now_provider=lambda: now,
    )

    result = skill.run({"start_at": start_at, "end_at": end_at}, SkillContext())

    assert result.success is False
    assert error in result.error


def test_load_user_activity_empty_or_corrupt_window_has_no_notice(tmp_path):
    tz = timezone(timedelta(hours=8))
    now = datetime(2026, 7, 28, 14, 0, tzinfo=tz)
    start = now - timedelta(hours=1)
    store = _store(tmp_path)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "INSERT INTO observations (ts, source, kind, payload, sensitivity) VALUES (?, ?, ?, ?, ?)",
            (start.timestamp() + 60, "app", "frontmost", "broken", "S1"),
        )
    skill = LoadUserActivitySkill(
        store,
        {"perception.model_access": True},
        now_provider=lambda: now,
    )

    result = skill.run(
        {"start_at": start.isoformat(), "end_at": now.isoformat()}, SkillContext()
    )

    assert result.success is True
    assert result.data["segments"] == []
    assert result.data["observation_count"] == 0
    assert result.data["skipped_count"] == 1
    assert skill.post_reply_notice(result) is None


def test_keychain_timeout_fails_closed(monkeypatch):
    monkeypatch.setattr(perception.sys, "platform", "darwin")
    monkeypatch.setattr(perception.getpass, "getuser", lambda: "denny")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 5)

    monkeypatch.setattr(perception.subprocess, "run", timeout)

    with pytest.raises(PerceptionKeyUnavailable, match="超时"):
        perception.key_from_macos_keychain()


def test_frontmost_sampler_rechecks_systemwide_ax_each_call(monkeypatch):
    focused = iter([(101, "one.py"), (202, "pytest")])
    names = {101: "Xcode", 202: "Terminal"}
    monkeypatch.setattr(perception.sys, "platform", "darwin")
    monkeypatch.setattr(perception, "_ax_frontmost", lambda: next(focused), raising=False)
    monkeypatch.setattr(perception, "_localized_app_name", lambda pid, fallback: names[pid])
    monkeypatch.setattr(
        perception,
        "_window_snapshot",
        lambda: pytest.fail("AX 可用时不应依赖屏幕录制权限"),
    )

    assert perception.sample_frontmost() == ("Xcode", "one.py")
    assert perception.sample_frontmost() == ("Terminal", "pytest")


def test_frontmost_sampler_falls_back_to_live_window_order_without_ax(monkeypatch):
    monkeypatch.setattr(perception.sys, "platform", "darwin")
    monkeypatch.setattr(perception, "_ax_frontmost", lambda: None)
    monkeypatch.setattr(
        perception,
        "_window_snapshot",
        lambda: [
            {"kCGWindowLayer": 20, "kCGWindowOwnerPID": 1, "kCGWindowOwnerName": "Overlay"},
            {
                "kCGWindowLayer": 0,
                "kCGWindowOwnerPID": 202,
                "kCGWindowOwnerName": "Terminal",
                "kCGWindowName": "pytest",
            },
        ],
    )
    monkeypatch.setattr(perception, "_localized_app_name", lambda pid, fallback: fallback)
    monkeypatch.setattr(perception, "_ax_title_for_pid", lambda pid: "")

    assert perception.sample_frontmost() == ("Terminal", "pytest")


def test_sensors_do_nothing_while_master_is_off(tmp_path):
    store = _store(tmp_path)
    settings = {
        "perception.master": False,
        "perception.app": True,
        "perception.activity": True,
    }
    sensors = PerceptionSensors(
        store,
        settings,
        app_sampler=lambda: ("Xcode", "Secret"),
        idle_sampler=lambda: 0.0,
    )

    sensors.tick()

    assert store.list() == []


def test_app_sensor_records_only_changes_and_reacts_to_settings(tmp_path):
    store = _store(tmp_path)
    settings = {
        "perception.master": True,
        "perception.app": False,
        "perception.activity": False,
    }
    current = {"value": ("Xcode", "one.py")}
    sensors = PerceptionSensors(
        store,
        settings,
        app_sampler=lambda: current["value"],
        idle_sampler=lambda: 0.0,
    )

    sensors.tick()
    settings["perception.app"] = True
    sensors.tick()
    sensors.tick()
    current["value"] = ("Terminal", "pytest")
    sensors.tick()
    settings["perception.master"] = False
    current["value"] = ("Finder", "Downloads")
    sensors.tick()

    items = list(reversed(store.list()))
    assert [(x["payload"]["app"], x["payload"]["title"]) for x in items] == [
        ("Xcode", "one.py"),
        ("Terminal", "pytest"),
    ]


def test_activity_sensor_uses_sixty_second_threshold_and_only_records_switches(tmp_path):
    store = _store(tmp_path)
    settings = {
        "perception.master": True,
        "perception.app": False,
        "perception.activity": True,
    }
    idle = {"value": 0.0}
    sensors = PerceptionSensors(
        store,
        settings,
        app_sampler=lambda: None,
        idle_sampler=lambda: idle["value"],
    )

    sensors.tick()
    idle["value"] = 59.9
    sensors.tick()
    idle["value"] = 60.0
    sensors.tick()
    idle["value"] = 120.0
    sensors.tick()
    idle["value"] = 3.0
    sensors.tick()

    items = list(reversed(store.list()))
    assert [(x["kind"], x["payload"]["idle_seconds"]) for x in items] == [
        ("active", 0),
        ("idle", 60),
        ("active", 3),
    ]
