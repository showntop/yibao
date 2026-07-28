"""ToolInvoker：tool 唯一执行器（v2 方案 §4）。loop 的两条路径与面板直达都收编到它上面。"""
import asyncio
import json

from yibao_brain.audit import AuditLog
from yibao_brain.invoker import ToolInvoker
from yibao_brain.ipc import Action, ActionResult, RiskLevel
from yibao_brain.llm import ToolCall
from yibao_brain.safety import Decision, Gate, GatePolicy, RiskClassifier
from yibao_brain.skills import EchoSkill, Skill, SkillRegistry


class _SensitiveSkill(Skill):
    id = "sensitive"
    description = "返回敏感数据"
    default_risk = RiskLevel.L0_READONLY
    sensitive_output = True

    def run(self, params, ctx):
        return ActionResult(success=True, data={"secret": "Window Secret", "count": 1})

    def safe_result(self, result):
        return ActionResult(success=result.success, error=result.error, data={"count": 1})


class _BrokenSafeResultSkill(_SensitiveSkill):
    id = "broken_sensitive"

    def safe_result(self, result):
        raise RuntimeError("摘要失败")


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
    assert action.label == "回声测试"  # 技能声明了 label 则透传（过程展示用）


def test_propose_label_falls_back_to_skill_id(tmp_path):
    class _NoLabel(Skill):
        id = "nolabel"
        description = "没声明 label 的技能"

        def run(self, params, ctx):
            return ActionResult(success=True)

    inv = make_invoker(tmp_path, [_NoLabel()])
    action = inv.propose(ToolCall(id="t1", skill_id="nolabel", params={}))
    assert action.label == "nolabel"  # 回退 skill_id，前端一定有得显示


def test_execute_success_and_audit(tmp_path):
    inv = make_invoker(tmp_path, [EchoSkill()])
    action = inv.propose(ToolCall(id="t1", skill_id="echo", params={"text": "hi"}))
    result = inv.execute(action, {"text": "hi"})
    assert result.success
    # 审计落库（不依赖 loop）
    assert len(inv.log.recent()) == 1


def test_sensitive_skill_audits_only_safe_result(tmp_path):
    inv = make_invoker(tmp_path, [_SensitiveSkill()])
    action = inv.propose(ToolCall(id="t1", skill_id="sensitive", params={}))

    result = inv.execute(action, {})

    assert result.data["secret"] == "Window Secret"
    audit_data = json.loads(inv.log.recent()[0]["data"])
    assert audit_data == {"count": 1}
    assert "Window Secret" not in json.dumps(audit_data, ensure_ascii=False)


def test_sensitive_skill_safe_result_failure_redacts_audit(tmp_path):
    inv = make_invoker(tmp_path, [_BrokenSafeResultSkill()])
    action = inv.propose(ToolCall(id="t1", skill_id="broken_sensitive", params={}))

    result = inv.execute(action, {})

    assert result.data["secret"] == "Window Secret"
    audit_data = json.loads(inv.log.recent()[0]["data"])
    assert audit_data == {"redacted": True}
    assert "Window Secret" not in json.dumps(audit_data, ensure_ascii=False)


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


def test_precheck_blocks_and_passes(tmp_path):
    """precheck 返回人话原因 = 拦截；None = 放行；技能没覆盖 / 检查本身炸了都放行。"""
    class PickySkill(Skill):
        id = "picky"
        description = "挑剔"

        def run(self, params, ctx):
            return ActionResult(success=True)

        def precheck(self, params):
            return "该走别的工具" if params.get("bad") else None

    class BoomCheckSkill(Skill):
        id = "boomcheck"
        description = "检查会炸"

        def run(self, params, ctx):
            return ActionResult(success=True)

        def precheck(self, params):
            raise RuntimeError("检查炸了")

    inv = make_invoker(tmp_path, [PickySkill(), BoomCheckSkill(), EchoSkill()])
    blocked = inv.propose(ToolCall(id="t1", skill_id="picky", params={"bad": 1}))
    assert inv.precheck(blocked) == "该走别的工具"
    ok = inv.propose(ToolCall(id="t2", skill_id="picky", params={}))
    assert inv.precheck(ok) is None
    boom = inv.propose(ToolCall(id="t3", skill_id="boomcheck", params={}))
    assert inv.precheck(boom) is None  # 检查本身出问题不挡路
    echo = inv.propose(ToolCall(id="t4", skill_id="echo", params={}))
    assert inv.precheck(echo) is None  # 基类默认放行
