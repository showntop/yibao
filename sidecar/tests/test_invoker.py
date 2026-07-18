"""ToolInvoker：tool 唯一执行器（v2 方案 §4）。loop 的两条路径与面板直达都收编到它上面。"""
import asyncio

from yibao_brain.audit import AuditLog
from yibao_brain.invoker import ToolInvoker
from yibao_brain.ipc import Action, ActionResult, RiskLevel
from yibao_brain.llm import ToolCall
from yibao_brain.safety import Decision, Gate, GatePolicy, RiskClassifier
from yibao_brain.skills import EchoSkill, Skill, SkillRegistry


def make_invoker(tmp_path, skills, confirmer=lambda a: True, policy=None):
    reg = SkillRegistry()
    for s in skills:
        reg.register(s)
    return ToolInvoker(
        skills=reg,
        classifier=RiskClassifier(),
        gate=Gate(policy or GatePolicy(auto_below_or_equal=RiskLevel.L1_LOW)),
        log=AuditLog(tmp_path / "a.db"),
        confirmer=confirmer,
    )


def test_propose_builds_action_with_classified_risk(tmp_path):
    inv = make_invoker(tmp_path, [EchoSkill()])
    action = inv.propose(ToolCall(id="t1", skill_id="echo", params={"text": "hi"}))
    assert isinstance(action, Action)
    assert action.skill_id == "echo"
    assert action.risk in (RiskLevel.L0_READONLY, RiskLevel.L1_LOW)


def test_execute_success_and_audit(tmp_path):
    inv = make_invoker(tmp_path, [EchoSkill()])
    action = inv.propose(ToolCall(id="t1", skill_id="echo", params={"text": "hi"}))
    result = inv.execute(action, {"text": "hi"})
    assert result.success
    # 审计落库（不依赖 loop）
    assert len(inv.log.recent()) == 1


def test_execute_skill_exception_becomes_failure_result(tmp_path):
    class BoomSkill(Skill):
        id = "boom"
        description = "炸"

        def run(self, params, ctx):
            raise RuntimeError("炸了")

    inv = make_invoker(tmp_path, [BoomSkill()])
    action = inv.propose(ToolCall(id="t1", skill_id="boom", params={}))
    result = inv.execute(action, {})
    assert not result.success
    assert "技能执行异常" in result.error


def test_gate_deny(tmp_path):
    class DangerSkill(Skill):
        id = "danger"
        description = "危险"
        default_risk = RiskLevel.L4_CRITICAL

        def run(self, params, ctx):
            return ActionResult(success=True)

    policy = GatePolicy(
        auto_below_or_equal=RiskLevel.L1_LOW,
        confirm_below_or_equal=RiskLevel.L3_HIGH,
        allow_critical=False,
    )
    inv = make_invoker(tmp_path, [DangerSkill()], policy=policy)
    action = inv.propose(ToolCall(id="t1", skill_id="danger", params={}))
    assert inv.decide(action) == Decision.DENY


def test_confirm_async_confirmer(tmp_path):
    class DangerSkill(Skill):
        id = "danger"
        description = "危险"
        default_risk = RiskLevel.L3_HIGH

        def run(self, params, ctx):
            return ActionResult(success=True)

    async def say_no(action):
        return False

    inv = make_invoker(tmp_path, [DangerSkill()], confirmer=say_no)
    action = inv.propose(ToolCall(id="t1", skill_id="danger", params={}))
    assert inv.decide(action) == Decision.CONFIRM
    assert asyncio.run(inv.confirm(action)) is False
