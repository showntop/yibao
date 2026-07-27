"""zimeiti.versions：列出选题的版本历史（编辑器「历史」面板用；正文用 article_read?version=N 取）。"""
from __future__ import annotations

from typing import Any

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.skills import Skill


class VersionsSkill(Skill):
    id = "zimeiti.versions"
    label = "查看版本"
    description = "列出选题的版本历史（版本号/备注/时间，新到旧；不含正文）。"
    default_risk = RiskLevel.L0_READONLY

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {"id": {"type": "string", "description": "选题 id"}},
                    "required": ["id"],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        db = getattr(ctx, "db", None)
        if db is None:
            return ActionResult(success=False, error="底座未提供数据库")
        tid = str(params.get("id") or "").strip()
        if not tid:
            return ActionResult(success=False, error="没给选题 id")
        rows = db.query("articles", where={"topic_id": tid}, order="version DESC", limit=50)
        out = [{"version": int(r["version"]), "note": r.get("note", ""),
                "created_at": int(r.get("created_at") or 0)} for r in rows]
        return ActionResult(success=True, data={"rows": out})


def make_tools(ctx: Any) -> list[Skill]:
    return [VersionsSkill()]
