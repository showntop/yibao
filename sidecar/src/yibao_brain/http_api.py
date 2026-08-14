"""aiohttp HTTP 面：浏览器扩展桥（/save /health）+ 手机伴生端 API（/v1/*）+ SSE。

替换原零依赖手写 httpserver.py——SSE 流式/keep-alive/多路由并发继续手搓就是造轮子。
AppRunner/TCPSite 程序化嵌入 serve_async 的 asyncio loop：无信号处理器/事件循环
集成冲突（httpserver.py 时代记的 uvicorn 顾虑，aiohttp 无此问题）。仍只监听
127.0.0.1，TLS 由外层 Caddy（VPS）终结、frp 转发到本机，token 把关。
"""
from __future__ import annotations

import asyncio
import hmac
import itertools
import json
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from aiohttp import web

_SUB_QSIZE = 256  # 每订阅者队列容量；满则丢最旧保活（EventSource 重连+Last-Event-ID 补齐）
_HEARTBEAT_S = 30.0  # SSE 心跳：连接保活 + 让死连接尽快暴露


def _sse_frame(seq: int, event: str, data: str) -> bytes:
    return f"id: {seq}\nevent: {event}\ndata: {data}\n\n".encode()


class EventTap:
    """事件分接头：包装 write_msg——stdio 照发，同时把 event/run_done 消息复制成
    SSE 帧（单调 seq + 环形缓冲，Last-Event-ID 断点补发）。

    v1 广播不过滤 surface：手机能看到桌宠的对话事件，多设备天然支持；
    客户端自行按 surface/conversation_id 挑着渲染。
    """

    def __init__(self, write_msg: Callable[[dict], None], capacity: int = 256):
        self._write = write_msg
        self._seq = itertools.count(1)
        self._buf: deque[tuple[int, str, str]] = deque(maxlen=capacity)
        self._subs: set[asyncio.Queue] = set()

    def __call__(self, msg: dict) -> None:
        self._write(msg)
        if msg.get("type") == "event":
            ev = msg.get("event") or {}
            self.publish(str(ev.get("kind") or "event"), ev)
        elif msg.get("type") == "run_done":
            self.publish("run_done", {"id": msg.get("id")})

    def publish(self, event: str, data: dict) -> int:
        """发布一帧（带外主动帧也走这里）。返回 seq。"""
        seq = next(self._seq)
        frame = (seq, event, json.dumps(data, ensure_ascii=False))
        self._buf.append(frame)
        for q in list(self._subs):
            try:
                q.put_nowait(frame)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()  # 丢最旧保活
                    q.put_nowait(frame)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    self._subs.discard(q)  # 竞态兜底：队列已不可用，放弃该订阅
        return seq

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=_SUB_QSIZE)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def replay(self, last_seq: int) -> list[tuple[int, str, str]]:
        """断线补发：seq > last_seq 的缓冲帧（超出缓冲窗口只能全量拉 /v1/state 重建）。"""
        return [f for f in self._buf if f[0] > last_seq]


class RateLimiter:
    """认证失败限速：window 秒内 fails 次失败 → 锁 lock 秒。内存级、全局单桶——
    个人服务器（1-2 台设备）够用，别为它上 per-ip 表。"""

    def __init__(self, fails: int = 5, window: float = 60.0, lock: float = 60.0):
        self.fails, self.window, self.lock = fails, window, lock
        self._hits: list[float] = []
        self._locked_until = 0.0

    def allow(self) -> bool:
        now = time.monotonic()
        if now < self._locked_until:
            return False
        self._hits = [t for t in self._hits if now - t < self.window]
        return True

    def record_fail(self) -> None:
        now = time.monotonic()
        self._hits.append(now)
        if len(self._hits) >= self.fails:
            self._locked_until = now + self.lock
            self._hits.clear()


@dataclass
class MobileDeps:
    """serve_async 注入的闭包集合（测试注 fake）。字段 None = 未接线；对应路由 503。

    签名约定（serve_async 侧按此实现，测试按此 fake）：
      save:          async (body: dict) -> tuple[int, dict]
      state:         () -> dict（含 running/pending 两键）
      submit_run:    (text: str, conversation_id: str) -> dict（含 run_id/conversation_id）
      interrupt:     () -> bool（是否真的打断）
      confirm:       (id: str, approved: bool, remember: bool) -> bool（False=已处理/未知）
      register_push: (registration_id: str, platform: str) -> None
    """

    save: Callable | None = None
    state: Callable | None = None
    submit_run: Callable | None = None
    interrupt: Callable | None = None
    confirm: Callable | None = None
    register_push: Callable | None = None


