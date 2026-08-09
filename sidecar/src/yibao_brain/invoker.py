"""ToolInvoker：tool 的唯一执行器（v2 方案 §4）。

对话入口（agent loop）与面板入口（direct action）都经它执行：
查 registry → 风险闸门 → （确认）→ 执行 → 审计。事件流式路由由各入口自己负责。
"""
from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import replace

from .audit import AuditLog
from .host import Host
from .ipc import Action, ActionResult
from .llm import ToolCall
from .safety import Decision, Gate, RiskClassifier
from .skills import SkillContext, SkillRegistry


class ToolInvoker:
    def __init__(
        self,
        skills: SkillRegistry,
        classifier: RiskClassifier,
        gate: Gate,
        log: AuditLog,
        confirmer=None,
        host: Host | None = None,
    ):
        self.skills = skills
        self.classifier = classifier
        self.gate = gate
        self.log = log
        # 批量 confirmer：list[Action] -> {action.id: (approved, remember)}。
        # 默认空 dict（=全拒），调用方按 .get(id, (False, False)) 读取。
        self.confirmer = confirmer or (lambda _actions: {})
        self.host = host
        # 真实技能后台线程的主动事件通道（由 serve_async 注入；插件技能走 plugin_ctx 自带）。
        self.emit_event = None

    def propose(self, tc: ToolCall) -> Action:
        """tool_call → Action：查 registry 拿声明，分类风险。"""
        skill = self.skills.get(tc.skill_id)
        return Action(
            skill_id=tc.skill_id,
            params=tc.params,
            description=skill.description,
            label=skill.label or tc.skill_id,
            risk=self.classifier.classify(
                Action(skill_id=tc.skill_id, params=tc.params), skill
            ),
        )

    def decide(self, action: Action) -> Decision:
        action_key = self._session_action_key(action)
        if action_key is not None and action_key in self.gate.session_allowed_actions:
            return Decision.AUTO
        return self.gate.decide(action)

    def _session_action_key(self, action: Action) -> str | None:
        skill = self.skills.get(action.skill_id)
        scoped = skill.session_remember_key(action.params)
        if scoped is None:
            return None
        canonical = json.dumps(scoped, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return f"{action.skill_id}:{digest}"

    def precheck(self, action: Action) -> str | None:
        """技能的本地启发式拦截：返回人话原因 = 不执行（loop 拿去让 LLM 换工具重试）。"""
        skill = self.skills.get(action.skill_id)
        try:
            return skill.precheck(action.params)
        except Exception:  # 检查本身出问题不挡路
            return None

    def batch_confirm_sync(self, actions: list[Action]) -> dict[str, tuple[bool, bool]]:
        """同步批量确认：调 self._confirmer(actions) 返回 {action.id: (approved, remember)}。

        异步 confirmer 在同步路径不可用（与单 action 时代一致，抛 RuntimeError）。
        """
        res = self.confirmer(actions)
        if inspect.isawaitable(res):
            raise RuntimeError("同步路径不支持异步 confirmer")
        return res

    async def batch_confirm(self, actions: list[Action]) -> dict[str, tuple[bool, bool]]:
        """异步批量确认：同步/异步 confirmer 兼容，返回协程则 await。"""
        res = self.confirmer(actions)
        if inspect.isawaitable(res):
            res = await res
        return res

    def apply_verdict(self, action: Action, approved: bool, remember: bool) -> None:
        """应用批量确认裁决：approved + remember 时写入会话内授权。

        普通技能按 skill 放行；参数敏感技能只记精确动作指纹。两者都只活在内存，
        后续命中时直接 AUTO（连 confirmation_needed 都不发）。

        loop（run/arun）拿到 batch_confirm 的 verdict 后统一调用，消除三处重复写入，
        并补回「本会话不再询问」stderr 日志（原逐个分支里漏打）。
        """
        skill = self.skills.get(action.skill_id)
        if not approved or not remember:
            return
        import sys

        if skill.allow_session_remember:
            self.gate.session_allowed.add(action.skill_id)
            print(f"[yibao] 本会话不再询问：{action.skill_id}", file=sys.stderr)
            return
        action_key = self._session_action_key(action)
        if action_key is not None:
            self.gate.session_allowed_actions.add(action_key)
            print(f"[yibao] 本会话允许相同参数：{action.skill_id}", file=sys.stderr)

    def execute(self, action: Action, params: dict, meta: dict | None = None) -> ActionResult:
        """执行 + 审计。技能异常转为失败结果，不抛出（不杀 run）。"""
        skill = self.skills.get(action.skill_id)
        try:
            # 插件技能用加载器按 capability 注入好的 plugin_ctx；底座技能照旧给 host
            ctx = skill.plugin_ctx or SkillContext(host=self.host)
            if meta:
                ctx = replace(ctx, meta={**ctx.meta, **meta})
            # 插件声明了 host capability：加载器拿不到 host，在这里嫁接 invoker 的
            if (
                skill.plugin_ctx is not None
                and skill.plugin_ctx.host is None
                and "host" in skill.plugin_capabilities
            ):
                ctx.host = self.host
            # 真实技能（如 watch_command）拿不到 plugin_ctx 的 emit_event——这里补注入，
            # 且不覆盖插件技能已注入的 emit_event。
            if getattr(ctx, "emit_event", None) is None and self.emit_event is not None:
                ctx.emit_event = self.emit_event
            result = skill.run(params, ctx)
        except Exception as e:
            result = ActionResult(success=False, error=f"技能执行异常：{e}")
        self._safe_record(action, self.safe_result(action, result))
        return result

    def safe_result(self, action: Action, result: ActionResult) -> ActionResult:
        """把完整结果转换成壳侧/持久化安全副本；敏感工具转换失败时禁止回退原文。"""
        skill = self.skills.get(action.skill_id)
        try:
            return skill.safe_result(result)
        except Exception:
            if skill.sensitive_output:
                return ActionResult(
                    success=result.success,
                    error=result.error,
                    data={"redacted": True},
                )
            return result

    def _safe_record(self, action: Action, result: ActionResult) -> None:
        """审计写库失败只记 stderr、不中断对话（丢一条日志好过整个 run 崩掉）。"""
        import sys

        try:
            self.log.record(action, result, screenshot_path=result.screenshot_path)
        except Exception as e:
            print(f"[yibao] 审计日志写入失败（已跳过）：{e}", file=sys.stderr)
