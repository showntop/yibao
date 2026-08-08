"""HTTP 微服务（浏览器扩展桥）：原生 asyncio 零依赖——只接 OPTIONS/GET/POST JSON。

只监听 127.0.0.1；共享 token 头 X-Yibao-Token 由上层路由校验（本机网页也可能扫 localhost）。
不引 starlette/uvicorn：端面小（一个 POST），避开 uvicorn 信号处理器/事件循环集成与打包传递依赖问题。
"""
from __future__ import annotations

import asyncio
import json
import sys
from typing import Awaitable, Callable

_MAX_BODY = 1_000_000  # POST body 上限 1MB
_MAX_HEADER = 16_000

# 路由处理：async (method, path, headers, body: dict) -> (status, json_obj)
Handler = Callable[[str, str, dict, dict], Awaitable[tuple[int, dict]]]

_REASON = {
    200: "OK", 204: "No Content", 400: "Bad Request", 401: "Unauthorized",
    403: "Forbidden", 404: "Not Found", 500: "Internal Server Error",
}


def _cors_headers() -> dict:
    return {
        "Access-Control-Allow-Origin": "*",  # 扩展 origin 是 chrome-extension://<id>，不固定；token 已把关
        "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, X-Yibao-Token",
    }


async def _read_request(reader: asyncio.StreamReader) -> tuple[str, str, dict, bytes] | None:
    """读一个 HTTP 请求（只认 Content-Length 体；不做 chunked/keep-alive）。坏请求返 None。"""
    head = await reader.readuntil(b"\r\n\r\n")  # IncompleteRead/LimitOverrun 由调用方 catch
    if len(head) > _MAX_HEADER:
        return None
    lines = head.decode("latin-1").split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) < 2:
        return None
    method, path = parts[0], parts[1]
    headers = {}
    for ln in lines[1:]:
        if ":" in ln:
            k, v = ln.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    body = b""
    if "content-length" in headers:
        n = int(headers["content-length"])
        if n > _MAX_BODY:
            return None
        body = await reader.readexactly(n)
    return method, path, headers, body


async def _write_response(writer: asyncio.StreamWriter, status: int, obj: dict | None) -> None:
    body = b"" if obj is None else json.dumps(obj, ensure_ascii=False).encode()
    hs = {"Content-Type": "application/json; charset=utf-8", "Connection": "close", **_cors_headers()}
    lines = ([f"HTTP/1.1 {status} {_REASON[status]}"] + [f"{k}: {v}" for k, v in hs.items()]
             + [f"Content-Length: {len(body)}", "", ""])
    writer.write("\r\n".join(lines).encode("latin-1") + body)
    await writer.drain()


async def serve(host: str, port: int, handler: Handler) -> asyncio.AbstractServer:
    """起监听；handler(method, path, headers, body_json) -> (status, obj)。返回 Server（调用方管 close）。"""

    async def _conn(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            req = await _read_request(reader)
            if req is None:
                await _write_response(writer, 400, {"ok": False, "error": "bad request"})
                return
            method, path, headers, raw = req
            if method == "OPTIONS":  # CORS 预检（自定义 token 头必然触发）
                await _write_response(writer, 204, None)
                return
            body = {}
            if raw:
                try:
                    body = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    await _write_response(writer, 400, {"ok": False, "error": "body 必须是 JSON"})
                    return
            try:
                status, obj = await handler(method, path, headers, body)
            except Exception as e:
                print(f"[yibao] HTTP 处理失败：{e}", file=sys.stderr)
                status, obj = 500, {"ok": False, "error": "internal"}
            await _write_response(writer, status, obj)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionResetError, ValueError):
            pass  # 对端断开/畸形请求：静默
        finally:
            try:
                writer.close()
            except Exception:
                pass

    return await asyncio.start_server(_conn, host, port)
