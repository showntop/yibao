"""ToolInvoker：tool 的唯一执行器（v2 方案 §4）。

对话入口（agent loop）与面板入口（direct action）都经它执行：
查 registry → 风险闸门 → （确认）→ 执行 → 审计。事件流式路由由各入口自己负责。
"""
from __future__ import annotations

from .log import log
import hashlib
import inspect
import json
from dataclasses import replace

from .audit import AuditLog
from .host import Host
from .ipc import Action, ActionResult
from .llm import ToolCall
from .safety import Decision, Gate, RiskClassifier
from .tools import ToolContext, ToolRegistry
from .work_events import materialize_work_events


class ToolInvoker:
    def __init__(
        self,
        skills: ToolRegistry,
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
        # serve_async 注入；测试/旧同步入口保持 None，不改变执行语义。
        self.invocation_sink = None

    def propose(self, tc: ToolCall) -> Action:
        """tool_call → Action：查 registry 拿声明，分类风险。"""
        skill = self.skills.get(tc.tool_id)
        return Action(
            tool_id=tc.tool_id,
            params=tc.params,
            description=skill.description,
            label=skill.label or tc.tool_id,
            risk=self.classifier.classify(
                Action(tool_id=tc.tool_id, params=tc.params), skill
            ),
        )

    def decide(self, action: Action) -> Decision:
        action_key = self._session_action_key(action)
        if action_key is not None and action_key in self.gate.session_allowed_actions:
            return Decision.AUTO
        return self.gate.decide(action)

    def _session_action_key(self, action: Action) -> str | None:
        skill = self.skills.get(action.tool_id)
        scoped = skill.session_remember_key(action.params)
        if scoped is None:
            return None
        canonical = json.dumps(scoped, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return f"{action.tool_id}:{digest}"

    def precheck(self, action: Action) -> str | None:
        """技能的本地启发式拦截：返回人话原因 = 不执行（loop 拿去让 LLM 换工具重试）。"""
        skill = self.skills.get(action.tool_id)
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
        skill = self.skills.get(action.tool_id)
        if not approved or not remember:
            return
        import sys

        if skill.allow_session_remember:
            self.gate.session_allowed.add(action.tool_id)
            log(f"本会话不再询问：{action.tool_id}")
            return
        action_key = self._session_action_key(action)
        if action_key is not None:
            self.gate.session_allowed_actions.add(action_key)
            log(f"本会话允许相同参数：{action.tool_id}")

    def execute(self, action: Action, params: dict, meta: dict | None = None) -> ActionResult:
        """执行 + 审计。技能异常转为失败结果，不抛出（不杀 run）。"""
        skill = self.skills.get(action.tool_id)
        invoke_meta = meta or {}
        invocation_id = (
            self.invocation_sink.begin(action, params, invoke_meta)
            if self.invocation_sink is not None else None
        )
        # 插件技能用加载器按 capability 注入好的 plugin_ctx；底座技能照旧给 host。
        ctx = skill.plugin_ctx or ToolContext(host=self.host)
        if invoke_meta:
            ctx = replace(ctx, meta={**ctx.meta, **invoke_meta})
        # 插件声明了 host capability：加载器拿不到 host，在这里嫁接 invoker 的。
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

        transactional = bool(
            self.invocation_sink is not None
            and skill.work_outputs
            and getattr(ctx, "db", None) is not None
        )
        domain_events_persisted = False
        try:
            if transactional:
                if not invocation_id:
                    raise RuntimeError("无法建立 Invocation，拒绝无事件提交插件业务数据")
                with ctx.db.work_transaction() as tx:
                    result = skill.run(params, ctx)
                    safe = self.safe_result(action, result)
                    if result.success:
                        events = materialize_work_events(
                            skill.work_outputs, params=action.params, data=safe.data or {},
                            tool_id=action.tool_id, strict=True,
                        )
                        ctx.db.enqueue_work_events(invocation_id, events)
                        domain_events_persisted = True
                    else:
                        tx.rollback()
            else:
                result = skill.run(params, ctx)
        except Exception as e:
            prefix = "插件工作事务失败，业务写入已回滚" if transactional else "技能执行异常"
            result = ActionResult(success=False, error=f"{prefix}：{e}")
            domain_events_persisted = False
        safe = safe if transactional and domain_events_persisted else self.safe_result(action, result)
        self._safe_record(action, safe)
        if self.invocation_sink is not None:
            self.invocation_sink.complete(
                invocation_id, action, safe, skill.work_outputs,
                plugin_db=ctx.db if domain_events_persisted else None,
                domain_events_persisted=domain_events_persisted,
            )
        return result

    def safe_result(self, action: Action, result: ActionResult) -> ActionResult:
        """把完整结果转换成壳侧/持久化安全副本；敏感工具转换失败时禁止回退原文。"""
        skill = self.skills.get(action.tool_id)
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
            log(f"审计日志写入失败（已跳过）：{e}")
