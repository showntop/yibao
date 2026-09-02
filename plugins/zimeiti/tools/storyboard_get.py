"""zimeiti.storyboard_get：读选题某版分镜（默认最新版），含完整 shots 数组。

L0 只读，镜像 article_read 的读回姿势（blob content-ref → 解析正文）。
文件自包含（加载器按文件独立 importlib 加载，禁止跨文件 import）。
"""
import json
import os
from pathlib import Path

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.tools import Tool


class StoryboardGet(Tool):
    id = "zimeiti.storyboard_get"
    label = "读分镜"
    description = "读选题的分镜：默认读最新版（含每镜 idx/口播/时长/画面描述）；version 指定读历史版"
    default_risk = RiskLevel.L0_READONLY

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "topic_id": {"type": "string", "description": "选题 id"},
                    "version": {"type": "integer", "description": "版本号（缺省=最新版）"},
                },
                "required": ["topic_id"],
            },
        }

    def run(self, params: dict, ctx) -> ActionResult:
        tid = str(params.get("topic_id", "")).strip()
        if not tid:
            return ActionResult(success=False, error="topic_id 不能为空")
        if not ctx.db.query("topics", where={"id": tid}):
            return ActionResult(success=False, error=f"选题不存在：{tid}")
        where = {"topic_id": tid}
        if params.get("version") is not None:
            try:
                where["version"] = int(params["version"])
            except (TypeError, ValueError):
                return ActionResult(success=False, error=f"非法版本号：{params['version']!r}")
        rows = ctx.db.query("storyboards", where=where, order="version DESC", limit=1)
        if not rows:
            return ActionResult(success=False, error=f"选题 {tid} 还没有分镜")
        row = rows[0]
        raw = str(row["content_path"])
        if raw.startswith("blob://sha256/"):
            if getattr(ctx, "blobs", None) is None:
                return ActionResult(success=False, error="底座未提供 blobs capability")
            path = ctx.blobs.resolve(raw, require_exists=False)
        else:
            cp = Path(raw)
            # 旧库相对路径（相对插件数据根）与绝对路径继续兼容（同 article_read）。
            path = cp if cp.is_absolute() else Path(os.path.dirname(ctx.db.path)) / cp
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            return ActionResult(success=False, error=f"分镜读取失败（{row['content_path']}）：{e}")
        shots = doc.get("shots") if isinstance(doc, dict) else None
        if not isinstance(shots, list):
            return ActionResult(success=False, error=f"分镜内容损坏（{row['content_path']}）：缺 shots 数组")
        return ActionResult(success=True, data={
            "topic_id": tid,
            "version": row["version"],
            "note": row.get("note", ""),
            "shot_count": len(shots),
            "shots": shots,
            "created_at": row.get("created_at", 0),
        })


def make_tools(ctx):
    return [StoryboardGet()]
