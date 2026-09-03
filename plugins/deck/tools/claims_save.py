"""deck.claims_save：主张/论据落库——deck.presentation workflow 的 claims 段 provider。

每条主张带支撑来源（claim + support + 可选 source_uri）；落 blob + deck_stages(kind=claims)
+ artifact deck.claim_set（ref=deck_id）+ derived_from 边 → deck.brief。
文件自包含（加载器按文件独立 importlib 加载，禁止跨文件 import）。
"""
import json
import os
import time
from pathlib import Path

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.tools import Tool

_MAX_CLAIMS = 30


class ClaimsSave(Tool):
    id = "deck.claims_save"
    label = "存主张论据"
    description = (
        "给演示文稿存主张与论据：每条 claim 带支撑说明（support）与可选来源链接。"
        "主张是故事线的上游输入——先把「要讲什么道理」钉住，再谈怎么讲。"
    )
    default_risk = RiskLevel.L2_MEDIUM
    work_outputs = (
        {
            "kind": "artifact",
            "artifact_type": "deck.claim_set",
            "ref_from": "data.claims_ref",
            "content_ref_from": "data.content_ref",
            "metadata_fields": ["data.version", "data.claim_count"],
        },
        {   # 主张 derived_from 简报
            "kind": "edge",
            "relation": "derived_from",
            "source_artifact_type": "deck.claim_set",
            "source_ref_from": "data.claims_ref",
            "target_artifact_type": "deck.brief",
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
                    "claims": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "claim": {"type": "string", "description": "主张（一句话观点）"},
                                "support": {"type": "string", "description": "支撑说明/论据"},
                                "source_uri": {"type": "string", "description": "来源链接（可选）"},
                            },
                            "required": ["claim", "support"],
                        },
                        "description": "主张列表（也可传 JSON 数组字符串）",
                    },
                },
                "required": ["deck_id", "claims"],
            },
        }

    def run(self, params: dict, ctx) -> ActionResult:
        deck_id = str(params.get("deck_id") or "").strip()
        if not deck_id:
            return ActionResult(success=False, error="deck_id 不能为空")
        if not ctx.db.query("decks", where={"id": deck_id}):
            return ActionResult(success=False, error=f"演示文稿不存在：{deck_id}")
        claims, error = _normalize_claims(params.get("claims"))
        if error:
            return ActionResult(success=False, error=error)
        blobs = getattr(ctx, "blobs", None)
        if blobs is None:
            return ActionResult(success=False, error="底座未提供 blobs capability")
        latest = ctx.db.query("deck_stages", where={"deck_id": deck_id, "kind": "claims"},
                              order="version DESC", limit=1)
        version = (int(latest[0]["version"]) if latest else 0) + 1
        now = int(time.time())
        doc = {"deck_id": deck_id, "version": version, "claims": claims, "created_at": now}
        try:
            staged = blobs.stage_text(json.dumps(doc, ensure_ascii=False, indent=2))
            content_ref = staged.finalize()
        except OSError as e:
            return ActionResult(success=False, error=f"写主张失败：{e}")
        ctx.db.insert("deck_stages", {
            "deck_id": deck_id, "kind": "claims", "version": version,
            "content_path": content_ref, "created_at": now,
        })
        return ActionResult(success=True, data={
            "deck_id": deck_id,
            "deck_ref": deck_id,
            "claims_ref": deck_id,
            "version": version,
            "claim_count": len(claims),
            "claims": claims,
            "content_ref": content_ref,
        })


def _normalize_claims(raw) -> tuple[list, str]:
    """claims 参数 → 规范列表；可传 JSON 数组字符串。返回 (claims, error)。"""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as e:
            return [], f"claims JSON 解析失败：{e}"
    if not isinstance(raw, list) or not raw:
        return [], "claims 必须是非空数组（[{claim, support}]）"
    out = []
    for item in raw:
        if not isinstance(item, dict):
            return [], "claims 条目必须是对象（{claim, support}）"
        claim = str(item.get("claim") or "").strip()
        support = str(item.get("support") or "").strip()
        if not claim or not support:
            return [], "claims 条目的 claim 与 support 都不能为空"
        out.append({
            "claim": claim,
            "support": support,
            "source_uri": str(item.get("source_uri") or "").strip(),
        })
        if len(out) > _MAX_CLAIMS:
            return [], f"主张最多 {_MAX_CLAIMS} 条"
    return out, ""


def make_tools(ctx):
    return [ClaimsSave(os.path.dirname(ctx.db.path))]
