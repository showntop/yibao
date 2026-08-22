"""fun.open：打开娱乐面板（L0 只读）。"""
from __future__ import annotations

from typing import Any

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.skills import Skill


class OpenFunSkill(Skill):
    id = "fun.open"
    label = "打开娱乐"
    description = (
        "打开娱乐面板：B站热门视频（点击用浏览器看）、音乐搜索直达（网易云/QQ音乐）、"
        "每日一言。用户说「摸鱼」「放松一下」「有什么好看的/好听的/好玩的」时用它。"
    )
    default_risk = RiskLevel.L0_READONLY

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "tab": {
                        "type": "string",
                        "enum": ["videos", "music", "quote"],
                        "description": "打开后定位到哪个 tab，默认 videos",
                    },
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        tab = params.get("tab") or "videos"
        if tab not in ("videos", "music", "quote"):
            tab = "videos"
        return ActionResult(success=True, data={"tab": tab}, panel="fun:main")


def make_tools(ctx: Any) -> list[Skill]:
    return [OpenFunSkill()]
