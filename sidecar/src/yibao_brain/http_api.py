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


def _with_envelope(msg: dict, data: dict) -> dict:
    """帧 data 并入外层信封字段（有才带）：客户端按 surface/conversation_id 挑着渲染（spec §4.3）。"""
    if msg.get("surface") is not None:
        data["surface"] = msg["surface"]
    if msg.get("conversation_id"):
        data["conversation_id"] = msg["conversation_id"]
    return data


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
        self._closed = False

    def __call__(self, msg: dict) -> None:
        self._write(msg)
        if msg.get("type") == "event":
            ev = msg.get("event") or {}
            self.publish(str(ev.get("kind") or "event"), _with_envelope(msg, dict(ev)))
        elif msg.get("type") == "run_done":
            self.publish("run_done", _with_envelope(msg, {"id": msg.get("id")}))

    def publish(self, event: str, data: dict) -> int:
        """发布一帧（带外主动帧也走这里）。返回 seq。close 后不再投递订阅者。"""
        seq = next(self._seq)
        frame = (seq, event, json.dumps(data, ensure_ascii=False))
        self._buf.append(frame)
        if self._closed:  # 进程退出路径：缓冲照记，订阅者已收哨兵不再打扰
            return seq
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

    def close(self) -> None:
        """关闭分接头（进程退出路径，幂等）：给每个订阅队列投 None 哨兵，
        让阻塞在 q.get() 的 SSE handler 立即收尾——否则要等 30s 心跳写到
        已断连接才抛错，serve_async 的 bridge_server.cleanup() 会挂到心跳超时。"""
        if self._closed:
            return
        self._closed = True
        for q in list(self._subs):
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()  # 丢最旧保活，确保哨兵送达
                    q.put_nowait(None)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    self._subs.discard(q)  # 竞态兜底：队列已不可用，放弃该订阅

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
      conversations:  () -> dict（含 items:[{id, preview, turns}] 桶摘要）
      history:        (conversation_id: str) -> dict（含 items:[{role, text}]；空 id=default 桶）
      feed:           (limit: int) -> dict（含 items/stats/running_tasks，与桌面 feed IPC 同形；mobile M2）
      reminders_list: async () -> dict（含 items；插件缺席/异常 → 空列表不 500；mobile M2）
      reminders_cancel: async (id: str) -> dict（含 ok/error；失败由路由转 500；mobile M2）
      memories:       async () -> dict（含 items；mobile M2）
    """

    save: Callable | None = None
    state: Callable | None = None
    submit_run: Callable | None = None
    interrupt: Callable | None = None
    confirm: Callable | None = None
    register_push: Callable | None = None
    conversations: Callable | None = None
    history: Callable | None = None
    feed: Callable | None = None
    reminders_list: Callable | None = None
    reminders_cancel: Callable | None = None
    memories: Callable | None = None


_VERSION = "1"


def _cors_allow(origin: str | None) -> str | None:
    """移动端 origin 白名单（反射式）：Capacitor iOS=capacitor://localhost、
    Android=http://localhost、开发浏览器=http://localhost:任意端口 / 127.0.0.1:任意端口、
    局域网体验=http://<本机私网IP>:任意端口（手机浏览器经 vite --host 访问）。
    其余 origin 不给 CORS 头（浏览器自会拦截）。"""
    if not origin:
        return None
    if origin.startswith("capacitor://"):
        return origin
    from urllib.parse import urlparse

    try:
        u = urlparse(origin)
    except ValueError:
        return None
    if u.scheme in ("http", "https") and u.hostname:
        if u.hostname == "localhost":
            return origin
        try:
            import ipaddress

            if ipaddress.ip_address(u.hostname).is_private:  # 127.x/192.168.x/10.x/172.16-31.x…
                return origin
        except ValueError:
            pass
    return None


def _cors_headers(allow: str) -> dict:
    return {
        "Access-Control-Allow-Origin": allow,
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        # Last-Event-ID：EventSource 断线重连带的头，不在 CORS 安全清单里，预检必查
        "Access-Control-Allow-Headers": "Content-Type, X-Yibao-Token, Last-Event-ID",
        "Access-Control-Max-Age": "600",
        "Vary": "Origin",  # 反射式 ACAO：缓存键必须含 Origin，否则 CDN/代理串答
    }


def build_app(*, get_bridge_token: Callable[[], str], get_mobile_token: Callable[[], str], tap: EventTap,
              limiter: RateLimiter | None = None, deps: MobileDeps | None = None) -> web.Application:
    """token 走获取闭包而非启动快照：auth 每次请求现取——桌面重置 token 即时生效。"""
    limiter = limiter or RateLimiter()
    deps = deps or MobileDeps()

    @web.middleware
    async def _cors(request: web.Request, handler):
        """CORS 外层中间件：只做预检短路——204 直返（无自定义头，auth 拦不得）。
        反射不在这里做：SSE handler 内 prepare 后头部已落网线，事后 update 是 no-op；
        反射交给 on_response_prepare（头部写出前触发，对流式响应也生效）。"""
        allow = _cors_allow(request.headers.get("Origin"))
        if request.method == "OPTIONS" and allow:
            return web.Response(status=204, headers=_cors_headers(allow))
        return await handler(request)

    async def _add_cors_headers(request: web.Request, response: web.StreamResponse) -> None:
        """按白名单反射 Origin——401/429 也要带，客户端要能读状态。"""
        allow = _cors_allow(request.headers.get("Origin"))
        if allow:
            response.headers.update(_cors_headers(allow))

    @web.middleware
    async def _auth(request: web.Request, handler):
        """双 token 隔离：/v1/* 认移动 token，其余认扩展桥 token。
        移动端允许 token 走 query（EventSource 不能设自定义 header）。"""
        # 先检查是否被锁定（对所有请求生效）
        if not limiter.allow():
            return web.json_response({"ok": False, "error": "失败太多次，稍后再试"}, status=429)

        mobile = request.path.startswith("/v1/")
        token = request.headers.get("x-yibao-token") or request.query.get("token") or ""
        expected = (get_mobile_token() if mobile else get_bridge_token())
        if not hmac.compare_digest(token.encode(), expected.encode()):
            limiter.record_fail()
            return web.json_response({"ok": False, "error": "token 不对"}, status=401)
        return await handler(request)

    app = web.Application(middlewares=[_cors, _auth])
    # CORS 反射挂 on_response_prepare：头部写出前触发——SSE handler 内 prepare 后
    # 中间件再 update 已是 no-op（头部落网线），信号回调对流式响应也生效。
    app.on_response_prepare.append(_add_cors_headers)

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

    async def v1_confirm(request):
        if deps.confirm is None:
            return web.json_response({"ok": False, "error": "not wired"}, status=503)
        body = await request.json()
        cid = str(body.get("id") or "")
        if not cid:
            return web.json_response({"ok": False, "error": "id 为空"}, status=400)
        if not deps.confirm(cid, bool(body.get("approved")), bool(body.get("remember"))):
            return web.json_response({"ok": False, "error": "未知或已处理的确认"}, status=404)
        return web.json_response({"ok": True})

    async def v1_state(request):
        if deps.state is None:
            return web.json_response({"ok": False, "error": "not wired"}, status=503)
        return web.json_response({"ok": True, **deps.state()})

    async def v1_conversations(request):
        """会话列表（mobile M1）：{id, preview, turns} 桶摘要。"""
        if deps.conversations is None:
            return web.json_response({"ok": False, "error": "not wired"}, status=503)
        return web.json_response(deps.conversations())

    async def v1_history(request):
        """单会话消息回显（mobile M1）：无 conversation_id 参数 → default 桶。"""
        if deps.history is None:
            return web.json_response({"ok": False, "error": "not wired"}, status=503)
        return web.json_response(deps.history(request.query.get("conversation_id") or ""))

    async def v1_feed(request):
        """动态流（mobile M2）：与桌面 feed IPC 完全同形（items 倒序），外层多 ok。"""
        if deps.feed is None:
            return web.json_response({"ok": False, "error": "not wired"}, status=503)
        try:
            limit = int(request.query.get("limit") or 60)
        except (TypeError, ValueError):
            limit = 60
        return web.json_response({"ok": True, **deps.feed(limit)})

    async def v1_reminders(request):
        """待办提醒（mobile M2）：reminders.list 直连；空/异常 → 空列表不 500。"""
        if deps.reminders_list is None:
            return web.json_response({"ok": False, "error": "not wired"}, status=503)
        return web.json_response(await deps.reminders_list())

    async def v1_reminders_cancel(request):
        """取消提醒（mobile M2）：reminders.cancel 直连；失败 500 带 error。"""
        if deps.reminders_cancel is None:
            return web.json_response({"ok": False, "error": "not wired"}, status=503)
        body = await request.json()
        rid = str(body.get("id") or "").strip()
        if not rid:
            return web.json_response({"ok": False, "error": "id 为空"}, status=400)
        out = await deps.reminders_cancel(rid)
        if not out.get("ok"):
            return web.json_response({"ok": False, "error": out.get("error") or "取消失败"}, status=500)
        return web.json_response({"ok": True})

    async def v1_memories(request):
        """记忆库（mobile M2）：_mem_list 现成（底座+插件命名空间分组）。"""
        if deps.memories is None:
            return web.json_response({"ok": False, "error": "not wired"}, status=503)
        return web.json_response(await deps.memories())

    async def v1_push_register(request):
        """推送设备登记：同 registration_id 覆盖，防重复堆积"""
        if deps.register_push is None:
            return web.json_response({"ok": False, "error": "not wired"}, status=503)
        body = await request.json()
        rid = str(body.get("registration_id") or "").strip()
        if not rid:
            return web.json_response({"ok": False, "error": "registration_id 为空"}, status=400)
        deps.register_push(rid, str(body.get("platform") or ""))
        return web.json_response({"ok": True})

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
            # 手动重连的 EventSource 新建时带不上 header → last_event_id query 兜底
            # （mobile M1：与 Last-Event-ID header 等效，header 优先）
            last = request.headers.get("Last-Event-ID") or request.query.get("last_event_id")
            try:
                last_seq = int(last) if last else 0
            except ValueError:
                last_seq = 0
            q = tap.subscribe()  # 先订阅：避免 replay 窗口丢帧
            try:
                # 再补发：此时队列已订阅，后续帧不会丢
                frames = tap.replay(last_seq)
                for seq, event, data in frames:
                    await resp.write(_sse_frame(seq, event, data))
                last_written = frames[-1][0] if frames else 0  # 取实际写出的最大 seq（服务端重启场景快照空→0）
                while True:
                    try:
                        frame = await asyncio.wait_for(q.get(), timeout=_HEARTBEAT_S)
                    except asyncio.TimeoutError:
                        await resp.write(b": ping\n\n")
                        continue
                    if frame is None:  # tap.close() 哨兵：进程退出，立即收尾别等心跳
                        break
                    seq, event, data = frame
                    if seq <= last_written:  # 去重：replay 已写出的帧跳过
                        continue
                    await resp.write(_sse_frame(seq, event, data))
                    last_written = seq
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
    app.router.add_post("/v1/confirm", v1_confirm)
    app.router.add_get("/v1/state", v1_state)
    app.router.add_get("/v1/conversations", v1_conversations)
    app.router.add_get("/v1/history", v1_history)
    app.router.add_get("/v1/feed", v1_feed)
    app.router.add_get("/v1/reminders", v1_reminders)
    app.router.add_post("/v1/reminders/cancel", v1_reminders_cancel)
    app.router.add_get("/v1/memories", v1_memories)
    app.router.add_post("/v1/push/register", v1_push_register)
    return app


async def run_server(app: web.Application, host: str, port: int) -> web.AppRunner:
    """程序化起服务（嵌入 serve_async 的 loop）；调用方持有 runner，收尾 runner.cleanup()。"""
    runner = web.AppRunner(app, access_log=None)  # SSE 长连接会刷爆 access log，关掉
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    return runner

