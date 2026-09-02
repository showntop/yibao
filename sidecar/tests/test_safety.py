from yibao_brain.safety import Decision, GatePolicy, RiskClassifier, Gate
from yibao_brain.ipc import Action, RiskLevel
from yibao_brain.tools import EchoTool


def make_action(risk, tool_id="x"):
    return Action(tool_id=tool_id, risk=risk)


def test_classifier_uses_skill_default():
    c = RiskClassifier()
    assert c.classify(Action(tool_id="echo"), EchoTool()) == RiskLevel.L1_LOW


def test_classifier_escalates_on_dangerous_params():
    """关键词升级只作用于有副作用的工具（base L2+）：L2 命中危险词升到 L3。"""
    c = RiskClassifier(dangerous_keywords=["delete", "format", "payment"])
    a = Action(tool_id="x", params={"target": "delete everything"}, risk=RiskLevel.L2_MEDIUM)
    assert c.classify(a, None) == RiskLevel.L3_HIGH


def test_classifier_no_escalation_for_readonly_tools():
    """只读/低危工具（L0/L1）不按查询文本升级：参数里的危险词是「谈论危险」而非
    「执行危险」（如 web_search 查「支付安全」），升级到 L3 属于误伤。"""
    c = RiskClassifier()
    l1 = EchoTool()  # default_risk = L1_LOW（web_search/extract_url 同级）
    a = Action(tool_id="web_search", params={"query": "移动支付 payment 安全"})
    assert c.classify(a, l1) == RiskLevel.L1_LOW
    l0 = EchoTool()
    l0.default_risk = RiskLevel.L0_READONLY
    assert c.classify(a, l0) == RiskLevel.L0_READONLY
    # 无 skill 声明时同样按 base 级别裁决：L1 base 不升级
    assert c.classify(Action(tool_id="x", params={"q": "payment"}, risk=RiskLevel.L1_LOW), None) == RiskLevel.L1_LOW


def test_classifier_escalates_side_effect_tool():
    """有副作用的工具（L2+）维持现行为：参数命中危险词照常升级到 L3。"""
    c = RiskClassifier()
    t = EchoTool()
    t.default_risk = RiskLevel.L2_MEDIUM
    a = Action(tool_id="danger", params={"target": "delete everything"})
    assert c.classify(a, t) == RiskLevel.L3_HIGH


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


def test_gate_session_allowed_skips_confirm():
    """会话内免确认：命中 session_allowed 的技能直接 AUTO（连确认事件都不发）。"""
    gate = Gate(GatePolicy())
    gate.session_allowed.add("danger")
    assert gate.decide(make_action(RiskLevel.L3_HIGH, tool_id="danger")) == Decision.AUTO
    assert gate.decide(make_action(RiskLevel.L3_HIGH, tool_id="other")) == Decision.CONFIRM
