"""Agent 回路：输入 -> 规划 -> 逐步执行 -> 结果，产出 Event 流。"""
from __future__ import annotations

import json
from collections.abc import Callable, Iterator

from .audit import AuditLog
from .ipc import Action, Event
from .llm import LLMProvider, LLMResponse
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
    ):
        self.provider = provider
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
                ctx = SkillContext()
                result = skill.run(tc.params, ctx)
                self.log.record(action, result)
                yield Event(kind="action_result", action=action, result=result)
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": _stringify_result(result)}
                )
                proceeded = True
            if not proceeded:
                # 所有工具调用都被拒/禁，给模型一次机会换策略
                continue
        yield Event(kind="error", text="达到最大步数仍未完成")


def _stringify_result(result) -> str:
    payload = {"success": result.success, "data": result.data, "error": result.error}
    return json.dumps(payload, ensure_ascii=False)
