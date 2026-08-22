"""感知 v1：加密观察存储与低成本 sensors。"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import time
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet

from yibao_brain import perception_sensors, perception_store
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


def test_store_query_window_limit_keeps_newest_rows_in_chronological_order(tmp_path):
    store = _store(tmp_path)
    for ts in range(1, 6):
        store.append("app", "frontmost", {"app": f"App {ts}"}, "S1", ts=ts)

    rows = store.query_window(0, 10, limit=3)

    assert [row["ts"] for row in rows] == [3.0, 4.0, 5.0]


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


def test_load_user_activity_dense_window_keeps_recent_context_and_marks_truncated():
    tz = timezone.utc
    start = datetime(2026, 7, 28, 10, 0, tzinfo=tz)
    end = start + timedelta(hours=1)

    class DenseStore:
        def __init__(self):
            self.seed_ts = None

        def query_window(self, start_ts, end_ts, limit=2000):
            assert limit == 2001
            return [
                {
                    "id": i + 1,
                    "ts": start.timestamp() + i,
                    "source": "app",
                    "kind": "frontmost",
                    "payload": {"app": f"App {i}", "title": f"Window {i}"},
                    "sensitivity": "S1",
                }
                for i in range(2001)
            ]

        def latest_before(self, source, ts):
            self.seed_ts = ts
            if source != "app":
                return None
            return {
                "source": "app",
                "kind": "frontmost",
                "payload": {"app": "App 0", "title": "Window 0"},
            }

    store = DenseStore()
    skill = LoadUserActivitySkill(
        store,
        {"perception.model_access": True},
        now_provider=lambda: end,
    )

    result = skill.run(
        {"start_at": start.isoformat(), "end_at": end.isoformat()}, SkillContext()
    )

    assert result.success is True
    assert result.data["truncated"] is True
    assert result.data["observation_count"] == 2000
    assert result.data["segments"][-1]["app"] == "App 2000"
    assert store.seed_ts == start.timestamp() + 1


def test_keychain_timeout_fails_closed(monkeypatch):
    monkeypatch.setattr(perception_store.sys, "platform", "darwin")
    monkeypatch.setattr(perception_store.getpass, "getuser", lambda: "denny")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 5)

    monkeypatch.setattr(perception_store.subprocess, "run", timeout)

    with pytest.raises(PerceptionKeyUnavailable, match="超时"):
        perception_store.key_from_macos_keychain()


def test_frontmost_sampler_rechecks_systemwide_ax_each_call(monkeypatch):
    focused = iter([(101, "one.py"), (202, "pytest")])
    names = {101: "Xcode", 202: "Terminal"}
    monkeypatch.setattr(perception_sensors.sys, "platform", "darwin")
    monkeypatch.setattr(perception_sensors, "_ax_frontmost", lambda: next(focused), raising=False)
    monkeypatch.setattr(perception_sensors, "_localized_app_name", lambda pid, fallback: names[pid])
    monkeypatch.setattr(
        perception_sensors,
        "_window_snapshot",
        lambda: pytest.fail("AX 可用时不应依赖屏幕录制权限"),
    )

    assert perception_sensors.sample_frontmost() == ("Xcode", "one.py")
    assert perception_sensors.sample_frontmost() == ("Terminal", "pytest")


def test_frontmost_bundle_sampler_rechecks_systemwide_ax_each_call(monkeypatch):
    focused = iter([(101, "one.py"), (202, "pytest")])
    bundle_ids = {101: "com.apple.dt.Xcode", 202: "com.apple.Terminal"}
    monkeypatch.setattr(perception_sensors.sys, "platform", "darwin")
    monkeypatch.setattr(perception_sensors, "_ax_frontmost", lambda: next(focused), raising=False)
    monkeypatch.setattr(perception_sensors, "_localized_app_name", lambda pid, fallback: fallback)
    monkeypatch.setattr(perception_sensors, "_bundle_id_for_pid", lambda pid: bundle_ids[pid])

    assert perception_sensors.sample_frontmost_bundle_id() == "com.apple.dt.Xcode"
    assert perception_sensors.sample_frontmost_bundle_id() == "com.apple.Terminal"


def test_frontmost_sampler_falls_back_to_live_window_order_without_ax(monkeypatch):
    monkeypatch.setattr(perception_sensors.sys, "platform", "darwin")
    monkeypatch.setattr(perception_sensors, "_ax_frontmost", lambda: None)
    monkeypatch.setattr(
        perception_sensors,
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
    monkeypatch.setattr(perception_sensors, "_localized_app_name", lambda pid, fallback: fallback)
    monkeypatch.setattr(perception_sensors, "_ax_title_for_pid", lambda pid: "")

    assert perception_sensors.sample_frontmost() == ("Terminal", "pytest")


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


def test_app_sensor_records_bundle_identity(tmp_path):
    store = _store(tmp_path)
    settings = {
        "perception.master": True,
        "perception.app": True,
        "perception.activity": False,
    }
    sensors = PerceptionSensors(
        store,
        settings,
        app_sampler=lambda: ("Xcode", "com.apple.dt.Xcode", "one.py"),
        idle_sampler=lambda: 0.0,
    )

    sensors.tick()

    assert store.list()[0]["payload"] == {
        "app": "Xcode",
        "bundle_id": "com.apple.dt.Xcode",
        "title": "one.py",
    }


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


def test_sensors_expose_fresh_watch_state_without_persisting_heartbeats(tmp_path):
    store = _store(tmp_path)
    now = [100.0]
    settings = {
        "perception.master": True,
        "perception.app": True,
        "perception.activity": True,
    }
    sensors = PerceptionSensors(
        store,
        settings,
        app_sampler=lambda: ("Xcode", "com.apple.dt.Xcode", "one.py"),
        idle_sampler=lambda: 0.0,
        clock=lambda: now[0],
    )
    sensors.tick()
    now[0] = 131.0
    sensors.tick()

    app_rows = [item for item in store.list() if item["source"] == "app"]
    activity_rows = [item for item in store.list() if item["source"] == "activity"]
    assert len(app_rows) == len(activity_rows) == 1
    assert activity_rows[0]["payload"]["segment_started_at"] == 100.0
    assert sensors.watch_state() == {
        "sampled_at": 131.0,
        "app": "Xcode",
        "app_id": "com.apple.dt.Xcode",
        "activity": "active",
        "activity_started_at": 100.0,
    }


# ---------- B 源：树文本序列化 ----------
def test_serialize_tree_text_compact_and_budget():
    from yibao_brain.perception import serialize_tree_text

    tree = {"role": "AXWindow", "title": "主窗", "children": [
        {"role": "AXButton", "title": "保存", "children": []},
        {"role": "AXTextArea", "value": "你好世界", "children": []},
        {"role": "AXGroup", "children": [
            {"role": "AXStaticText", "value": "深层文字", "children": []},
        ]},
    ]}
    text = serialize_tree_text(tree)
    assert "AXWindow: 主窗" in text and "AXButton: 保存" in text
    assert "AXTextArea: 你好世界" in text and "AXStaticText: 深层文字" in text
    assert serialize_tree_text({"role": "AXWindow", "children": []}) == ""
    big = {"role": "AXWindow", "title": "x" * 200, "children": []}
    assert len(serialize_tree_text(big, max_chars=50)) <= 51  # 截断+省略号


# ---------- B 源：sensors screen 段 ----------
def _screen_sensor(store, settings, *, screen_sampler, vision_summarizer=None,
                   secure_input_checker=None, clock=None):
    from yibao_brain.perception import PerceptionSensors

    s = PerceptionSensors(store, settings, app_sampler=lambda: None, idle_sampler=lambda: None,
                          screen_sampler=screen_sampler, vision_summarizer=vision_summarizer,
                          secure_input_checker=secure_input_checker, clock=clock or time.time)
    return s


def test_screen_event_on_change_stores_tree(tmp_path):
    from yibao_brain.perception import PerceptionStore

    store = _store(tmp_path)
    settings = {"perception.master": True, "perception.screen": True}
    tree = {"role": "AXWindow", "title": "Safari — 天气", "children": []}
    samples = iter([("tree", tree, None, "Safari", "com.apple.Safari", "天气")])
    s = _screen_sensor(store, settings,
                       screen_sampler=lambda: next(samples))
    s.tick()
    rows = [r for r in store.list() if r["source"] == "screen"]
    assert len(rows) == 1 and rows[0]["kind"] == "tree" and rows[0]["sensitivity"] == "S3"
    assert "Safari — 天气" in rows[0]["payload"]["text"]
    store.close()


def test_screen_skips_unchanged_and_fires_heartbeat(tmp_path):
    from yibao_brain.perception import PerceptionStore

    store = _store(tmp_path)
    settings = {"perception.master": True, "perception.screen": True}
    tree = {"role": "AXWindow", "title": "同页", "children": []}
    now = [1000.0]
    s = _screen_sensor(store, settings,
                       screen_sampler=lambda: ("tree", tree, None, "App", "com.x", "同页"),
                       clock=lambda: now[0])
    s.tick()                      # t=1000：首次记一条
    s.tick(); s.tick()            # 无变化不记
    assert len([r for r in store.list() if r["source"] == "screen"]) == 1
    now[0] += 301                 # t=1301：超心跳间隔 → 再记
    s.tick()
    assert len([r for r in store.list() if r["source"] == "screen"]) == 2
    store.close()


def test_screen_blacklist_and_privacy_window_and_secure_input(tmp_path):
    from yibao_brain.perception import PerceptionStore

    store = _store(tmp_path)
    settings = {"perception.master": True, "perception.screen": True}
    tree = {"role": "AXWindow", "title": "x", "children": []}
    # L1 黑名单（内置 1Password）
    s = _screen_sensor(store, settings,
                       screen_sampler=lambda: ("tree", tree, None, "1Password", "com.1password.1password", "x"))
    s.tick()
    # L2 隐私窗（Chrome 无痕）
    s2 = _screen_sensor(store, settings,
                        screen_sampler=lambda: ("tree", tree, None, "Chrome", "com.google.Chrome", "新标签页 - 无痕浏览"))
    s2.tick()
    # L3 secure input
    s3 = _screen_sensor(store, settings,
                        screen_sampler=lambda: ("tree", tree, None, "App", "com.x", "y"),
                        secure_input_checker=lambda: True)
    s3.tick()
    assert [r for r in store.list() if r["source"] == "screen"] == []
    store.close()


def test_screen_tree_missing_uses_vision_with_sensitive_filter(tmp_path):
    from yibao_brain.perception import PerceptionStore

    store = _store(tmp_path)
    settings = {"perception.master": True, "perception.screen": True}
    # 树为空 → vision 兜底；概括含卡号 → 敏感丢弃
    s = _screen_sensor(store, settings,
                       screen_sampler=lambda: ("empty", None, "/tmp/shot.png", "Canvas", "com.x", "画板"),
                       vision_summarizer=lambda path: "卡号 6222 0000 0000 0000 可见")
    s.tick()
    assert [r for r in store.list() if r["source"] == "screen"] == []
    # 概括正常 → 存 vision 条目（payload 含 path）
    s2 = _screen_sensor(store, settings,
                        screen_sampler=lambda: ("empty", None, "/tmp/shot.png", "Canvas", "com.y", "画板"),
                        vision_summarizer=lambda path: "Excalidraw 画板，有一个矩形")
    s2.tick()
    rows = [r for r in store.list() if r["source"] == "screen"]
    assert len(rows) == 1 and rows[0]["kind"] == "vision" and rows[0]["payload"]["path"] == "/tmp/shot.png"
    store.close()


def test_screen_daily_budget(tmp_path):
    from yibao_brain.perception import PerceptionStore

    store = _store(tmp_path)
    settings = {"perception.master": True, "perception.screen": True}
    tree = {"role": "AXWindow", "title": "x", "children": []}
    n = [0]
    def sampler():
        n[0] += 1
        return ("tree", tree, None, "App", "com.x", f"第{n[0]}页")
    s = _screen_sensor(store, settings, screen_sampler=sampler)
    for _ in range(125):
        s.tick()
    assert len([r for r in store.list(limit=200) if r["source"] == "screen"]) == 120  # 预算闸
    store.close()


def test_query_window_sources_param(tmp_path):
    from yibao_brain.perception import PerceptionStore

    store = _store(tmp_path)
    store.append("app", "frontmost", {"app": "A"}, "S1", ts=100.0)
    store.append("screen", "tree", {"text": "T"}, "S3", ts=101.0)
    default = store.query_window(0, 200)
    assert all(r["source"] != "screen" for r in default)          # 旧语义不含 screen
    with_screen = store.query_window(0, 200, sources=("screen",))
    assert len(with_screen) == 1 and with_screen[0]["source"] == "screen"
    store.close()


def test_screen_sensitive_vision_discard_still_debounces(tmp_path):
    """敏感概括丢弃后同画面不再重复调 vision（去抖），但概括失败(None)允许重试。"""
    from yibao_brain.perception import PerceptionStore

    store = _store(tmp_path)
    settings = {"perception.master": True, "perception.screen": True}
    calls = []
    s = _screen_sensor(store, settings,
                       screen_sampler=lambda: ("empty", None, "/tmp/s.png", "App", "com.x", "银行"),
                       vision_summarizer=lambda p: calls.append(p) or "卡号 6222000000000000")
    s.tick(); s.tick(); s.tick()
    assert [r for r in store.list() if r["source"] == "screen"] == []
    assert len(calls) == 1  # 敏感丢弃但已去抖：只调一次
    store.close()


def test_screen_filtered_frame_file_removed(tmp_path):
    """被黑名单过滤的截图帧：文件即删（明文不残留）。"""
    from yibao_brain.perception import PerceptionStore

    store = _store(tmp_path)
    shot = tmp_path / "frame.png"
    shot.write_bytes(b"png")
    settings = {"perception.master": True, "perception.screen": True}
    s = _screen_sensor(store, settings,
                       screen_sampler=lambda: ("empty", None, str(shot), "1Password", "com.1password.1password", "x"))
    s.tick()
    assert [r for r in store.list() if r["source"] == "screen"] == []
    assert not shot.exists()
    store.close()


def test_screen_vision_frame_removed_after_summary(tmp_path):
    """vision 概括完成后原图即删（概括文本留存，payload 留 path 溯源）。"""
    from yibao_brain.perception import PerceptionStore

    store = _store(tmp_path)
    shot = tmp_path / "frame.png"
    shot.write_bytes(b"png")
    settings = {"perception.master": True, "perception.screen": True}
    s = _screen_sensor(store, settings,
                       screen_sampler=lambda: ("empty", None, str(shot), "Canvas", "com.y", "画板"),
                       vision_summarizer=lambda p: "Excalidraw 画板，有一个矩形")
    s.tick()
    rows = [r for r in store.list() if r["source"] == "screen"]
    assert len(rows) == 1 and rows[0]["kind"] == "vision"
    assert rows[0]["payload"]["path"] == str(shot)
    assert not shot.exists()
    store.close()


# ---------- 消费工具：load_screen_content ----------
def test_load_screen_content_gate_and_window(tmp_path):
    from yibao_brain.perception import LoadScreenContentSkill

    store = _store(tmp_path)
    store.append("screen", "tree", {"app": "Safari", "title": "天气", "text": "AXWindow: 天气页"}, "S3")
    store.append("screen", "vision", {"app": "Canvas", "text": "画板一个矩形", "path": "/x.png"}, "S3")
    store.append("app", "frontmost", {"app": "Safari"}, "S1")
    # 未开 model_access → 拦截
    skill = LoadScreenContentSkill(store, {"perception.model_access": False})
    r = skill.run({}, SkillContext())
    assert not r.success and "未开启" in (r.error or "")
    # 开启 → 返回 screen 条目（不含 app 源），按时间倒序
    skill2 = LoadScreenContentSkill(store, {"perception.model_access": True})
    r2 = skill2.run({}, SkillContext())
    assert r2.success and r2.data["count"] == 2
    assert all(it["kind"] in ("tree", "vision") for it in r2.data["items"])
    assert r2.data["items"][0]["app"] == "Canvas"
    assert skill2.safe_result(r2).data["count"] == 2 and "items" not in skill2.safe_result(r2).data
    assert skill2.post_reply_notice(r2) == "已参考屏幕内容"
    store.close()


def test_load_screen_content_limit_truncation_and_empty_window(tmp_path):
    from yibao_brain.perception import LoadScreenContentSkill

    now = time.time()
    store = _store(tmp_path)
    for i in range(3):
        store.append("screen", "tree", {"app": f"App{i}", "text": f"页面{i}"}, "S3", ts=now - 90 + i * 10)
    store.append("screen", "tree", {"app": "Old", "text": "窗口外"}, "S3", ts=now - 7200)
    skill = LoadScreenContentSkill(store, {"perception.model_access": True})

    # limit 截断：留最新 limit 条、倒序、标记 truncated；窗口外条目不进结果
    r = skill.run({"minutes": 30, "limit": 2}, SkillContext())
    assert r.success and r.data["count"] == 2 and r.data["truncated"] is True
    assert [it["app"] for it in r.data["items"]] == ["App2", "App1"]

    # 窗口内无条目（1 分钟窗覆盖不到 70~90 秒前的条目）→ 空结果且无 notice
    empty = skill.run({"minutes": 1}, SkillContext())
    assert empty.success and empty.data["count"] == 0 and empty.data["truncated"] is False
    assert skill.post_reply_notice(empty) is None

    # minutes 上限 1440（超出收敛）
    clamped = skill.run({"minutes": 99999}, SkillContext())
    assert clamped.success and clamped.data["minutes"] == 1440
    store.close()
