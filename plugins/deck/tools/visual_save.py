"""deck.visual_save：视觉规范——deck.presentation workflow 的 visual 段 provider。

存演示的视觉规范（palette 调色板 / accent 强调色 / 字体梯度）；导出（export_pptx）
按它设色。落 blob + deck_stages(kind=visual) + artifact deck.visual_spec（ref=deck_id）
+ derived_from 边 → deck.document。
文件自包含（加载器按文件独立 importlib 加载，禁止跨文件 import）。
"""
import json
import os
import re
import time
from pathlib import Path

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.tools import Tool

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class VisualSave(Tool):
    id = "deck.visual_save"
    label = "存视觉规范"
    description = (
        "给演示文稿存视觉规范：底色（palette）与强调色（accent），导出 pptx 时按它设色。"
        "缺省深色底 + 天青强调。"
    )
    default_risk = RiskLevel.L1_LOW
    work_outputs = (
        {
            "kind": "artifact",
            "artifact_type": "deck.visual_spec",
            "ref_from": "data.visual_ref",
            "content_ref_from": "data.content_ref",
            "metadata_fields": ["data.version", "data.palette", "data.accent"],
        },
        {
            "kind": "edge",
            "relation": "derived_from",
            "source_artifact_type": "deck.visual_spec",
            "source_ref_from": "data.visual_ref",
            "target_artifact_type": "deck.document",
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
                    "palette": {"type": "string", "description": "底色（#RRGGBB，缺省 #111418 深色）"},
                    "accent": {"type": "string", "description": "强调色（#RRGGBB，缺省 #5EA0D2 天青）"},
                },
                "required": ["deck_id"],
            },
        }

    def run(self, params: dict, ctx) -> ActionResult:
        deck_id = str(params.get("deck_id") or "").strip()
        if not deck_id:
            return ActionResult(success=False, error="deck_id 不能为空")
        if not ctx.db.query("decks", where={"id": deck_id}):
            return ActionResult(success=False, error=f"演示文稿不存在：{deck_id}")
        palette = str(params.get("palette") or "#111418").strip()
        accent = str(params.get("accent") or "#5EA0D2").strip()
        for name, value in (("palette", palette), ("accent", accent)):
            if not _HEX_RE.match(value):
                return ActionResult(success=False, error=f"{name} 必须是 #RRGGBB 形式（当前：{value!r}）")
        blobs = getattr(ctx, "blobs", None)
        if blobs is None:
            return ActionResult(success=False, error="底座未提供 blobs capability")
        latest = ctx.db.query("deck_stages", where={"deck_id": deck_id, "kind": "visual"},
                              order="version DESC", limit=1)
        version = (int(latest[0]["version"]) if latest else 0) + 1
        now = int(time.time())
        doc = {"deck_id": deck_id, "version": version, "palette": palette, "accent": accent,
               "created_at": now}
        try:
            staged = blobs.stage_text(json.dumps(doc, ensure_ascii=False, indent=2))
            content_ref = staged.finalize()
        except OSError as e:
            return ActionResult(success=False, error=f"写视觉规范失败：{e}")
        ctx.db.insert("deck_stages", {
            "deck_id": deck_id, "kind": "visual", "version": version,
            "content_path": content_ref, "created_at": now,
        })
        return ActionResult(success=True, data={
            "deck_id": deck_id,
            "deck_ref": deck_id,
            "visual_ref": deck_id,
            "version": version,
            "palette": palette,
            "accent": accent,
            "content_ref": content_ref,
        })


def make_tools(ctx):
    return [VisualSave(os.path.dirname(ctx.db.path))]
