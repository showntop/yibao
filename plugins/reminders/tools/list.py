"""reminders.list：列出待触发提醒（面板管理用；对话里用户问提醒走底座 reminder_list）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.skills import Skill


def _fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%m月%d日 %H:%M")


class RemindersListSkill(Skill):
    id = "reminders.list"
    description = "列出待触发提醒并打开提醒面板（面板管理用）。"
    default_risk = RiskLevel.L0_READONLY

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {"name": self.id, "description": self.description,
                         "parameters": {"type": "object", "properties": {}}},
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        store = getattr(ctx, "reminders", None)
        if store is None:
            return ActionResult(success=False, error="底座未提供提醒存储")
        items = sorted(store.list_pending(), key=lambda r: r["fire_at"])
        rows = [{"id": r["id"], "text": r["text"], "when": _fmt_ts(float(r["fire_at"]))}
                for r in items]
        return ActionResult(success=True, data={"rows": rows}, panel="reminders:main")


def make_tools(ctx: Any) -> list[Skill]:
    return [RemindersListSkill()]
