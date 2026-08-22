"""统一 HTTP 客户端：标准库 urllib 封装（单一事实源，避免各模块各写一套）。

- get_bytes / post_bytes：低层字节访问（UA + 超时 + 大小上限）
- HttpClient：JSON 访问（非 JSON 返回原文），供插件 http 能力注入
- fetch_url_text：网页粗提取（标题 + 可读正文）
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

MAX_FETCH_BYTES = 2_000_000  # 网页下载上限（防超大页拖死）
FETCH_TIMEOUT = 10           # 网络请求超时（秒）
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def get_bytes(url: str, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        return resp.read(MAX_FETCH_BYTES)


def post_bytes(url: str, payload: bytes, headers: dict) -> bytes:
    req = urllib.request.Request(
        url, data=payload, headers={"User-Agent": _UA, **headers}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        return resp.read(MAX_FETCH_BYTES)


class HttpClient:
    """标准库 urllib 极简 http 客户端：get/post → 解析后的 json（非 json 返回原文）。10s 超时。"""

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout

    def get(self, url: str, **kw):
        return self._request("GET", url, **kw)

    def post(self, url: str, **kw):
        return self._request("POST", url, **kw)

    def _request(self, method: str, url: str, json_body=None, headers=None, **_):
        hdrs = {"Accept": "application/json", **(headers or {})}
        data = None
        if json_body is not None:
            data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
        except (urllib.error.URLError, OSError) as e:
            raise RuntimeError(f"http 请求失败：{method} {url}：{e}") from e
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return body


def fetch_url_text(url: str) -> tuple[str, str]:
    """抓网页并粗提取可读正文：返回 (标题, 正文文本)。"""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        raw = resp.read(MAX_FETCH_BYTES)
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
