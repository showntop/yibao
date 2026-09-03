"""deck.storyline_save：故事线落库——deck.presentation workflow 的 storyline 段 provider。

故事线 = 有序段落数组（title + key_points[]）：把主张组织成叙事。落 blob +
deck_stages(kind=storyline) + artifact deck.storyline（ref=deck_id）+ derived_from 边
→ deck.claim_set。
文件自包含（加载器按文件独立 importlib 加载，禁止跨文件 import）。
"""
import json
import os
import time
from pathlib import Path

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.tools import Tool

_MAX_SECTIONS = 30


class StorylineSave(Tool):
    id = "deck.storyline_save"
    label = "存故事线"
    description = (
        "给演示文稿存故事线：有序段落（title + key_points[]），把主张组织成叙事顺序。"
        "故事线是页面组装（compose）的直接输入。"
    )
    default_risk = RiskLevel.L2_MEDIUM
    work_outputs = (
        {
            "kind": "artifact",
            "artifact_type": "deck.storyline",
            "ref_from": "data.storyline_ref",
            "content_ref_from": "data.content_ref",
            "metadata_fields": ["data.version", "data.section_count"],
        },
        {   # 故事线 derived_from 主张集
            "kind": "edge",
            "relation": "derived_from",
            "source_artifact_type": "deck.storyline",
            "source_ref_from": "data.storyline_ref",
            "target_artifact_type": "deck.claim_set",
            "target_ref_from": "data.deck_ref",
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
                    "deck_id": {"type": "string", "description": "演示文稿 id"},
                    "sections": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string", "description": "段落标题"},
                                "key_points": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "本段要点（每条一句）",
                                },
                            },
                            "required": ["title", "key_points"],
                        },
                        "description": "段落列表（也可传 JSON 数组字符串）",
                    },
                },
                "required": ["deck_id", "sections"],
            },
        }

    def run(self, params: dict, ctx) -> ActionResult:
        deck_id = str(params.get("deck_id") or "").strip()
        if not deck_id:
            return ActionResult(success=False, error="deck_id 不能为空")
        if not ctx.db.query("decks", where={"id": deck_id}):
            return ActionResult(success=False, error=f"演示文稿不存在：{deck_id}")
        sections, error = _normalize_sections(params.get("sections"))
        if error:
            return ActionResult(success=False, error=error)
        blobs = getattr(ctx, "blobs", None)
        if blobs is None:
            return ActionResult(success=False, error="底座未提供 blobs capability")
        latest = ctx.db.query("deck_stages", where={"deck_id": deck_id, "kind": "storyline"},
                              order="version DESC", limit=1)
        version = (int(latest[0]["version"]) if latest else 0) + 1
        now = int(time.time())
        doc = {"deck_id": deck_id, "version": version, "sections": sections, "created_at": now}
        try:
            staged = blobs.stage_text(json.dumps(doc, ensure_ascii=False, indent=2))
            content_ref = staged.finalize()
        except OSError as e:
            return ActionResult(success=False, error=f"写故事线失败：{e}")
        ctx.db.insert("deck_stages", {
            "deck_id": deck_id, "kind": "storyline", "version": version,
            "content_path": content_ref, "created_at": now,
        })
        return ActionResult(success=True, data={
            "deck_id": deck_id,
            "deck_ref": deck_id,
            "storyline_ref": deck_id,
            "version": version,
            "section_count": len(sections),
            "sections": sections,
            "content_ref": content_ref,
        })


def _normalize_sections(raw) -> tuple[list, str]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as e:
            return [], f"sections JSON 解析失败：{e}"
    if not isinstance(raw, list) or not raw:
        return [], "sections 必须是非空数组（[{title, key_points[]}]）"
    out = []
    for item in raw:
        if not isinstance(item, dict):
            return [], "sections 条目必须是对象（{title, key_points[]}）"
        title = str(item.get("title") or "").strip()
        points = item.get("key_points")
        if isinstance(points, str):
            points = [points]
        if not title or not isinstance(points, list) or not points:
            return [], "sections 条目的 title 不能为空，key_points 必须是非空数组"
        clean = [str(p).strip() for p in points if str(p).strip()]
        if not clean:
            return [], f"段落「{title}」的要点全为空"
        out.append({"title": title, "key_points": clean})
        if len(out) > _MAX_SECTIONS:
            return [], f"段落最多 {_MAX_SECTIONS} 段"
    return out, ""


def make_tools(ctx):
    return [StorylineSave(os.path.dirname(ctx.db.path))]
