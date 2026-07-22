"""toolbox.text_diff：两段文本逐行对比（L0 只读，纯函数）。"""
from __future__ import annotations

import difflib
from typing import Any

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.skills import Skill

_MAX_BYTES = 512 * 1024
_MAX_LINES = 5000


class TextDiffSkill(Skill):
    id = "toolbox.text_diff"
    description = (
        "对比两段文本的差异（逐行 diff）。用户说「对比 / diff / 看看改了什么」时调用；"
        "返回增删统计与逐行差异并打开工具箱面板展示。"
    )
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
                        "old": {"type": "string", "description": "原文"},
                        "new": {"type": "string", "description": "新文"},
                    },
                    "required": ["old", "new"],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        old = str(params.get("old") or "")
        new = str(params.get("new") or "")
        if not old and not new:
            return ActionResult(success=False, error="输入为空：两段文本至少给一段")
        if len(old.encode("utf-8")) > _MAX_BYTES or len(new.encode("utf-8")) > _MAX_BYTES:
            return ActionResult(success=False, error="输入过大：单段超过 512KB 限制")

        old_lines = old.splitlines()
        new_lines = new.splitlines()
        sm = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)

        added = removed = 0
        lines: list[dict] = []
        truncated = False
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag in ("replace", "delete"):
                for s in old_lines[i1:i2]:
                    removed += 1
                    lines.append({"t": "del", "s": s})
            if tag in ("replace", "insert"):
                for s in new_lines[j1:j2]:
                    added += 1
                    lines.append({"t": "add", "s": s})
            if tag == "equal":
                for s in old_lines[i1:i2]:
                    lines.append({"t": "same", "s": s})
            if len(lines) > _MAX_LINES:
                lines = lines[:_MAX_LINES]
                truncated = True
                break

        return ActionResult(
            success=True,
            data={
                "tool": "diff", "old": old, "new": new,
                "lines": lines, "added": added, "removed": removed,
                "identical": added == 0 and removed == 0,
                "truncated": truncated,
            },
            panel="toolbox:main",
        )


def make_tools(ctx: Any) -> list[Skill]:
    return [TextDiffSkill()]
