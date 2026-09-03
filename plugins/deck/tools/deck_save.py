"""deck.deck_save：演示文稿立项——deck.presentation workflow 的 brief 段 provider。

创建演示文稿工作对象（标题/受众/目标/页数）→ decks 表 + artifact deck.brief（ref=deck_id）。
后续 claims/storyline/compose/visual/validate/export_pptx 都以 deck_id 为锚点。
文件自包含（加载器按文件独立 importlib 加载，禁止跨文件 import）。
"""
import json
import os
import time
import uuid
from pathlib import Path

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.tools import Tool


class DeckSave(Tool):
    id = "deck.deck_save"
    label = "新建演示文稿"
    description = (
        "创建演示文稿工作对象：标题 + 受众 + 目标 + 页数。返回 deck_id，"
        "后续主张/故事线/页面/校验/导出都以它为锚点。"
    )
    default_risk = RiskLevel.L2_MEDIUM
    work_outputs = (
        {   # 演示简报 artifact：一 deck 一个，ref=deck_id
            "kind": "artifact",
            "artifact_type": "deck.brief",
            "ref_from": "data.deck_ref",
            "content_ref_from": "data.content_ref",
            "metadata_fields": ["data.audience", "data.goal", "data.page_count"],
        },
    )

    def __init__(self, data_dir: str):
        self._plugin_root = Path(data_dir)

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "演示标题"},
                    "audience": {"type": "string", "description": "受众（如：首次接触 Agent 的职场人）"},
                    "goal": {"type": "string", "description": "目标（如：讲清三种 AI 的分工边界）"},
                    "page_count": {"type": "integer", "description": "目标页数（缺省 12）"},
                },
                "required": ["title"],
            },
        }

    def run(self, params: dict, ctx) -> ActionResult:
        title = str(params.get("title") or "").strip()
        if not title:
            return ActionResult(success=False, error="title 不能为空")
        page_count = params.get("page_count")
        if page_count is None:
            page_count = 12
        if isinstance(page_count, bool) or not isinstance(page_count, int) or not 2 <= page_count <= 60:
            return ActionResult(success=False, error="page_count 必须是 2–60 的整数")
        blobs = getattr(ctx, "blobs", None)
        if blobs is None:
            return ActionResult(success=False, error="底座未提供 blobs capability")
        now = int(time.time())
        deck_id = uuid.uuid4().hex[:16]
        doc = {
            "id": deck_id,
            "title": title,
            "audience": str(params.get("audience") or "").strip(),
            "goal": str(params.get("goal") or "").strip(),
            "page_count": page_count,
            "created_at": now,
        }
        try:
            # promote 先于 PluginDb commit：崩溃最多留可 GC 的孤儿 blob
            staged = blobs.stage_text(json.dumps(doc, ensure_ascii=False, indent=2))
            content_ref = staged.finalize()
        except OSError as e:
            return ActionResult(success=False, error=f"写简报失败：{e}")
        ctx.db.insert("decks", {
            "id": deck_id, "title": title,
            "audience": doc["audience"], "goal": doc["goal"],
            "page_count": page_count, "created_at": now, "updated_at": now,
        })
        result = ActionResult(success=True, data={
            "deck_id": deck_id,
            "deck_ref": deck_id,
            "title": title,
            "audience": doc["audience"],
            "goal": doc["goal"],
            "page_count": page_count,
            "content_ref": content_ref,
        })
        return result


def make_tools(ctx):
    return [DeckSave(os.path.dirname(ctx.db.path))]
