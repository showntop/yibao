"""zimeiti 选题状态流转：SetStatusTool（静默，编辑器内用）+ MoveTool（面板/对话用，带 panel+refresh）。

两工具共享 _apply_status（枚举校验 + published_at/published_version 留痕）。
2026-08-25：声明式 move（manifest 自由文本 status、已发布不留痕）退役，由 MoveTool 承接。
"""
from __future__ import annotations

import time
from typing import Any

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.tools import Tool

_STATUSES = ("候选", "写作中", "待发布", "已发布")


def _apply_status(db, tid: str, status: str) -> ActionResult | None:
    """共享流转逻辑：校验 + published_at/published_version 留痕。出错返回 ActionResult。"""
    rows = db.query("topics", where={"id": tid})
    if not rows:
        return ActionResult(success=False, error=f"选题不存在：{tid}")
    fields: dict = {"status": status, "updated_at": int(time.time())}
    if status == "已发布":
        fields["published_at"] = fields["updated_at"]  # 发布留痕：已发布列可按时间回看
        latest = db.query("articles", where={"topic_id": tid}, order="version DESC", limit=1)
        if latest:
            fields["published_version"] = int(latest[0]["version"])  # 复盘对齐：发布的是哪版稿
    db.update("topics", tid, fields)
    return None


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
        err = _apply_status(db, tid, status)
        if err is not None:
            return err
        return ActionResult(success=True, data={"id": tid, "status": status})


class MoveTool(Tool):
    """面板/对话流转（声明式 move 退役后的代码承接，2026-08-25）：与 set_status 同一套
    校验+留痕，差别只在带 panel+refresh（详情页刷新）——修掉声明式 move 的
    「status 自由文本写幽灵状态」「已发布不记 published_at」双标。"""

    id = "zimeiti.move"
    label = "流转选题状态"
    description = "选题状态流转：候选 / 写作中 / 待发布 / 已发布（其他值拒绝）。"
    default_risk = RiskLevel.L1_LOW
    refresh = "zimeiti.get"  # 写后详情面板拿刷新数据（对齐原声明式 move 语义）

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
            return ActionResult(success=False, error=f"未知状态：{status}（可选：{' / '.join(_STATUSES)}）")
        err = _apply_status(db, tid, status)
        if err is not None:
            return err
        result = ActionResult(success=True, data={"id": tid, "status": status})
        result.panel = "zimeiti:detail"
        return result


def make_tools(ctx: Any) -> list[Tool]:
    return [SetStatusTool(), MoveTool()]
