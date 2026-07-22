"""toolbox.open：打开工具箱面板（L0 只读）。"""
from __future__ import annotations

from typing import Any

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.skills import Skill

_TOOLS = ("json", "diff")


class OpenToolboxSkill(Skill):
    id = "toolbox.open"
    description = "打开工具箱面板（JSON 格式化 / 文本对比 等常用小工具）。"
    default_risk = RiskLevel.L0_READONLY

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tool": {
                            "type": "string",
                            "enum": list(_TOOLS),
                            "description": "打开后定位到哪个工具，默认 json",
                        },
                    },
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        tool = params.get("tool") or "json"
        if tool not in _TOOLS:
            tool = "json"
        return ActionResult(success=True, data={"tool": tool}, panel="toolbox:main")


def make_tools(ctx: Any) -> list[Skill]:
    return [OpenToolboxSkill()]
