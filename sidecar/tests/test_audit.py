from yibao_brain.audit import AuditLog
from yibao_brain.ipc import Action, ActionResult, RiskLevel


def test_record_and_recent(tmp_path):
    log = AuditLog(tmp_path / "audit.db")
    a = Action(skill_id="echo", params={"text": "hi"}, risk=RiskLevel.L1_LOW)
    r = ActionResult(success=True, data={"echo": "hi"})
    log.record(a, r, screenshot_path=None)
    rows = log.recent(10)
    assert len(rows) == 1
    assert rows[0]["skill_id"] == "echo"
    assert rows[0]["success"] == 1
    assert rows[0]["risk"] == int(RiskLevel.L1_LOW)


def test_recent_respects_limit(tmp_path):
    log = AuditLog(tmp_path / "audit.db")
    for i in range(5):
        log.record(Action(skill_id="echo"), ActionResult(success=True))
    assert len(log.recent(2)) == 2
