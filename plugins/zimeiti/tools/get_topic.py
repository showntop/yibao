"""zimeiti.get：查选题——声明式 get 的代码承接（2026-08-25 v2 #14），按 id 查时拼聚合字段。

聚合（详情页用）：draft = 最新稿版本+字数（「v3 · 2100 字」/「还没写稿」）、
materials = 关联素材数（「2 条」/「—」）。不带 id 的全量查询不拼聚合（避免逐条读稿）。
文件自包含（禁止跨文件 import）；数据目录从 ctx.db.path 推导（同 article_save 姿势）。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.tools import Tool


class GetTopicTool(Tool):
    id = "zimeiti.get"
    label = "查看选题"
    description = (
        "查看选题详情：传 id 查单条（带稿件版本/字数/关联素材数聚合字段）；"
        "也接受 where 条件字典做等值过滤，不传则列全部。"
    )
    default_risk = RiskLevel.L0_READONLY

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "选题 id（传了按 id 查单条）"},
                    "where": {"type": "object", "description": "等值过滤条件（如 {\"status\": \"候选\"}）"},
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        db = getattr(ctx, "db", None)
        if db is None:
            return ActionResult(success=False, error="底座未提供数据库")
        tid = str(params.get("id") or "").strip()
        where = params.get("where") if isinstance(params.get("where"), dict) else None
        if tid:
            rows = db.query("topics", where={"id": tid})
        elif where:
            rows = db.query("topics", where=where)
        else:
            rows = db.query("topics")
        if tid and rows:
            self._enrich(db, rows[0], tid)
        result = ActionResult(success=True, data={"rows": rows})
        result.panel = "zimeiti:detail"
        return result

    def _enrich(self, db, row: dict, tid: str) -> None:
        """单条详情聚合：最新稿版本+字数（读文件拿真实字数）、关联素材数。失败降级为占位文案。"""
        latest = db.query("articles", where={"topic_id": tid}, order="version DESC", limit=1)
        if latest:
            version = int(latest[0]["version"])
            chars = 0
            try:
                cp = Path(str(latest[0].get("content_path") or ""))
                p = cp if cp.is_absolute() else Path(os.path.dirname(db.path)) / cp
                chars = len(p.read_text(encoding="utf-8"))
            except OSError:
                pass
            row["draft"] = f"v{version} · {chars} 字"
        else:
            row["draft"] = "还没写稿"
        mats = db.query("materials", where={"topic_id": tid})
        row["materials"] = f"{len(mats)} 条" if mats else "—"


def make_tools(ctx: Any) -> list[Tool]:
    return [GetTopicTool()]
