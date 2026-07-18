"""Agent 回路：输入 -> 规划 -> 逐步执行 -> 结果，产出 Event 流。tool 执行收编到 ToolInvoker。"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Iterator

from .audit import AuditLog
from .history import ConversationHistory
from .host import Host
from .invoker import ToolInvoker
from .ipc import Action, Event
from .llm import LLMProvider, LLMResponse, merge_tool_call_deltas
from .memory import Memory
from .plugins import panel_payload
from .safety import Decision, Gate, RiskClassifier
from .skills import SkillRegistry

Confirmer = Callable[[Action], bool]

SYSTEM_PROMPT = (
    "你是译宝，一个桌面 AI 助手。通过调用工具帮用户操作电脑。"
    "若无需调用工具，直接用自然语言回复。"
)


async def _offload(fn, *args):
    """同步阻塞调用（技能执行 / 记忆读写：HTTP、torch、subprocess）挪到线程池。

    压在事件循环上会冻结整个 sidecar：看门狗 ping 答不了 → 15s 无 pong 被杀。
    """
    return await asyncio.get_running_loop().run_in_executor(None, lambda: fn(*args))


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
        history: ConversationHistory | None = None,
    ):
        self.provider = provider
        self.host = host
        self.memory = memory
        self.log = log
        self.confirmer = confirmer or (lambda _a: False)
        self.user_id = user_id
        self.max_steps = max_steps
        self.history = history
        # tool 执行收编到唯一执行器；loop 只留事件路由与 LLM 往返
        self.invoker = ToolInvoker(skills, classifier, gate, log, self.confirmer, host)

    @property
    def skills(self) -> SkillRegistry:
        """委托给 invoker：替换 registry 时执行器同步生效（测试/运行期换注册表）。"""
        return self.invoker.skills

    @skills.setter
    def skills(self, reg: SkillRegistry) -> None:
        self.invoker.skills = reg

    def run(self, user_text: str) -> Iterator[Event]:
        memories = self.memory.recall(user_text, self.user_id)
        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        if memories:
            messages.append({"role": "system", "content": "关于用户的记忆：\n" + "\n".join(memories)})
        if self.history:
            messages.extend(self.history.messages())
        messages.append({"role": "user", "content": user_text})
        tools = self.skills.openai_tools()

        for _ in range(self.max_steps):
            resp: LLMResponse = self.provider.chat(messages, tools=tools)
            if not resp.tool_calls:
                self.memory.add(user_text, self.user_id)
                if self.history:
                    self.history.record_turn(user_text, resp.text)
                yield Event(kind="final_reply", text=resp.text)
                return
            messages.append(_assistant_with_tools(resp.text, resp.tool_calls))
            proceeded = False
            for tc in resp.tool_calls:
                action = self.invoker.propose(tc)
                yield Event(kind="action_proposed", action=action)
                decision = self.invoker.decide(action)
                if decision == Decision.CONFIRM:
                    yield Event(kind="confirmation_needed", action=action, confirmation_id=action.id)
                    if not self.invoker.confirm_sync(action):
                        yield Event(kind="error", text=f"用户拒绝执行 {tc.skill_id}")
                        messages.append({"role": "tool", "tool_call_id": tc.id, "content": "用户拒绝执行该操作"})
                        continue
                elif decision == Decision.DENY:
                    yield Event(kind="error", text=f"策略禁止执行 {tc.skill_id}（风险过高）")
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": "策略禁止该操作"})
                    continue
                result = self.invoker.execute(action, tc.params)
                yield Event(kind="action_result", action=action, result=result)
                payload = panel_payload(result)  # 结果带面板引用 → 通知壳渲染（schema 缺失给 None 降级）
                if payload is not None:
                    yield Event(kind="panel", payload=payload)
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
        memories = await _offload(self.memory.recall, user_text, self.user_id)
        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        if memories:
            messages.append({"role": "system", "content": "关于用户的记忆：\n" + "\n".join(memories)})
        if self.history:
            messages.extend(self.history.messages())
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
                await _offload(self.memory.add, user_text, self.user_id)
                if self.history:
                    self.history.record_turn(user_text, text_buf)
                yield Event(kind="final_reply", text=text_buf)
                return
            messages.append(_assistant_with_tools(text_buf, tool_calls))
            proceeded = False
            for tc in tool_calls:
                if cancelled():
                    yield Event(kind="interrupted")
                    return
                action = self.invoker.propose(tc)
                yield Event(kind="action_proposed", action=action)
                decision = self.invoker.decide(action)
                if decision == Decision.CONFIRM:
                    yield Event(kind="confirmation_needed", action=action, confirmation_id=action.id)
                    ok = await self.invoker.confirm(action)
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
                result = await _offload(self.invoker.execute, action, tc.params)
                yield Event(kind="action_result", action=action, result=result)
                payload = panel_payload(result)  # 结果带面板引用 → 通知壳渲染（schema 缺失给 None 降级）
                if payload is not None:
                    yield Event(kind="panel", payload=payload)
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": _stringify_result(result)}
                )
                proceeded = True
            if not proceeded:
                continue
        yield Event(kind="error", text="达到最大步数仍未完成")


def _assistant_with_tools(content: str, tool_calls) -> dict:
    """构造 assistant 消息：带 tool_calls 时附 OpenAI 标准字段。

    DeepSeek 等严格校验：tool 消息必须紧跟带 tool_calls 的 assistant 消息，
    否则 400（GLM 容忍缺字段，但不能依赖）。
    """
    msg: dict = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.skill_id,
                    "arguments": json.dumps(tc.params, ensure_ascii=False),
                },
            }
            for tc in tool_calls
        ]
    return msg


def _stringify_result(result) -> str:
    payload = {"success": result.success, "data": result.data, "error": result.error}
    return json.dumps(payload, ensure_ascii=False)
