"""aiohttp HTTP 面：浏览器扩展桥（/save /health）+ 手机伴生端 API（/v1/*）+ SSE。

替换原零依赖手写 httpserver.py——SSE 流式/keep-alive/多路由并发继续手搓就是造轮子。
AppRunner/TCPSite 程序化嵌入 serve_async 的 asyncio loop：无信号处理器/事件循环
集成冲突（httpserver.py 时代记的 uvicorn 顾虑，aiohttp 无此问题）。仍只监听
127.0.0.1，TLS 由外层 Caddy（VPS）终结、frp 转发到本机，token 把关。
"""
from __future__ import annotations

import asyncio
import itertools
import json
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from aiohttp import web

_SUB_QSIZE = 256  # 每订阅者队列容量；满则丢最旧保活（EventSource 重连+Last-Event-ID 补齐）


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
