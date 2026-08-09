"""ToolInvoker：tool 唯一执行器（v2 方案 §4）。loop 的两条路径与面板直达都收编到它上面。"""
import asyncio
import json

import pytest

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


def _batch_approve(actions):
    """批量 confirmer 测试 helper：全批准、不 remember。"""
    return {a.id: (True, False) for a in actions}


def make_invoker(tmp_path, skills, confirmer=_batch_approve, policy=None):
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


def test_invoker_injects_emit_event_into_real_skill_ctx(tmp_path):
    """真实技能 ctx.emit_event 由 invoker.emit_event 注入；插件技能自带的 emit_event 不被覆盖。"""
    from yibao_brain.ipc import Action, ActionResult, RiskLevel
    from yibao_brain.skills import SkillContext

    captured: dict = {}

    class _Cap(Skill):
        id = "cap"
        default_risk = RiskLevel.L0_READONLY

        def run(self, params, ctx):
            captured["emit"] = getattr(ctx, "emit_event", None)
            return ActionResult(success=True)

    inv = make_invoker(tmp_path, [_Cap()])
    inv.emit_event = "CHAN"
    inv.execute(Action(id="a", skill_id="cap"), {})
    assert captured["emit"] == "CHAN"

    class _Plug(Skill):
        id = "plug"
        default_risk = RiskLevel.L0_READONLY
        plugin_ctx = SkillContext(emit_event="PLUGIN")
        plugin_capabilities = frozenset()

        def run(self, params, ctx):
            captured["plug"] = ctx.emit_event
            return ActionResult(success=True)

    inv2 = make_invoker(tmp_path, [_Plug()])
    inv2.emit_event = "CHAN"
    inv2.execute(Action(id="b", skill_id="plug"), {})
    assert captured["plug"] == "PLUGIN"  # 插件自带的不被 invoker 覆盖


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

    async def say_no(actions):
        # 异步批量 confirmer：全拒绝
        return {a.id: (False, False) for a in actions}

    inv = make_invoker(tmp_path, [DangerSkill()], confirmer=say_no)
    action = inv.propose(ToolCall(id="t1", skill_id="danger", params={}))
    assert inv.decide(action) == Decision.CONFIRM
    verdicts = asyncio.run(inv.batch_confirm([action]))
    assert verdicts[action.id] == (False, False)


def test_batch_confirm_sync_returns_verdicts_per_id(tmp_path):
    """批量 confirmer：invoker.batch_confirm_sync([a1,a2]) 调批量 confirmer，
    返回 {id: (approved, remember)}，每条 action 各自的 verdict。"""
    class DangerSkill(Skill):
        id = "danger"
        description = "危险"
        default_risk = RiskLevel.L3_HIGH

        def run(self, params, ctx):
            return ActionResult(success=True)

    def batch_confirmer(actions):
        # 第一条批准+remember，第二条拒绝
        out = {}
        for i, a in enumerate(actions):
            out[a.id] = (True, True) if i == 0 else (False, False)
        return out

    inv = make_invoker(tmp_path, [DangerSkill()], confirmer=batch_confirmer)
    a1 = inv.propose(ToolCall(id="t1", skill_id="danger", params={}))
    a2 = inv.propose(ToolCall(id="t2", skill_id="danger", params={}))
    out = inv.batch_confirm_sync([a1, a2])
    assert out == {a1.id: (True, True), a2.id: (False, False)}


def test_apply_verdict_does_not_remember_skill_that_disallows_it(tmp_path):
    class AlwaysConfirmSkill(Skill):
        id = "always_confirm"
        description = "每次确认"
        default_risk = RiskLevel.L3_HIGH
        allow_session_remember = False

        def run(self, params, ctx):
            return ActionResult(success=True)

    inv = make_invoker(tmp_path, [AlwaysConfirmSkill()])
    action = inv.propose(ToolCall(id="t1", skill_id="always_confirm", params={}))

    inv.apply_verdict(action, approved=True, remember=True)

    assert "always_confirm" not in inv.gate.session_allowed
    assert not inv.gate.session_allowed_actions


def test_apply_verdict_remembers_only_same_parameters_when_skill_scopes_it(tmp_path):
    class ExactConfirmSkill(Skill):
        id = "exact_confirm"
        description = "相同参数可在会话内记住"
        default_risk = RiskLevel.L3_HIGH
        allow_session_remember = False

        def session_remember_key(self, params):
            return {"command": params.get("command"), "cwd": params.get("cwd")}

        def run(self, params, ctx):
            return ActionResult(success=True)

    inv = make_invoker(tmp_path, [ExactConfirmSkill()])
    first = inv.propose(ToolCall(id="t1", skill_id="exact_confirm", params={"command": "build", "cwd": "/tmp/a"}))
    same = inv.propose(ToolCall(id="t2", skill_id="exact_confirm", params={"cwd": "/tmp/a", "command": "build"}))
    different = inv.propose(ToolCall(id="t3", skill_id="exact_confirm", params={"command": "test", "cwd": "/tmp/a"}))

    inv.apply_verdict(first, approved=True, remember=True)

    assert "exact_confirm" not in inv.gate.session_allowed
    assert len(inv.gate.session_allowed_actions) == 1
    assert inv.decide(same) == Decision.AUTO
    assert inv.decide(different) == Decision.CONFIRM


def test_batch_confirm_sync_rejects_when_confirmer_returns_empty(tmp_path):
    """confirmer 返回空 dict（默认值）= 全拒；调用方按 .get(id, (False,False)) 读。"""
    class DangerSkill(Skill):
        id = "danger"
        description = "危险"
        default_risk = RiskLevel.L3_HIGH

        def run(self, params, ctx):
            return ActionResult(success=True)

    inv = make_invoker(tmp_path, [DangerSkill()], confirmer=lambda _actions: {})
    action = inv.propose(ToolCall(id="t1", skill_id="danger", params={}))
    verdicts = inv.batch_confirm_sync([action])
    assert verdicts.get(action.id, (False, False)) == (False, False)


@pytest.mark.filterwarnings("ignore::RuntimeWarning")
def test_batch_confirm_sync_raises_on_async_confirmer(tmp_path):
    """同步路径调异步 confirmer 抛 RuntimeError（与旧单 action 行为一致）。

    同步路径会调 confirmer 拿到协程后即抛错，协程未 await → RuntimeWarning，
    属本测试预期副作用，静音之。
    """
    class DangerSkill(Skill):
        id = "danger"
        description = "危险"
        default_risk = RiskLevel.L3_HIGH

        def run(self, params, ctx):
            return ActionResult(success=True)

    async def async_confirmer(actions):
        return {a.id: (True, False) for a in actions}

    inv = make_invoker(tmp_path, [DangerSkill()], confirmer=async_confirmer)
    action = inv.propose(ToolCall(id="t1", skill_id="danger", params={}))
    try:
        inv.batch_confirm_sync([action])
        assert False, "应抛 RuntimeError"
    except RuntimeError as e:
        assert "同步路径不支持异步 confirmer" in str(e)


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
