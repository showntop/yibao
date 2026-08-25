"""zimeiti.mat_search：素材检索——关键词扫标题/摘要/标签/正文，支持 tag/topic 过滤组合。

A8（2026-08-25）：mat_list 只能按更新时间全量翻，素材多了找论据靠运气。
检索结果按更新时间倒序、不含正文（省 payload；取全文走 mat_get）。
文件自包含（加载器按文件独立 importlib 加载，禁止跨文件 import）。
"""
from __future__ import annotations

from typing import Any

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.tools import Tool

_MAX_ROWS = 20
_SCAN_LIMIT = 500  # 全表扫描上限（素材库是百级规模，内存过滤够用）
_LIGHT_FIELDS = ("id", "title", "url", "kind", "summary", "tags", "topic_id", "updated_at")


class MatSearch(Tool):
    id = "zimeiti.mat_search"
    label = "检索素材"
    description = (
        "检索素材库：q 关键词扫标题/摘要/标签/正文（包含匹配，大小写不敏感），tag 按标签精确过滤，"
        "topic_id 看某选题的关联素材；三者可组合，至少给一个。结果不含正文（取全文用 mat_get）。"
        "写稿前找论据用它，比 mat_list 全量翻高效。"
    )
    default_risk = RiskLevel.L0_READONLY

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "q": {"type": "string", "description": "关键词（扫标题/摘要/标签/正文）"},
                    "tag": {"type": "string", "description": "标签精确过滤（如 AI）"},
                    "topic_id": {"type": "string", "description": "只看某选题关联的素材"},
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        db = getattr(ctx, "db", None)
        if db is None:
            return ActionResult(success=False, error="底座未提供数据库")
        q = str(params.get("q") or "").strip().lower()
        tag = str(params.get("tag") or "").strip().lower()
        topic_id = str(params.get("topic_id") or "").strip()
        if not q and not tag and not topic_id:
            return ActionResult(success=False, error="q / tag / topic_id 至少给一个")
        out: list[dict] = []
        for r in db.query("materials", order="updated_at DESC", limit=_SCAN_LIMIT):
            if topic_id and str(r.get("topic_id") or "") != topic_id:
                continue
            if tag and tag not in [t.strip().lower() for t in str(r.get("tags") or "").split(",")]:
                continue
            if q:
                hay = " ".join(str(r.get(k) or "") for k in ("title", "summary", "tags", "content")).lower()
                if q not in hay:
                    continue
            out.append({k: r.get(k) for k in _LIGHT_FIELDS})
            if len(out) >= _MAX_ROWS:
                break
        return ActionResult(success=True, data={"rows": out, "total": len(out)})


def make_tools(ctx: Any) -> list[Tool]:
    return [MatSearch()]