_VERSION = "1"


def build_app(*, bridge_token: str, mobile_token: str, tap: EventTap,
              limiter: RateLimiter | None = None, deps: MobileDeps | None = None) -> web.Application:
    limiter = limiter or RateLimiter()
    deps = deps or MobileDeps()

    @web.middleware
    async def _auth(request: web.Request, handler):
        """双 token 隔离：/v1/* 认移动 token，其余认扩展桥 token。
        移动端允许 token 走 query（EventSource 不能设自定义 header）。"""
        # 先检查是否被锁定（对所有请求生效）
        if not limiter.allow():
            return web.json_response({"ok": False, "error": "失败太多次，稍后再试"}, status=429)

        mobile = request.path.startswith("/v1/")
        token = request.headers.get("x-yibao-token") or request.query.get("token") or ""
        expected = mobile_token if mobile else bridge_token
        if not hmac.compare_digest(token.encode(), expected.encode()):
            limiter.record_fail()
            return web.json_response({"ok": False, "error": "token 不对"}, status=401)
        return await handler(request)

    app = web.Application(middlewares=[_auth])

    async def health(request):
        return web.json_response({"ok": True, "service": "yibao-bridge"})

    async def v1_health(request):
        return web.json_response({"ok": True, "service": "yibao", "version": _VERSION})

    async def save(request):
        if deps.save is None:
            return web.json_response({"ok": False, "error": "not wired"}, status=503)
        status, obj = await deps.save(await request.json())
        return web.json_response(obj, status=status)

    async def v1_chat(request):
        if deps.submit_run is None:
            return web.json_response({"ok": False, "error": "not wired"}, status=503)
        body = await request.json()
        text = str(body.get("text") or "").strip()
        if not text:
            return web.json_response({"ok": False, "error": "text 为空"}, status=400)
        return web.json_response(
            deps.submit_run(text, str(body.get("conversation_id") or "")))

    async def v1_interrupt(request):
        if deps.interrupt is None:
            return web.json_response({"ok": False, "error": "not wired"}, status=503)
        return web.json_response({"ok": True, "interrupted": bool(deps.interrupt())})

    async def v1_events(request):
        """SSE 事件流：先订阅（避免 replay → subscribe 窗口丢帧），再补发 Last-Event-ID 之后的缓冲帧，最后实时消费队列并按 seq 去重。
        EventSource 不能设 header → token 走 query（auth 中间件已验）。"""
        resp = web.StreamResponse(headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 反代（Caddy/nginx）别缓冲
        })
        await resp.prepare(request)
        try:
            last = request.headers.get("Last-Event-ID")
            try:
                last_seq = int(last) if last else 0
            except ValueError:
                last_seq = 0
            q = tap.subscribe()  # 先订阅：避免 replay 窗口丢帧
            try:
                # 再补发：此时队列已订阅，后续帧不会丢
                for seq, event, data in tap.replay(last_seq):
                    await resp.write(_sse_frame(seq, event, data))
                last_written = last_seq  # 记录已写出的最大 seq
                while True:
                    try:
                        seq, event, data = await asyncio.wait_for(q.get(), timeout=_HEARTBEAT_S)
                        if seq <= last_written:  # 去重：replay 已写出的帧跳过
                            continue
                        await resp.write(_sse_frame(seq, event, data))
                        last_written = seq
                    except asyncio.TimeoutError:
                        await resp.write(b": ping\n\n")
            finally:
                tap.unsubscribe(q)
        except (ConnectionResetError, asyncio.CancelledError):
            pass  # 客户端断开 / 应用关闭：正常收尾
        return resp

    app.router.add_get("/health", health)
    app.router.add_get("/v1/health", v1_health)
    app.router.add_post("/save", save)
    app.router.add_post("/v1/save", save)
    app.router.add_post("/v1/chat", v1_chat)
    app.router.add_post("/v1/interrupt", v1_interrupt)
    app.router.add_get("/v1/events", v1_events)
    return app


async def run_server(app: web.Application, host: str, port: int) -> web.AppRunner:
    """程序化起服务（嵌入 serve_async 的 loop）；调用方持有 runner，收尾 runner.cleanup()。"""
    runner = web.AppRunner(app, access_log=None)  # SSE 长连接会刷爆 access log，关掉
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    return runner

