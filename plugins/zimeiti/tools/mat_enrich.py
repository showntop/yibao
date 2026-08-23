"""zimeiti.mat_enrich：素材后台精整——按 id 读正文，LLM 起标题/摘要/标签后更新行。

配合 mat_save 的 defer 模式（先存后整理）：浏览器扩展/唤起条秒回落库，本工具异步补元数据。
失败静默（调用方是后台任务）：素材本体已在库，元数据保持即席值也可用。
文件自包含（加载器按文件独立 importlib 加载，禁止跨文件 import——_summarize 与 mat_save 同款复制）。
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.tools import Tool

_MAX_FOR_LLM = 12000  # 喂 LLM 的截取（摘要质量够用的上限）


def _summarize(llm: Any, text: str) -> dict:
    """LLM 产出 {title, summary, tags}；解析失败给保底（不阻断精整）。"""
    prompt = (
        "你是素材整理助手。给下面的素材文本做归档整理，只输出 JSON，不要任何其它内容：\n"
        '{"title": "不超过 20 字的标题", "summary": "3-5 句中文摘要，说清核心观点/数据/结论", "tags": ["3-6 个中文标签"]}\n\n'
        f"素材文本：\n{text[:_MAX_FOR_LLM]}"
    )
    out = llm.chat(prompt)
    t = out.strip()
    if t.startswith("```"):  # 剥代码围栏
        t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", t).strip()
    i, j = t.find("{"), t.rfind("}")
    if i >= 0 and j > i:
        try:
            meta = json.loads(t[i : j + 1])
            title = str(meta.get("title") or "").strip()
            summary = str(meta.get("summary") or "").strip()
            tags = meta.get("tags")
            tags = [str(x).strip() for x in tags if str(x).strip()] if isinstance(tags, list) else []
            if title and summary:
                return {"title": title, "summary": summary, "tags": tags}
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass
    # 保底：LLM 输出不守约定也能更新（标题取首句，摘要取开头）
    head = text.strip().split("\n", 1)[0][:20] or "未命名素材"
    return {"title": head, "summary": text[:200], "tags": []}


class MatEnrich(Tool):
    id = "zimeiti.mat_enrich"
    label = "素材精整"
    description = (
        "素材后台精整：按 id 读 materials 表正文，LLM 重起标题、写摘要、打标签后更新该行。"
        "配合 mat_save defer（先存后整理）由底座后台调用，一般不需要用户直接触发。"
    )
    default_risk = RiskLevel.L1_LOW

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "素材行 id（mat_save 落库返回的）"},
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        mid = str(params.get("id") or "").strip()
        if not mid:
            return ActionResult(success=False, error="缺素材 id")
        rows = ctx.db.query("materials", where={"id": mid}, limit=1)
        if not rows:
            return ActionResult(success=False, error=f"素材不存在：{mid}")
        content = str(rows[0].get("content") or "")
        if not content:
            return ActionResult(success=False, error="素材正文为空")
        llm = getattr(ctx, "llm", None)
        if llm is None:
            return ActionResult(success=False, error="底座未提供 LLM 能力")
        try:
            meta = _summarize(llm, content)
        except Exception as e:
            return ActionResult(success=False, error=f"摘要生成失败：{e}")
        ctx.db.update("materials", mid, {
            "title": meta["title"],
            "summary": meta["summary"],
            "tags": ",".join(meta["tags"]),
            "updated_at": int(time.time()),
        })
        return ActionResult(success=True, data={"id": mid, "title": meta["title"]})


def make_tools(ctx: Any) -> list[Tool]:
    return [MatEnrich()]
