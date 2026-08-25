"""zimeiti.hot_topics：热点雷达——拉取多平台热榜（知乎/头条/百度），归一化成结构化热点列表。

免登录公开端点 + urllib（与 mat_save 同套抓取姿势，不引第三方库）。平台级失败隔离：
单平台挂了/结构变了不拖垮整体，failed 如实上报；全挂才算失败。结果面板化（zimeiti:hot），
面板「转选题」走 zimeiti.hot_add（api.toml 映射 zimeiti.add）。文件自包含（禁止跨文件 import）。
2026-08-25 v2：多平台并行抓取（最坏耗时从串行 30s+ 收敛到单平台超时 10s）+ 10 分钟缓存
（转选题/刷新跟单不再重打网络）；抓取失败回退陈缓存，不把一个平台整列抹掉。
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.tools import Tool

_FETCH_TIMEOUT = 10
_MAX_LIMIT = 20
_CACHE_TTL = 600  # 热点 10 分钟缓存
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

# 平台 → (抓取时间, 解析好的条目，按 _MAX_LIMIT 截断)。模块级：同一大脑进程内共享。
_CACHE: dict[str, tuple[float, list[dict]]] = {}


def _fetch_json(url: str) -> dict:
    """GET 一个 JSON 端点（module-level，测试 monkeypatch 它，不真发网络）。"""
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return json.loads(resp.read().decode(charset, errors="replace"))


def _fmt_heat(raw: Any) -> str:
    """热度归一化：数字转「x万」中文读法；已是文本的（知乎「320 万热度」）原样保留。"""
    s = str(raw or "").strip()
    if not s:
        return ""
    try:
        n = float(s)
    except ValueError:
        return s
    if n >= 10000:
        return f"{n / 10000:.1f}".rstrip("0").rstrip(".") + "万热度"
    return f"{int(n)}热度"


def _parse_zhihu(data: dict, limit: int) -> list[dict]:
    """知乎热榜：新结构 title_area/metrics_area/link，老结构 target.title/detail_text，宽容两吃。"""
    out: list[dict] = []
    for item in data.get("data") or []:
        t = item.get("target") or {}
        title = str((t.get("title_area") or {}).get("text") or t.get("title") or "").strip()
        if not title:
            continue
        heat = str((t.get("metrics_area") or {}).get("text") or item.get("detail_text") or "").strip()
        url = str((t.get("link") or {}).get("url") or "").strip()
        if not url:
            m = re.search(r"questions/(\d+)", str(t.get("url") or ""))
            url = f"https://www.zhihu.com/question/{m.group(1)}" if m else ""
        out.append({"title": title, "heat": heat, "url": url})
        if len(out) >= limit:
            break
    return out


def _parse_toutiao(data: dict, limit: int) -> list[dict]:
    out: list[dict] = []
    for item in data.get("data") or []:
        title = str(item.get("Title") or "").strip()
        if not title:
            continue
        out.append({
            "title": title,
            "heat": _fmt_heat(item.get("HotValue")),
            "url": str(item.get("Url") or "").strip(),
        })
        if len(out) >= limit:
            break
    return out


def _parse_baidu(data: dict, limit: int) -> list[dict]:
    out: list[dict] = []
    cards = ((data.get("data") or {}).get("cards") or [])
    for item in (cards[0].get("content") if cards else []) or []:
        title = str(item.get("word") or "").strip()
        if not title:
            continue
        out.append({
            "title": title,
            "heat": _fmt_heat(item.get("hotScore")),
            "url": str(item.get("url") or "").strip(),
        })
        if len(out) >= limit:
            break
    return out


# 平台 id → (中文名, 端点, 解析器)。端点都是免登录公开 JSON；微博需登录态 cookie，先不接
_PLATFORMS: dict[str, tuple[str, str, Callable[[dict, int], list[dict]]]] = {
    "zhihu": ("知乎", "https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total?limit=20", _parse_zhihu),
    "toutiao": ("头条", "https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc", _parse_toutiao),
    "baidu": ("百度", "https://top.baidu.com/api/board?platform=wise&tab=realtime", _parse_baidu),
}


def _platform_items(name: str) -> list[dict]:
    """取一个平台的热榜条目（上限 _MAX_LIMIT）：鲜缓存直用；过期/没有就抓并写缓存；
    抓挂了回退陈缓存，连陈缓存都没有返回 []（上层记 failed）。"""
    _, url, parser = _PLATFORMS[name]
    now = time.time()
    hit = _CACHE.get(name)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    try:
        items = parser(_fetch_json(url), _MAX_LIMIT)
    except Exception:
        items = []
    if items:
        _CACHE[name] = (now, items)
        return items
    return hit[1] if hit else []


class HotTopicsTool(Tool):
    id = "zimeiti.hot_topics"
    label = "查热点选题"
    description = (
        "拉取多平台热榜（知乎/头条/百度），返回结构化热点（标题/热度/链接，热点雷达面板展示）。"
        "用户说「看看最近热点」「有什么热点可写」「找选题灵感」时用它；挑中后用 zimeiti.add 转成选题"
        "（source 填热点来源，如「知乎热榜#3」）。"
    )
    default_risk = RiskLevel.L1_LOW

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "platforms": {
                        "type": "string",
                        "description": "平台，逗号分隔（zhihu/toutiao/baidu，默认全拉）",
                    },
                    "limit": {"type": "integer", "description": "每平台条数（默认 10，上限 20）"},
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        try:
            limit = int(params.get("limit") or 10)
        except (TypeError, ValueError):
            limit = 10
        limit = max(1, min(limit, _MAX_LIMIT))
        names = [n.strip() for n in str(params.get("platforms") or "").split(",") if n.strip()]
        names = names or list(_PLATFORMS)
        unknown = [n for n in names if n not in _PLATFORMS]
        if unknown:
            return ActionResult(
                success=False,
                error=f"不认识的平台：{'、'.join(unknown)}（可选 {'/'.join(_PLATFORMS)}）",
            )
        rows: list[dict] = []
        failed: list[str] = []
        # 多平台并行：顺序保持 names 声明序（pool.map 按输入序返回）
        with ThreadPoolExecutor(max_workers=len(names)) as pool:
            per_platform = list(pool.map(_platform_items, names))
        for name, items in zip(names, per_platform):
            cn = _PLATFORMS[name][0]
            if not items:  # 网络挂了和「拿到响应但一条都解析不出（结构变了）」同等对待：如实报失败
                failed.append(cn)
                continue
            for i, it in enumerate(items[:limit], 1):
                rows.append({
                    "rank": i,
                    "platform": name,
                    "title": it["title"],
                    "heat": it["heat"],
                    "url": it["url"],
                    "meta": f"{cn} #{i}" + (f" · {it['heat']}" if it["heat"] else ""),
                    "source_ref": f"{cn}热榜#{i}",
                })
        if not rows:
            return ActionResult(
                success=False,
                error=f"热榜拉取失败（{'、'.join(failed)}）：网络不通或平台改版，稍后重试",
            )
        result = ActionResult(success=True, data={"rows": rows, "failed": failed})
        result.panel = "zimeiti:hot"
        return result


def make_tools(ctx: Any) -> list[Tool]:
    return [HotTopicsTool()]
