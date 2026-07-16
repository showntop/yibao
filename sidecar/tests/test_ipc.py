from yibao_brain.ipc import RiskLevel, Action, ActionResult, Event


def test_risk_level_ordering():
    assert RiskLevel.L0_READONLY < RiskLevel.L4_CRITICAL
    assert int(RiskLevel.L3_HIGH) == 3


def test_action_defaults():
    a = Action(skill_id="echo", params={"text": "hi"})
    assert a.skill_id == "echo"
    assert a.params == {"text": "hi"}
    assert a.risk == RiskLevel.L1_LOW
    assert a.id  # auto-assigned non-empty


def test_action_result_optional_fields():
    r = ActionResult(success=True)
    assert r.success is True
    assert r.data == {}
    assert r.error == ""
    assert r.screenshot_path is None


def test_event_kinds():
    e = Event(kind="final_reply", text="done")
    assert e.kind == "final_reply"
    assert e.action is None
    e2 = Event(kind="confirmation_needed", confirmation_id="c1")
    assert e2.confirmation_id == "c1"
