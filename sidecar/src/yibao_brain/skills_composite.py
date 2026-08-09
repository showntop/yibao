"""复合技能：把原子能力编排成用户看得懂的场景（找文件/搜索/打开/写东西/读网页）。

实现原则：确定性优先 —— 能走 CLI（open/mdfind）就不点像素，能走 AX 设值就不模拟键入。
find_file/web_search/open_path/extract_url 不依赖 host（直接 subprocess / urllib）；
write_note 依赖 host（AX/键入）。

web_search 走可配置 provider（settings 即时生效）：
- browser（默认）：用系统浏览器打开搜索引擎结果页，交给人看（保留原行为）
- ddg：DuckDuckGo HTML 端点，免费免 key，返回结构化结果
- searxng：自建元搜索实例（search.searxng_url），返回 JSON 结果
- brave / tavily / serper：商用 Search API（key 走 .env：YIBAO_SEARCH_*_KEY）
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .config import search_api_key, search_engine, search_provider, search_searxng_url
from .ipc import ActionResult, RiskLevel
from .skills import Skill, SkillContext, SkillRegistry

_SEARCH_ENGINES = {
    "baidu": "https://www.baidu.com/s?wd=",
    "bing": "https://www.bing.com/search?q=",
    "google": "https://www.google.com/search?q=",
}

_MAX_FETCH_BYTES = 2_000_000  # 网页下载上限（防超大页拖死）
_MAX_RESULTS = 8              # 搜索结果条数上限
_FETCH_TIMEOUT = 10           # 网络请求超时（秒）
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def _run_argv(argv: list[str], timeout: int = 10) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)


# ---- 网络请求基础 ----

def _http_get(url: str, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
        return resp.read(_MAX_FETCH_BYTES)


def _http_post(url: str, payload: bytes, headers: dict) -> bytes:
    req = urllib.request.Request(
        url, data=payload, headers={"User-Agent": _UA, **headers}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
        return resp.read(_MAX_FETCH_BYTES)


def _browser_url(query: str, engine: str | None = None) -> str:
    """浏览器模式的结果页 URL（engine: baidu/bing/google）。"""
    eng = (engine or search_engine()).lower()
    base = _SEARCH_ENGINES.get(eng, _SEARCH_ENGINES["baidu"])
    return base + urllib.parse.quote(query)


# ---- 正文抓取（extract_url 用）----

def _fetch_url_text(url: str) -> tuple[str, str]:
    """抓网页并粗提取可读正文：返回 (标题, 正文文本)。"""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
        raw = resp.read(_MAX_FETCH_BYTES)
        charset = resp.headers.get_content_charset() or "utf-8"
    src = raw.decode(charset, errors="replace")
    m = re.search(r"<title[^>]*>(.*?)</title>", src, re.I | re.S)
    title = " ".join(m.group(1).split()) if m else ""
    body = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", src)
    body = re.sub(r"(?s)<[^>]+>", " ", body)
    body = re.sub(r"\s+", " ", body).strip()
    if title and body.startswith(title):
        body = body[len(title):].strip()
    return title[:200], body


# ---- 搜索 provider 适配 ----

def _uddg_target(href: str) -> str:
    """DuckDuckGo 结果链接是 /l/?uddg=<真实URL> 的重定向，取回真实目标。"""
    m = re.search(r"[?&]uddg=([^&]+)", href)
    if m:
        return urllib.parse.unquote(m.group(1))
    if href.startswith("//"):
        return "https:" + href
    return href


class _DDGResultParser(HTMLParser):
    """解析 html.duckduckgo.com/html 结果页：result__a 标题 + result__snippet 摘要。"""

    _SNIPPET_END = ("a", "td", "div")

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict] = []
        self._cur: dict | None = None
        self._mode: str | None = None  # "title" | "snippet"
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        cls = dict(attrs).get("class", "") or ""
        if tag == "a" and "result__a" in cls:
            self._flush()
            href = dict(attrs).get("href", "")
            self._cur = {"title": "", "url": _uddg_target(href), "snippet": ""}
            self._mode = "title"
            self._buf = []
        elif tag in ("a", "td", "div") and "result__snippet" in cls and self._cur is not None:
            self._flush()
            self._mode = "snippet"
            self._buf = []

    def handle_data(self, data: str) -> None:
        if self._cur is not None and self._mode is not None:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._mode == "title" and tag == "a":
            self._flush()
        elif self._mode == "snippet" and tag in self._SNIPPET_END:
            self._flush()

    def _flush(self) -> None:
        """收尾当前字段：标题直接落；摘要后整条入列。"""
        if self._cur is None or self._mode is None:
            self._mode = None
            return
        text = " ".join("".join(self._buf).split())
        if self._mode == "title":
            self._cur["title"] = text
        else:
            self._cur["snippet"] = text
            if self._cur.get("title") and self._cur.get("url"):
                self.results.append(self._cur)
            self._cur = None
        self._mode = None
        self._buf = []


def _search_ddg(query: str) -> list[dict]:
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    raw = _http_get(url)
    parser = _DDGResultParser()
    parser.feed(raw.decode("utf-8", errors="replace"))
    return parser.results[:_MAX_RESULTS]


def _search_searxng(query: str, base: str) -> list[dict]:
    base = base.rstrip("/")
    url = f"{base}/search?q={urllib.parse.quote(query)}&format=json"
    raw = _http_get(url, headers={"Accept": "application/json"})
    data = json.loads(raw.decode("utf-8", errors="replace"))
    out = []
    for r in data.get("results", []):
        title = str(r.get("title") or "").strip()
        link = str(r.get("url") or "").strip()
        if title and link:
            out.append({"title": title, "url": link,
                        "snippet": str(r.get("content") or "").strip()})
    return out[:_MAX_RESULTS]


def _search_api(provider: str, query: str, key: str) -> list[dict]:
    """商用 Search API 统一适配：brave（GET）/ tavily（POST）/ serper（POST）。"""
    if provider == "brave":
        url = "https://api.search.brave.com/res/v1/web/search?q=" + urllib.parse.quote(query)
        raw = _http_get(url, headers={"X-Subscription-Token": key})
        data = json.loads(raw.decode("utf-8", errors="replace"))
        items = data.get("web", {}).get("results", [])
        out = [
            {"title": str(i.get("title") or "").strip(),
             "url": str(i.get("url") or "").strip(),
             "snippet": str(i.get("description") or "").strip()}
            for i in items if i.get("title") and i.get("url")
        ]
        return out[:_MAX_RESULTS]

    if provider == "tavily":
        payload = json.dumps({"api_key": key, "query": query, "max_results": _MAX_RESULTS}).encode()
        raw = _http_post("https://api.tavily.com/search", payload, {"Content-Type": "application/json"})
        data = json.loads(raw.decode("utf-8", errors="replace"))
        items = data.get("results", [])
        return [
            {"title": str(i.get("title") or "").strip(),
             "url": str(i.get("url") or "").strip(),
             "snippet": str(i.get("content") or "").strip()}
            for i in items if i.get("title") and i.get("url")
        ][:_MAX_RESULTS]

    if provider == "serper":
        payload = json.dumps({"q": query}).encode()
        raw = _http_post("https://google.serper.dev/search", payload,
                         {"X-API-KEY": key, "Content-Type": "application/json"})
        data = json.loads(raw.decode("utf-8", errors="replace"))
        items = data.get("organic", [])
        return [
            {"title": str(i.get("title") or "").strip(),
             "url": str(i.get("link") or "").strip(),
             "snippet": str(i.get("snippet") or "").strip()}
            for i in items if i.get("title") and i.get("link")
        ][:_MAX_RESULTS]

    return []


class FindFileSkill(Skill):
    id = "find_file"
    label = "找文件"
    description = "在本机全盘搜索文件（Spotlight），按文件名或内容关键词匹配，返回最相关的前 10 个路径。配合 open_path 打开结果。"
    default_risk = RiskLevel.L0_READONLY

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "文件名或内容关键词"}},
                "required": ["query"],
            },
        }

    def run(self, params: dict, ctx: SkillContext) -> ActionResult:
        query = str(params.get("query", "")).strip()
        if not query:
            return ActionResult(success=False, error="缺少 query 参数")
        try:
            cp = _run_argv(["mdfind", query])
        except Exception as e:
            return ActionResult(success=False, error=f"mdfind 失败：{e}")
        paths = [p.strip() for p in (cp.stdout or "").splitlines() if p.strip()][:10]
        return ActionResult(success=True, data={"paths": paths, "count": len(paths)})


class WebSearchSkill(Skill):
    id = "web_search"
    label = "联网搜索"
    description = (
        "联网搜索，返回结构化结果（标题/链接/摘要，最多 8 条）。搜索通道可在设置里配置："
        "browser=打开浏览器给人看；ddg=DuckDuckGo 免费免 key；searxng=自建实例；brave/tavily/serper=API。"
        "搜到链接后想读正文，用 extract_url 抓取。"
    )
    default_risk = RiskLevel.L1_LOW

    def __init__(self, engine: str | None = None, provider: str | None = None,
                 searxng_url: str | None = None):
        self._engine = engine          # browser 模式引擎（None → 运行时读 config）
        self._provider = provider      # 搜索通道（None → 运行时读 config/settings）
        self._searxng_url = searxng_url

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "搜索关键词"}},
                "required": ["query"],
            },
        }

    def run(self, params: dict, ctx: SkillContext) -> ActionResult:
        query = str(params.get("query", "")).strip()
        if not query:
            return ActionResult(success=False, error="缺少 query 参数")
        # 显式指定 engine（旧 API 语义）→ 强制浏览器模式，不读全局 provider 配置
        if self._engine:
            provider = "browser"
        else:
            provider = self._provider or search_provider()
        if provider == "browser":
            return self._browser(query)
        try:
            if provider == "ddg":
                results = _search_ddg(query)
            elif provider == "searxng":
                base = self._searxng_url or search_searxng_url()
                if not base:
                    return ActionResult(
                        success=False,
                        error="未配置 SearXNG 实例地址：请在设置「搜索」里填写实例 URL（search.searxng_url）",
                    )
                results = _search_searxng(query, base)
            elif provider in ("brave", "tavily", "serper"):
                key = search_api_key(provider)
                if not key:
                    return ActionResult(
                        success=False,
                        error=f"未配置 {provider} API key：请设置环境变量 YIBAO_SEARCH_{provider.upper()}_KEY",
                    )
                results = _search_api(provider, query, key)
            else:
                return ActionResult(success=False, error=f"未知搜索通道：{provider}")
        except Exception as e:
            return ActionResult(success=False, error=f"{provider} 搜索失败：{e}")
        return ActionResult(success=True, data={
            "provider": provider,
            "query": query,
            "results": results,
            "browser_url": _browser_url(query),  # 供用户想点开结果页时参考
        })

    def _browser(self, query: str) -> ActionResult:
        engine = self._engine or search_engine()
        url = _browser_url(query, engine)
        try:
            _run_argv(["open", url])
        except Exception as e:
            return ActionResult(success=False, error=f"打开浏览器失败：{e}")
        return ActionResult(success=True, data={
            "provider": "browser", "engine": engine, "query": query,
            "results": [], "browser_url": url,
        })


class ExtractUrlSkill(Skill):
    id = "extract_url"
    label = "读网页"
    description = (
        "抓取一个网页并把正文提取成纯文本返回（自动去脚本/样式/导航）。"
        "联网搜索（web_search）拿到链接后想读具体内容时用；正文适合总结、引用、存素材。"
        "登录墙/动态渲染页面可能抓不到，抓不到就如实说。"
    )
    default_risk = RiskLevel.L1_LOW

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "网页链接（http/https）"},
                    "max_chars": {"type": "integer", "description": "返回正文的最大字符数，默认 8000"},
                },
                "required": ["url"],
            },
        }

    def run(self, params: dict, ctx: SkillContext) -> ActionResult:
        url = str(params.get("url") or "").strip()
        if not re.match(r"^https?://", url):
            return ActionResult(success=False, error=f"不是合法 http(s) 链接：{url}")
        try:
            max_chars = int(params.get("max_chars") or 8000)
        except (TypeError, ValueError):
            max_chars = 8000
        max_chars = max(500, min(max_chars, 50000))
        try:
            title, text = _fetch_url_text(url)
        except Exception as e:
            return ActionResult(success=False, error=f"抓取失败：{e}")
        if not text:
            return ActionResult(success=False, error="抓到了页面但没提取出正文")
        return ActionResult(success=True, data={
            "url": url, "title": title, "text": text[:max_chars],
        })


class OpenPathSkill(Skill):
    id = "open_path"
    label = "打开文件"
    description = "用默认应用打开一个本地文件/目录；reveal=true 时改为在 Finder 中定位显示该文件。"
    default_risk = RiskLevel.L1_LOW

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "绝对路径"},
                    "reveal": {"type": "boolean", "default": False, "description": "true 时在 Finder 中定位而非打开"},
                },
                "required": ["path"],
            },
        }

    def run(self, params: dict, ctx: SkillContext) -> ActionResult:
        path = str(params.get("path", "")).strip()
        if not path:
            return ActionResult(success=False, error="缺少 path 参数")
        if not Path(path).exists():
            return ActionResult(success=False, error=f"路径不存在：{path}")
        reveal = bool(params.get("reveal"))
        argv = ["open", "-R", path] if reveal else ["open", path]
        try:
            cp = _run_argv(argv)
        except Exception as e:
            return ActionResult(success=False, error=f"open 失败：{e}")
        if cp.returncode != 0:
            return ActionResult(success=False, error=f"open 退出码 {cp.returncode}")
        return ActionResult(success=True, data={"path": path, "reveal": reveal})


class WriteNoteSkill(Skill):
    id = "write_note"
    label = "记笔记"
    description = "打开文本编辑应用（默认 TextEdit）并写入一段文字（新建草稿，不落盘）。适合起草、记录、写文案。"
    default_risk = RiskLevel.L2_MEDIUM

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "要写入的文字"},
                    "app": {"type": "string", "default": "TextEdit", "description": "目标编辑器应用名"},
                },
                "required": ["text"],
            },
        }

    def run(self, params: dict, ctx: SkillContext) -> ActionResult:
        if ctx.host is None:
            return ActionResult(success=False, error="无执行基座 host（ctx.host 为空）")
        text = str(params.get("text", ""))
        if not text.strip():
            return ActionResult(success=False, error="缺少 text 参数")
        app = str(params.get("app", "TextEdit")).strip() or "TextEdit"
        pid = ctx.host.a11y.launch_app(app)
        if pid is None:
            return ActionResult(success=False, error=f"无法打开应用：{app}")
        time.sleep(1.0)  # 等应用起窗
        handle = ctx.host.a11y.find(role="AXTextArea")
        if handle is not None and ctx.host.a11y.set_value(handle, text):
            return ActionResult(success=True, data={"method": "ax", "app": app, "chars": len(text)})
        ctx.host.input.type_text(text)
        return ActionResult(success=True, data={"method": "type", "app": app, "chars": len(text)})


def register_composite_skills(reg: SkillRegistry) -> None:
    """把 5 个复合技能注册到 registry。"""
    for skill in (FindFileSkill(), WebSearchSkill(), ExtractUrlSkill(), OpenPathSkill(), WriteNoteSkill()):
        reg.register(skill)
