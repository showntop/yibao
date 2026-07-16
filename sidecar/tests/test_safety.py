from yibao_brain.safety import Decision, GatePolicy, RiskClassifier, Gate
from yibao_brain.ipc import Action, RiskLevel
from yibao_brain.skills import EchoSkill


def make_action(risk):
    return Action(skill_id="x", risk=risk)


def test_classifier_uses_skill_default():
    c = RiskClassifier()
    assert c.classify(Action(skill_id="echo"), EchoSkill()) == RiskLevel.L1_LOW


def test_classifier_escalates_on_dangerous_params():
    c = RiskClassifier(dangerous_keywords=["delete", "format", "payment"])
    a = Action(skill_id="x", params={"target": "delete everything"}, risk=RiskLevel.L1_LOW)
    assert c.classify(a, None) == RiskLevel.L3_HIGH


def test_gate_auto_for_low_risk():
    gate = Gate(GatePolicy())  # 默认 auto_below=L2
    assert gate.decide(make_action(RiskLevel.L0_READONLY)) == Decision.AUTO
    assert gate.decide(make_action(RiskLevel.L2_MEDIUM)) == Decision.AUTO


def test_gate_confirm_for_high_risk():
    gate = Gate(GatePolicy())
    assert gate.decide(make_action(RiskLevel.L3_HIGH)) == Decision.CONFIRM


def test_gate_deny_for_critical_when_disabled():
    policy = GatePolicy(allow_critical=False)  # L4 直接拒绝
    gate = Gate(policy)
    assert gate.decide(make_action(RiskLevel.L4_CRITICAL)) == Decision.DENY
