"""Agent 回路：输入 -> 规划 -> 逐步执行 -> 结果，产出 Event 流。"""
from __future__ import annotations

import inspect
import json
from collections.abc import AsyncIterator, Callable, Iterator

from .audit import AuditLog
from .host import Host
from .ipc import Action, Event
from .llm import LLMProvider, LLMResponse, merge_tool_call_deltas
from .memory import Memory
from .safety import Decision, Gate, RiskClassifier
from .skills import SkillContext, SkillRegistry

Confirmer = Callable[[Action], bool]

SYSTEM_PROMPT = (
    "你是译宝，一个桌面 AI 助手。通过调用工具帮用户操作电脑。"
    "若无需调用工具，直接用自然语言回复。"
)


class AgentLoop:
    def __init__(
        self,
        provider: LLMProvider,
        skills: SkillRegistry,
        classifier: RiskClassifier,
        gate: Gate,
        memory: Memory,
        log: AuditLog,
        confirmer: Confirmer | None = None,
        user_id: str = "default",
        max_steps: int = 8,
        host: Host | None = None,
    ):
        self.provider = provider
        self.host = host
        self.skills = skills
        self.classifier = classifier
        self.gate = gate
        self.memory = memory
        self.log = log
        self.confirmer = confirmer or (lambda _a: False)
        self.user_id = user_id
        self.max_steps = max_steps

    def run(self, user_text: str) -> Iterator[Event]:
        memories = self.memory.recall(user_text, self.user_id)
        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        if memories:
            messages.append({"role": "system", "content": "关于用户的记忆：\n" + "\n".join(memories)})
        messages.append({"role": "user", "content": user_text})
        tools = self.skills.openai_tools()

        for _ in range(self.max_steps):
            resp: LLMResponse = self.provider.chat(messages, tools=tools)
            if not resp.tool_calls:
                self.memory.add(user_text, self.user_id)
                yield Event(kind="final_reply", text=resp.text)
                return
            messages.append({"role": "assistant", "content": resp.text})
            proceeded = False
            for tc in resp.tool_calls:
                skill = self.skills.get(tc.skill_id)
                action = Action(
                    skill_id=tc.skill_id,
                    params=tc.params,
                    description=skill.description,
                    risk=self.classifier.classify(
                        Action(skill_id=tc.skill_id, params=tc.params), skill
                    ),
                )
                yield Event(kind="action_proposed", action=action)
                decision = self.gate.decide(action)
                if decision == Decision.CONFIRM:
                    yield Event(kind="confirmation_needed", action=action, confirmation_id=action.id)
                    if not self.confirmer(action):
                        yield Event(kind="error", text=f"用户拒绝执行 {tc.skill_id}")
                        messages.append({"role": "tool", "tool_call_id": tc.id, "content": "用户拒绝执行该操作"})
                        continue
                elif decision == Decision.DENY:
                    yield Event(kind="error", text=f"策略禁止执行 {tc.skill_id}（风险过高）")
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": "策略禁止该操作"})
                    continue
                ctx = SkillContext(host=self.host)
                result = skill.run(tc.params, ctx)
                self.log.record(action, result, screenshot_path=result.screenshot_path)
                yield Event(kind="action_result", action=action, result=result)
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": _stringify_result(result)}
                )
                proceeded = True
            if not proceeded:
                # 所有工具调用都被拒/禁，给模型一次机会换策略
                continue
        yield Event(kind="error", text="达到最大步数仍未完成")

    async def arun(
        self, user_text: str, cancel=None
    ) -> AsyncIterator[Event]:
        """流式异步回路：LLM 边生成边吐 final_reply_chunk；cancel.is_set() 随时打断。

        cancel 为 asyncio.Event（或任何带 is_set() 的对象）。打断时产出 interrupted 并返回。
        confirmer 可同步也可异步（返回协程则 await）。
        """
        memories = self.memory.recall(user_text, self.user_id)
        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        if memories:
            messages.append({"role": "system", "content": "关于用户的记忆：\n" + "\n".join(memories)})
        messages.append({"role": "user", "content": user_text})
        tools = self.skills.openai_tools()

        def cancelled() -> bool:
            return bool(cancel and cancel.is_set())

        for _ in range(self.max_steps):
            if cancelled():
                yield Event(kind="interrupted")
                return
            text_buf = ""
            delta_acc: list = []
            async for delta in self.provider.astream(messages, tools=tools):
                if cancelled():
                    yield Event(kind="interrupted")
                    return
                if delta.text:
                    text_buf += delta.text
                    yield Event(kind="final_reply_chunk", text=delta.text)
                if delta.tool_call_deltas:
                    delta_acc.extend(delta.tool_call_deltas)
            tool_calls = merge_tool_call_deltas(delta_acc)
            if not tool_calls:
                self.memory.add(user_text, self.user_id)
                yield Event(kind="final_reply", text=text_buf)
                return
            messages.append({"role": "assistant", "content": text_buf})
            proceeded = False
            for tc in tool_calls:
                if cancelled():
                    yield Event(kind="interrupted")
                    return
                skill = self.skills.get(tc.skill_id)
                action = Action(
                    skill_id=tc.skill_id,
                    params=tc.params,
                    description=skill.description,
                    risk=self.classifier.classify(
                        Action(skill_id=tc.skill_id, params=tc.params), skill
                    ),
                )
                yield Event(kind="action_proposed", action=action)
                decision = self.gate.decide(action)
                if decision == Decision.CONFIRM:
                    yield Event(kind="confirmation_needed", action=action, confirmation_id=action.id)
                    ok = await self._await_confirmer(action)
                    if cancelled() or not ok:
                        yield Event(kind="error", text=f"用户拒绝执行 {tc.skill_id}")
                        messages.append(
                            {"role": "tool", "tool_call_id": tc.id, "content": "用户拒绝执行该操作"}
                        )
                        continue
                elif decision == Decision.DENY:
                    yield Event(kind="error", text=f"策略禁止执行 {tc.skill_id}（风险过高）")
                    messages.append(
                        {"role": "tool", "tool_call_id": tc.id, "content": "策略禁止该操作"}
                    )
                    continue
                ctx = SkillContext(host=self.host)
                result = skill.run(tc.params, ctx)
                self.log.record(action, result, screenshot_path=result.screenshot_path)
                yield Event(kind="action_result", action=action, result=result)
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": _stringify_result(result)}
                )
                proceeded = True
            if not proceeded:
                continue
        yield Event(kind="error", text="达到最大步数仍未完成")

    async def _await_confirmer(self, action: Action) -> bool:
        """同步/异步 confirmer 兼容：返回协程则直接 await（与 arun 同 loop）。"""
        res = self.confirmer(action)
        if inspect.isawaitable(res):
            res = await res
        return bool(res)


def _stringify_result(result) -> str:
    payload = {"success": result.success, "data": result.data, "error": result.error}
    return json.dumps(payload, ensure_ascii=False)
