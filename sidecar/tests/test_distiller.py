"""Distiller：离线深加工层（感知 v3）。"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from yibao_brain.distiller import DistillerStore, auto_run_due  # noqa: E402


def _store(tmp_path):
    return DistillerStore(str(tmp_path / "distill.db"))


def test_store_add_and_day_items(tmp_path):
    s = _store(tmp_path)
    did = s.add("2026-08-01", "insight", "下午同个报错查了 3 次",
                data={"apps": ["VSCode"]}, confidence=0.8)
    assert isinstance(did, int) and did > 0
    s.add("2026-08-01", "pattern", "工作日上午深度使用 VSCode", confidence=0.9)
    items = s.day_items("2026-08-01")
    assert len(items) == 2
    first = items[0]
    assert first["kind"] == "insight"
    assert first["text"] == "下午同个报错查了 3 次"
    assert first["data"] == {"apps": ["VSCode"]}
    assert first["confidence"] == 0.8
    assert first["projected"] == 0
    assert s.day_items("2026-07-31") == []
    s.close()


def test_store_mark_projected(tmp_path):
    s = _store(tmp_path)
    a = s.add("2026-08-01", "insight", "甲")
    b = s.add("2026-08-01", "insight", "乙")
    s.mark_projected([a])
    items = {it["id"]: it for it in s.day_items("2026-08-01")}
    assert items[a]["projected"] == 1
    assert items[b]["projected"] == 0
    s.close()


def test_store_runs_and_last_auto_run_day(tmp_path):
    s = _store(tmp_path)
    assert s.last_auto_run_day() is None
    s.record_run("2026-08-02", "2026-08-01", "auto", "ok")
    s.record_run("2026-08-02", "2026-08-01", "manual", "ok")
    assert s.last_auto_run_day() == "2026-08-02"
    s.close()


def test_store_purge_keeps_14_days(tmp_path):
    s = _store(tmp_path)
    now = time.time()
    old = now - 15 * 86400
    fresh = now - 1 * 86400
    conn = s._conn  # 直接插行控制 created_at
    conn.execute(
        "INSERT INTO distillations (day, kind, text, created_at) VALUES ('2026-07-17', 'event', '旧', ?)",
        (old,),
    )
    conn.execute(
        "INSERT INTO distillations (day, kind, text, created_at) VALUES ('2026-07-31', 'event', '新', ?)",
        (fresh,),
    )
    conn.execute(
        "INSERT INTO runs (run_day, target_day, source, status, created_at) VALUES ('2026-07-17', '2026-07-16', 'auto', 'ok', ?)",
        (old,),
    )
    conn.commit()
    deleted = s.purge(now=now)
    assert deleted == 2  # 1 条旧 distillation + 1 条旧 run
    assert [it["text"] for it in s.day_items("2026-07-31")] == ["新"]
    s.close()


def test_auto_run_due():
    # 2026-08-02 是本地时间；用 mktime 构造本地时间戳避免时区陷阱
    morning = time.mktime((2026, 8, 2, 5, 0, 0, 0, 0, -1))
    early = time.mktime((2026, 8, 2, 4, 10, 0, 0, 0, -1))
    assert auto_run_due(morning, None) is True
    assert auto_run_due(morning, "2026-08-01") is True   # 上次跑是昨天
    assert auto_run_due(morning, "2026-08-02") is False  # 今日已跑
    assert auto_run_due(early, None) is False            # 还没到 04:17
