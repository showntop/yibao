"""技能/动作抽象 + 注册表 + 一个 EchoSkill（真实技能在 Plan 3）。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from .ipc import ActionResult, RiskLevel


@dataclass
class SkillContext:
    """执行上下文：留给真实技能放日志/截图等。Plan 1 暂为空壳。"""
    meta: dict = field(default_factory=dict)


class Skill(ABC):
    id: str = "base"
    description: str = ""
    default_risk: RiskLevel = RiskLevel.L1_LOW

    @abstractmethod
    def run(self, params: dict, ctx: SkillContext) -> ActionResult: ...

    def openai_schema(self) -> dict:
        """OpenAI function-calling 工具描述（子类按需覆盖 params 描述）。"""
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {"type": "object", "properties": {}, "required": []},
        }


class EchoSkill(Skill):
    id = "echo"
    description = "原样回显一段文本（占位技能，用于验证回路）。"

    def run(self, params: dict, ctx: SkillContext) -> ActionResult:
        return ActionResult(success=True, data={"echo": params.get("text", "")})


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        self._skills[skill.id] = skill

    def get(self, skill_id: str) -> Skill:
        return self._skills[skill_id]

    def list(self) -> list[Skill]:
        return list(self._skills.values())

    def openai_tools(self) -> list[dict]:
        return [s.openai_schema() for s in self._skills.values()]
