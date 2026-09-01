"""zimeiti.delete：删选题——级联清理稿件（库行+磁盘文件+目录）、素材关联、发布数据。

声明式 delete（只删 topics 一行）2026-08-25 退役：稿件文件/素材关联/复盘数据会变孤儿。
文件自包含（加载器按文件独立 importlib 加载，禁止跨文件 import）；
数据目录从插件 scoped ctx 的 db.path 推导（同 article_save 姿势，保持插件可搬运）。
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.tools import Tool


class DeleteTopicTool(Tool):
    id = "zimeiti.delete"
    label = "删除选题"
    description = (
        "删除选题并级联清理：稿件版本（库行 + 磁盘文件）、素材的选题关联（素材本体保留）、"
        "发布数据。用户明确要删选题时用。"
    )
    default_risk = RiskLevel.L2_MEDIUM
    refresh = "zimeiti.list"  # 删后看板拿刷新数据（对齐原声明式语义）

    def __init__(self, data_dir: str):
        self._articles_dir = Path(data_dir) / "articles"

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {"id": {"type": "string", "description": "选题 id"}},
                "required": ["id"],
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        db = getattr(ctx, "db", None)
        if db is None:
            return ActionResult(success=False, error="底座未提供数据库")
        tid = str(params.get("id") or "").strip()
        if not tid:
            return ActionResult(success=False, error="没给选题 id")
        if not db.query("topics", where={"id": tid}):
            return ActionResult(success=False, error=f"选题不存在：{tid}")
        # 稿件：删库行 + 磁盘文件（content_path 兼容老库绝对路径），最后收掉该选题的稿件目录
        for row in db.query("articles", where={"topic_id": tid}):
            db.delete("articles", str(row["id"]))
            raw = str(row.get("content_path") or "")
            if raw.startswith("blob://sha256/"):
                continue  # 内容可跨 Artifact 共享，由 BlobStore 引用扫描 + 宽限期统一回收。
            cp = Path(raw)
            p = cp if cp.is_absolute() else self._articles_dir.parent / cp
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        shutil.rmtree(self._articles_dir / tid, ignore_errors=True)
        # 素材：不删素材本体，只摘掉选题关联
        for row in db.query("materials", where={"topic_id": tid}):
            db.update("materials", str(row["id"]), {"topic_id": ""})
        # 发布数据：选题没了，数据无归属
        for row in db.query("post_stats", where={"topic_id": tid}):
            db.delete("post_stats", str(row["id"]))
        db.delete("topics", tid)
        result = ActionResult(success=True, data={"id": tid})
        result.panel = "zimeiti:board"
        return result


def make_tools(ctx: Any) -> list[Tool]:
    return [DeleteTopicTool(os.path.dirname(ctx.db.path))]
