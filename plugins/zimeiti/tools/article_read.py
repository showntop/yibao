"""zimeiti.article_read：读选题某版稿件正文（默认最新版），供对话改稿/展示。

文件自包含（加载器按文件独立 importlib 加载，禁止跨文件 import）。
"""
from pathlib import Path

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.skills import Skill


class ArticleRead(Skill):
    id = "zimeiti.article_read"
    description = "读选题的稿件正文：默认读最新版；version 指定读历史版。改稿前必读当前稿"
    default_risk = RiskLevel.L0_READONLY

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "选题 id"},
                    "version": {"type": "integer", "description": "版本号（缺省=最新版）"},
                },
                "required": ["id"],
            },
        }

    def run(self, params: dict, ctx) -> ActionResult:
        tid = str(params.get("id", "")).strip()
        if not tid:
            return ActionResult(success=False, error="id 不能为空")
        where = {"topic_id": tid}
        if params.get("version") is not None:
            try:
                where["version"] = int(params["version"])
            except (TypeError, ValueError):
                return ActionResult(success=False, error=f"非法版本号：{params['version']!r}")
        rows = ctx.db.query("articles", where=where, order="version DESC", limit=1)
        if not rows:
            return ActionResult(success=False, error=f"选题 {tid} 还没有稿件")
        row = rows[0]
        try:
            content = Path(row["content_path"]).read_text(encoding="utf-8")
        except OSError as e:
            return ActionResult(success=False, error=f"稿件读取失败（{row['content_path']}）：{e}")
        return ActionResult(
            success=True,
            data={"id": tid, "version": row["version"], "note": row.get("note", ""), "content": content},
        )


def make_tools(ctx):
    return [ArticleRead()]
