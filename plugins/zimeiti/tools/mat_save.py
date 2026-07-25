"""zimeiti.mat_save：存素材——链接抓正文 / 直接收文本，LLM 起标题、摘要、标签后落库。

文件自包含（加载器按文件独立 importlib 加载，禁止跨文件 import）。
抓网页用标准库 urllib + 正则粗提取（不引 readability 依赖）；正文截断入库，
写作时 mat_list 挑素材、mat_get 取全文当论据。
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from typing import Any

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.skills import Skill

_MAX_FETCH_BYTES = 2_000_000  # 网页下载上限（防超大页拖死）
_MAX_CONTENT = 8000  # 入库正文截断
_MAX_FOR_LLM = 12000  # 喂 LLM 的截取（摘要质量够用的上限）
_FETCH_TIMEOUT = 15

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def _fetch_text(url: str) -> str:
    """抓网页并粗提取可读文本：去 script/style、剥标签、压空白。"""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
        raw = resp.read(_MAX_FETCH_BYTES)
        charset = resp.headers.get_content_charset() or "utf-8"
    html = raw.decode(charset, errors="replace")
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


def _summarize(llm: Any, text: str) -> dict:
    """LLM 产出 {title, summary, tags}；解析失败给保底（不阻断存素材）。"""
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
    # 保底：LLM 输出不守约定也能存（标题取首句，摘要取开头）
    head = text.strip().split("\n", 1)[0][:20] or "未命名素材"
    return {"title": head, "summary": text[:200], "tags": []}


class MatSave(Skill):
    id = "zimeiti.mat_save"
    description = (
        "存一条写作素材：传 url 抓网页正文，或传 text 直接存（灵感/摘抄/数据）。"
        "自动起标题、写摘要、打标签后进素材库（mat_list 可见）。用户发来链接说「存一下/收藏」就用它。"
    )
    default_risk = RiskLevel.L1_LOW

    def __init__(self) -> None:
        self.refresh = "zimeiti.mat_list"  # 存完面板拿刷新后的素材列表

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "网页链接（与 text 二选一，优先 url）"},
                    "text": {"type": "string", "description": "直接存的文本内容（无 url 时必填）"},
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        url = str(params.get("url") or "").strip()
        text = str(params.get("text") or "").strip()
        if not url and not text:
            return ActionResult(success=False, error="url 和 text 至少给一个")
        kind = "link" if url else "note"
        if url:
            if not re.match(r"^https?://", url):
                return ActionResult(success=False, error=f"不是合法 http(s) 链接：{url}")
            try:
                text = _fetch_text(url)
            except Exception as e:
                return ActionResult(success=False, error=f"抓取失败：{e}")
            if not text:
                return ActionResult(success=False, error="抓到了页面但没提取出文字内容")
        llm = getattr(ctx, "llm", None)
        if llm is None:
            return ActionResult(success=False, error="底座未提供 LLM 能力")
        try:
            meta = _summarize(llm, text)
        except Exception as e:
            return ActionResult(success=False, error=f"摘要生成失败：{e}")

        now = int(time.time())
        row = {
            "title": meta["title"],
            "url": url,
            "kind": kind,
            "summary": meta["summary"],
            "tags": ",".join(meta["tags"]),
            "content": text[:_MAX_CONTENT],
            "created_at": now,
            "updated_at": now,
        }
        rid = ctx.db.insert("materials", row)
        result = ActionResult(success=True, data={
            "id": rid, "title": meta["title"], "summary": meta["summary"], "tags": meta["tags"],
        })
        result.panel = "zimeiti:materials"
        return result


def make_tools(ctx: Any) -> list[Skill]:
    return [MatSave()]
