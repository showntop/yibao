"""fun.open：打开娱乐面板（L0 只读）。"""
from __future__ import annotations

from typing import Any

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.tools import Tool


class OpenFunTool(Tool):
    id = "fun.open"
    label = "打开娱乐"
    description = (
        "打开娱乐面板：B站热门视频（点击用浏览器看）、音乐搜索直达（网易云/QQ音乐）、"
        "每日一言。用户说「摸鱼」「放松一下」「打开娱乐/娱乐面板」「再打开/恢复面板」「有什么好看的/好听的/好玩的」时用它。"
        "注意：即使面板之前打开过，只要用户再次说「打开」，就必须调用本方法重新弹出面板"
        "（用户可能已收起面板，不调用就弹不出来）。带 kw 会直达音乐并自动播放。"
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
                    "kw": {
                        "type": "string",
                        "description": "music tab 的直达歌名/歌手（如「七里香」），面板会直接搜并自动播放；只打开面板可不传",
                    },
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        tab = params.get("tab") or "videos"
        if tab not in ("videos", "music", "quote"):
            tab = "videos"
        data: dict[str, Any] = {"tab": tab}
        kw = str(params.get("kw") or "").strip()
        if kw:
            data["kw"] = kw  # 面板 onInit 消费：切 music tab + 自动搜播
        # explicit：对话点名要娱乐 → 宿主裁决视为用户意图，直接弹面板浮窗
        return ActionResult(success=True, data=data, panel="fun:main", explicit=True)


def make_tools(ctx: Any) -> list[Tool]:
    return [OpenFunTool()]
