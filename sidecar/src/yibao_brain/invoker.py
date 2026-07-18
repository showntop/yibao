"""ToolInvoker：tool 的唯一执行器（v2 方案 §4）。

对话入口（agent loop）与面板入口（direct action）都经它执行：
查 registry → 风险闸门 → （确认）→ 执行 → 审计。事件流式路由由各入口自己负责。
"""
from __future__ import annotations

import inspect

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
        self.confirmer = confirmer or (lambda _a: False)
        self.host = host

    def propose(self, tc: ToolCall) -> Action:
        """tool_call → Action：查 registry 拿声明，分类风险。"""
        skill = self.skills.get(tc.skill_id)
        return Action(
            skill_id=tc.skill_id,
            params=tc.params,
            description=skill.description,
            risk=self.classifier.classify(
                Action(skill_id=tc.skill_id, params=tc.params), skill
            ),
        )

    def decide(self, action: Action) -> Decision:
        return self.gate.decide(action)

    def confirm_sync(self, action: Action) -> bool:
        res = self.confirmer(action)
        if inspect.isawaitable(res):
            raise RuntimeError("同步路径不支持异步 confirmer")
        return bool(res)

    async def confirm(self, action: Action) -> bool:
        """同步/异步 confirmer 兼容：返回协程则 await（与调用方同 loop）。"""
        res = self.confirmer(action)
        if inspect.isawaitable(res):
            res = await res
        return bool(res)

    def execute(self, action: Action, params: dict) -> ActionResult:
        """执行 + 审计。技能异常转为失败结果，不抛出（不杀 run）。"""
        skill = self.skills.get(action.skill_id)
        try:
            result = skill.run(params, SkillContext(host=self.host))
        except Exception as e:
            result = ActionResult(success=False, error=f"技能执行异常：{e}")
        self._safe_record(action, result)
        return result

    def _safe_record(self, action: Action, result: ActionResult) -> None:
        """审计写库失败只记 stderr、不中断对话（丢一条日志好过整个 run 崩掉）。"""
        import sys

        try:
            self.log.record(action, result, screenshot_path=result.screenshot_path)
        except Exception as e:
            print(f"[yibao] 审计日志写入失败（已跳过）：{e}", file=sys.stderr)
