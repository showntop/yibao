"""感知 v1：加密观察存储与低成本 sensors。"""
from __future__ import annotations

import os
import sqlite3
import subprocess

import pytest
from cryptography.fernet import Fernet

from yibao_brain import perception
from yibao_brain.perception import PerceptionKeyUnavailable, PerceptionSensors, PerceptionStore


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


def test_keychain_timeout_fails_closed(monkeypatch):
    monkeypatch.setattr(perception.sys, "platform", "darwin")
    monkeypatch.setattr(perception.getpass, "getuser", lambda: "denny")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 5)

    monkeypatch.setattr(perception.subprocess, "run", timeout)

    with pytest.raises(PerceptionKeyUnavailable, match="超时"):
        perception.key_from_macos_keychain()


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
