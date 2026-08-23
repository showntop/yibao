"""zimeiti.set_status：静默流转选题状态（编辑器内「标为已发布」用）。

与声明式 zimeiti.move 的差别：不带 panel 引用、不发面板事件——编辑器内调用后停留在编辑器，
不跳转详情页/看板（move 的 panel+refresh 是为对话/详情场景设计的）。
"""
from __future__ import annotations

import time
from typing import Any

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.tools import Tool

_STATUSES = ("候选", "写作中", "待发布", "已发布")


class SetStatusTool(Tool):
    id = "zimeiti.set_status"
    label = "更新选题状态"
    description = "流转选题状态（静默版：不发面板事件，编辑器内调用用）。"
    default_risk = RiskLevel.L1_LOW

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "选题 id"},
                        "status": {"type": "string", "enum": list(_STATUSES),
                                   "description": "目标状态"},
                    },
                    "required": ["id", "status"],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        db = getattr(ctx, "db", None)
        if db is None:
            return ActionResult(success=False, error="底座未提供数据库")
        tid = str(params.get("id") or "").strip()
        status = str(params.get("status") or "").strip()
        if not tid:
            return ActionResult(success=False, error="没给选题 id")
        if status not in _STATUSES:
            return ActionResult(success=False, error=f"未知状态：{status}")
        rows = db.query("topics", where={"id": tid})
        if not rows:
            return ActionResult(success=False, error=f"选题不存在：{tid}")
        fields: dict = {"status": status, "updated_at": int(time.time())}
        if status == "已发布":
            fields["published_at"] = fields["updated_at"]  # 发布留痕：已发布列可按时间回看
        db.update("topics", tid, fields)
        return ActionResult(success=True, data={"id": tid, "status": status})


def make_tools(ctx: Any) -> list[Tool]:
    return [SetStatusTool()]
