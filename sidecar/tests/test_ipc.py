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


def test_action_ids_unique_across_calls():
    # 回归：进程内自增计数器会在 sidecar 重启后与 audit.db 旧记录撞 id（UNIQUE 约束）
    from yibao_brain.ipc import _new_id

    ids = {_new_id("act") for _ in range(1000)}
    assert len(ids) == 1000
    assert all(i.startswith("act_") for i in ids)


def test_new_id_not_sequential_counter():
    # 回归：计数器 id（act_1）重启后必撞库；id 应带随机段
    from yibao_brain.ipc import _new_id

    assert _new_id("act") != "act_1"
