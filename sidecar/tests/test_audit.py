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
