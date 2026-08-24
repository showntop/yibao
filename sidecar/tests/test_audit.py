from yibao_brain.audit import AuditLog
from yibao_brain.ipc import Action, ActionResult, RiskLevel


def test_record_and_recent(tmp_path):
    log = AuditLog(tmp_path / "audit.db")
    a = Action(tool_id="echo", params={"text": "hi"}, risk=RiskLevel.L1_LOW)
    r = ActionResult(success=True, data={"echo": "hi"})
    log.record(a, r, screenshot_path=None)
    rows = log.recent(10)
    assert len(rows) == 1
    assert rows[0]["tool_id"] == "echo"
    assert rows[0]["success"] == 1
    assert rows[0]["risk"] == int(RiskLevel.L1_LOW)


def test_recent_respects_limit(tmp_path):
    log = AuditLog(tmp_path / "audit.db")
    for i in range(5):
        log.record(Action(tool_id="echo"), ActionResult(success=True))
    assert len(log.recent(2)) == 2


def test_plugin_call_counts(tmp_path):
    """按 plugin 前缀聚合 tool 执行次数（tool_id = <plugin>.<tool>）。"""
    log = AuditLog(tmp_path / "audit.db")
    log.record(Action(tool_id="notes.keep"), ActionResult(success=True))
    log.record(Action(tool_id="notes.list"), ActionResult(success=True))
    log.record(Action(tool_id="forge.add"), ActionResult(success=True))
    assert log.plugin_call_counts() == {"notes": 2, "forge": 1}


def test_plugin_call_counts_empty(tmp_path):
    """无审计记录返回 {}，不报错。"""
    log = AuditLog(tmp_path / "audit.db")
    assert log.plugin_call_counts() == {}


def test_plugin_call_counts_skips_null_skill(tmp_path):
    """NULL/空 tool_id 跳过（防御：record 不该写空，但聚合侧也别炸）。"""
    log = AuditLog(tmp_path / "audit.db")
    # 正常记录一条
    log.record(Action(tool_id="notes.keep"), ActionResult(success=True))
    # 直接插一条空 tool_id（模拟脏数据）
    with log._lock:
        log.conn.execute("INSERT INTO actions (id, tool_id) VALUES (?, NULL)", ("dirty",))
        log.conn.commit()
    assert log.plugin_call_counts() == {"notes": 1}


def test_migrate_legacy_skill_id_column(tmp_path):
    """老库（2026-08-23 改名前）只有 skill_id 列：打开即迁移为 tool_id，存量行保留。"""
    import sqlite3

    db = tmp_path / "audit.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE actions (id TEXT PRIMARY KEY, ts TEXT DEFAULT (datetime('now')),"
        " skill_id TEXT, params TEXT, risk INTEGER, success INTEGER, error TEXT, data TEXT,"
        " screenshot_path TEXT)"
    )
    conn.execute("INSERT INTO actions (id, skill_id, success) VALUES ('old1', 'notes.keep', 1)")
    conn.commit()
    conn.close()

    log = AuditLog(db)
    log.record(Action(tool_id="forge.add", params={"x": 1}), ActionResult(success=True))
    rows = log.recent(10)
    assert len(rows) == 2
    by_id = {r["id"]: r for r in rows}
    assert by_id["old1"]["tool_id"] == "notes.keep"  # 存量行随迁
    assert "skill_id" not in by_id["old1"]
    assert log.plugin_call_counts() == {"notes": 1, "forge": 1}
