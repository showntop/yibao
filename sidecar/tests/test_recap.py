"""晨间反刍 + 每日回顾：纯逻辑与存储测试。"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from yibao_brain.distiller import DistillerStore  # noqa: E402

def _store(tmp_path):
    return DistillerStore(str(tmp_path / "distill.db"))

def test_recap_dedup_roundtrip(tmp_path):
    s = _store(tmp_path)
    assert s.recap_last_day() is None        # 初始无标记
    s.set_recap_day("2026-08-04")
    assert s.recap_last_day() == "2026-08-04"
    s.set_recap_day("2026-08-05")             # 覆盖
    assert s.recap_last_day() == "2026-08-05"
    s.close()

def test_recap_marker_survives_reopen(tmp_path):
    """去重标记持久化——新 store 实例（模拟重启）仍命中。"""
    db = str(tmp_path / "distill.db")
    s = DistillerStore(db)
    s.set_recap_day("2026-08-04")
    s.close()
    s2 = DistillerStore(db)
    assert s2.recap_last_day() == "2026-08-04"
    s2.close()

def test_record_run_persists_stats(tmp_path):
    s = _store(tmp_path)
    s.record_run("2026-08-04", "2026-08-03", "auto", "ok",
                 stats={"app_seconds": {"VSCode": 11520}, "active_blocks": [["09:00", "11:00"]]})
    row = s._conn.execute(
        "SELECT stats FROM runs WHERE target_day=?", ("2026-08-03",)
    ).fetchone()
    import json
    assert json.loads(row["stats"])["app_seconds"]["VSCode"] == 11520
    s.close()

def test_runs_stats_migration_idempotent(tmp_path):
    """重复打开存量库不报 duplicate column。"""
    db = str(tmp_path / "distill.db")
    DistillerStore(db).close()
    DistillerStore(db).close()  # 不抛即过
