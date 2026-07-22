"""reminders.cancel：取消一个待触发提醒（面板按钮直调；api.toml 声明 refresh 自动刷新面板）。"""
from __future__ import annotations

from typing import Any

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.skills import Skill


class RemindersCancelSkill(Skill):
    id = "reminders.cancel"
    description = "取消一个待触发提醒（面板管理用）。"
    default_risk = RiskLevel.L1_LOW

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {"id": {"type": "string", "description": "提醒 id（或前几位）"}},
                    "required": ["id"],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        store = getattr(ctx, "reminders", None)
        if store is None:
            return ActionResult(success=False, error="底座未提供提醒存储")
        rid = str(params.get("id") or "").strip()
        if not rid:
            return ActionResult(success=False, error="没给要取消的提醒 id")
        item = store.cancel(rid)
        if item is None:
            return ActionResult(success=False, error=f"没找到待触发的提醒：{rid}")
        return ActionResult(success=True,
                            data={"id": item["id"], "human": f"已取消提醒：{item['text']}"})


def make_tools(ctx: Any) -> list[Skill]:
    return [RemindersCancelSkill()]
