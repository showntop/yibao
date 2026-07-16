from yibao_brain.loop import AgentLoop
from yibao_brain.llm import FakeProvider, ToolCall
from yibao_brain.skills import SkillRegistry, EchoSkill, Skill, SkillContext
from yibao_brain.safety import RiskClassifier, Gate, GatePolicy
from yibao_brain.audit import AuditLog
from yibao_brain.memory import FakeMemory
from yibao_brain.ipc import ActionResult, RiskLevel


def build_loop(tmp_path, provider, confirmer=lambda a: True):
    reg = SkillRegistry()
    reg.register(EchoSkill())
    return AgentLoop(
        provider=provider,
        skills=reg,
        classifier=RiskClassifier(),
        gate=Gate(GatePolicy()),
        memory=FakeMemory(),
        log=AuditLog(tmp_path / "a.db"),
        confirmer=confirmer,
    )


def test_loop_executes_tool_then_replies(tmp_path):
    # 第一轮模型调用 echo，第二轮给出最终回复
    provider = _TwoStepProvider(
        first=FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="echo", params={"text": "hi"})]),
        second=FakeProvider(text="echoed: hi"),
    )
    loop = build_loop(tmp_path, provider)
    events = list(loop.run("请回显 hi"))
    kinds = [e.kind for e in events]
    assert "action_result" in kinds
    assert kinds[-1] == "final_reply"
    assert "echoed: hi" in events[-1].text


def test_loop_confirms_high_risk(tmp_path):
    class DangerSkill(Skill):
        id = "danger"
        description = "危险占位"
        default_risk = RiskLevel.L3_HIGH

        def run(self, params, ctx):
            return ActionResult(success=True, data={"did": True})

    reg = SkillRegistry()
    reg.register(DangerSkill())
    loop = AgentLoop(
        provider=_TwoStepProvider(
            first=FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="danger", params={})]),
            second=FakeProvider(text="done"),
        ),
        skills=reg,
        classifier=RiskClassifier(),
        gate=Gate(GatePolicy()),
        memory=FakeMemory(),
        log=AuditLog(tmp_path / "a.db"),
        confirmer=lambda a: False,  # 用户拒绝
    )
    events = list(loop.run("做危险的事"))
    kinds = [e.kind for e in events]
    assert "confirmation_needed" in kinds
    # 拒绝后不执行 danger
    assert not any(e.kind == "action_result" and e.result and e.result.data.get("did") for e in events)


class _TwoStepProvider:
    """第一次返回 first，之后都返回 second。"""

    def __init__(self, first, second):
        self._first = first
        self._second = second
        self._n = 0

    def chat(self, messages, tools=None):
        self._n += 1
        return self._first.chat(messages, tools) if self._n == 1 else self._second.chat(messages, tools)
