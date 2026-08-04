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

def test_recent_days_aggregates_status_stats_items(tmp_path):
    from datetime import date, timedelta
    s = _store(tmp_path)
    today = date.today()
    data_day = (today - timedelta(days=1)).isoformat()      # 昨天有数据
    pending_day = (today - timedelta(days=2)).isoformat()    # 前天无 run
    s.add(data_day, "insight", "切了 14 次", confidence=0.8)
    s.record_run(today.isoformat(), data_day, "auto", "ok",
                 stats={"app_seconds": {"VSCode": 11520}})
    days = s.recent_days(3)
    by_day = {d["day"]: d for d in days}
    assert by_day[data_day]["status"] == "ok"
    assert by_day[data_day]["stats"]["app_seconds"]["VSCode"] == 11520
    assert len(by_day[data_day]["items"]) == 1
    # 没提炼的天：pending + 空
    assert by_day[pending_day]["status"] == "pending"
    assert by_day[pending_day]["items"] == []
    # 倒序
    assert days[0]["day"] > days[1]["day"]
    s.close()

from yibao_brain.distiller import recap_select, build_recap_text  # noqa: E402

def _item(kind, text, conf=None, id=1):
    d = {"id": id, "day": "2026-08-03", "kind": kind, "text": text,
         "data": {}, "confidence": conf, "projected": 0, "created_at": 0.0}
    return d

def test_recap_select_insights_by_confidence():
    items = [_item("insight", "低", 0.4), _item("insight", "高", 0.9),
             _item("insight", "中", 0.7), _item("insight", "四", 0.65)]
    sel = recap_select(items)
    assert [s["text"] for s in sel] == ["高", "中", "四"]   # 降序 ≤3，0.4 被挤掉

def test_recap_select_falls_back_to_event():
    items = [_item("pattern", "模式", 0.9), _item("event", "深夜活跃", 0.9, id=2)]
    sel = recap_select(items)
    assert len(sel) == 1 and sel[0]["kind"] == "event"

def test_recap_select_empty_when_nothing():
    assert recap_select([]) == []
    assert recap_select([_item("pattern", "仅模式", 0.9)]) == []

def test_build_recap_text_format():
    sel = [_item("insight", "建议A"), _item("insight", "建议B")]
    txt = build_recap_text(sel)
    assert txt.startswith("早上好")
    assert "①" in txt and "建议A" in txt and "②" in txt and "建议B" in txt
    assert build_recap_text([]) == ""

from yibao_brain.server import _recap_decide  # noqa: E402

def test_recap_decide_gates_off():
    """闸门任一关 → 不出。"""
    assert _recap_decide(settings={"perception.master": False,
        "perception.distill": True, "perception.recap": True},
        last_recap_day="2026-08-04", today="2026-08-05",
        yesterday_items=[_item("insight", "x", 0.9)]) is None
    assert _recap_decide(settings={"perception.master": True,
        "perception.distill": True, "perception.recap": False},
        last_recap_day=None, today="2026-08-05",
        yesterday_items=[_item("insight", "x", 0.9)]) is None

def test_recap_decide_dedup_today():
    """今天已反刍 → 不出。"""
    assert _recap_decide(settings={"perception.master": True,
        "perception.distill": True, "perception.recap": True},
        last_recap_day="2026-08-05", today="2026-08-05",
        yesterday_items=[_item("insight", "x", 0.9)]) is None

def test_recap_decide_no_content():
    """昨日无产物 → 不出。"""
    assert _recap_decide(settings={"perception.master": True,
        "perception.distill": True, "perception.recap": True},
        last_recap_day=None, today="2026-08-05", yesterday_items=[]) is None

def test_recap_decide_returns_text_and_day():
    r = _recap_decide(settings={"perception.master": True,
        "perception.distill": True, "perception.recap": True},
        last_recap_day=None, today="2026-08-05",
        yesterday_items=[_item("insight", "切了14次——建议…", 0.9)])
    assert r is not None and "建议" in r["text"] and r["day"] is not None
