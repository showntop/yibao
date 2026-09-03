"""deck.compose：页面组装——deck.presentation workflow 的 slides 段 provider。

读最新故事线 → 组装成页面文档（deck.document）：封面页 + 每段一页内容页 + 结尾页，
每页带标题/要点/演讲备注（备注从主张里按段索引取）。落 blob + deck_stages(kind=document)
+ artifact deck.document（ref=deck_id）+ derived_from 边 → deck.storyline。
文件自包含（加载器按文件独立 importlib 加载，禁止跨文件 import）。
"""
import json
import os
import time
from pathlib import Path

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.tools import Tool


class Compose(Tool):
    id = "deck.compose"
    label = "组装页面"
    description = (
        "按最新故事线组装演示页面文档：封面 + 每段一页内容 + 结尾。"
        "每页带标题、要点与演讲备注；供校验（validate）与导出（export_pptx）消费。"
    )
    default_risk = RiskLevel.L2_MEDIUM
    work_outputs = (
        {
            "kind": "artifact",
            "artifact_type": "deck.document",
            "ref_from": "data.document_ref",
            "content_ref_from": "data.content_ref",
            "metadata_fields": ["data.version", "data.slide_count"],
        },
        {
            "kind": "edge",
            "relation": "derived_from",
            "source_artifact_type": "deck.document",
            "source_ref_from": "data.document_ref",
            "target_artifact_type": "deck.storyline",
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
                },
                "required": ["deck_id"],
            },
        }

    def run(self, params: dict, ctx) -> ActionResult:
        deck_id = str(params.get("deck_id") or "").strip()
        if not deck_id:
            return ActionResult(success=False, error="deck_id 不能为空")
        deck_rows = ctx.db.query("decks", where={"id": deck_id})
        if not deck_rows:
            return ActionResult(success=False, error=f"演示文稿不存在：{deck_id}")
        storyline, error = _load_latest(ctx, deck_id, "storyline")
        if error:
            return ActionResult(success=False, error=error)
        claims, _ = _load_latest(ctx, deck_id, "claims")  # 备注素材：没有就空
        doc = _build_document(deck_rows[0], storyline, claims)
        blobs = getattr(ctx, "blobs", None)
        if blobs is None:
            return ActionResult(success=False, error="底座未提供 blobs capability")
        latest = ctx.db.query("deck_stages", where={"deck_id": deck_id, "kind": "document"},
                              order="version DESC", limit=1)
        version = (int(latest[0]["version"]) if latest else 0) + 1
        doc["version"] = version
        now = int(time.time())
        doc["created_at"] = now
        try:
            staged = blobs.stage_text(json.dumps(doc, ensure_ascii=False, indent=2))
            content_ref = staged.finalize()
        except OSError as e:
            return ActionResult(success=False, error=f"写页面文档失败：{e}")
        ctx.db.insert("deck_stages", {
            "deck_id": deck_id, "kind": "document", "version": version,
            "content_path": content_ref, "created_at": now,
        })
        return ActionResult(success=True, data={
            "deck_id": deck_id,
            "deck_ref": deck_id,
            "document_ref": deck_id,
            "version": version,
            "slide_count": len(doc["slides"]),
            "content_ref": content_ref,
        })


def _load_latest(ctx, deck_id: str, kind: str) -> tuple[dict, str]:
    """读 deck_stages 里该 kind 的最新版内容（blob 解析 JSON）。返回 (doc, error)。"""
    rows = ctx.db.query("deck_stages", where={"deck_id": deck_id, "kind": kind},
                        order="version DESC", limit=1)
    if not rows:
        return {}, f"还没有{_kind_label(kind)}（先存 {_kind_label(kind)}）"
    raw = str(rows[0]["content_path"])
    blobs = getattr(ctx, "blobs", None)
    if blobs is None:
        return {}, "底座未提供 blobs capability"
    try:
        path = blobs.resolve(raw, require_exists=False)
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {}, f"{_kind_label(kind)}读取失败：{e}"
    return doc, ""


def _kind_label(kind: str) -> str:
    return {"claims": "主张", "storyline": "故事线", "document": "页面文档"}.get(kind, kind)


def _build_document(deck: dict, storyline: dict, claims: dict) -> dict:
    """故事线 → 页面文档：封面 + 每段一页内容 + 结尾；备注按段取对应主张的支撑说明。"""
    sections = storyline.get("sections") or []
    claim_list = (claims or {}).get("claims") or []
    slides: list[dict] = [{
        "kind": "title",
        "title": str(deck["title"]),
        "subtitle": "、".join(p for p in (str(deck.get("audience") or ""), str(deck.get("goal") or "")) if p),
        "bullets": [],
        "note": "开场：一句话说清这场演示讲什么、对谁讲。",
    }]
    for i, section in enumerate(sections):
        claim = claim_list[i] if i < len(claim_list) else None
        note = f"本段主张：{claim['claim']}（{claim['support']}）" if claim else ""
        slides.append({
            "kind": "content",
            "title": str(section.get("title") or f"第 {i + 1} 段"),
            "bullets": [str(p) for p in (section.get("key_points") or [])],
            "note": note,
        })
    slides.append({
        "kind": "end",
        "title": "谢谢",
        "bullets": [],
        "note": "收口：重复核心主张，留一个问题给观众。",
    })
    return {
        "deck_id": str(deck["id"]),
        "title": str(deck["title"]),
        "slides": slides,
    }


def make_tools(ctx):
    return [Compose(os.path.dirname(ctx.db.path))]
