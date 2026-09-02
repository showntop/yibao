"""Surface L0 读模型的单飞、短缓存与熔断。

UI 查询不是领域历史，但仍可能因 WebView/响应式循环产生请求风暴。本模块按
``method + params + conversation + surface`` 聚合读取：并发只执行一次，短窗口
复用结果，超过突发阈值时优先返回最近快照；没有快照则明确限流。
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Generic, TypeVar


T = TypeVar("T")


class SurfaceReadThrottled(RuntimeError):
    pass


@dataclass(frozen=True)
class SurfaceReadPolicy:
    cache_ttl: float = 0.35
    stale_ttl: float = 30.0
    burst_window: float = 1.0
    burst_limit: int = 20
    cooldown: float = 2.0
    max_keys: int = 256


@dataclass
class _ReadState(Generic[T]):
    hits: deque[float] = field(default_factory=deque)
    cached: T | None = None
    cached_at: float = 0.0
    open_until: float = 0.0
    in_flight: asyncio.Task[T] | None = None


class SurfaceReadGuard(Generic[T]):
    """事件循环内共享；返回深拷贝，避免调用方改坏缓存快照。"""

    def __init__(
        self, policy: SurfaceReadPolicy | None = None, *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.policy = policy or SurfaceReadPolicy()
        self._clock = clock
        self._lock = asyncio.Lock()
        self._states: OrderedDict[str, _ReadState[T]] = OrderedDict()

    @staticmethod
    def key(
        method: str, params: dict, *, conversation_id: str = "", surface: str = "",
    ) -> str:
        canonical = json.dumps(
            {
                "method": str(method), "params": params or {},
                "conversation_id": str(conversation_id), "surface": str(surface),
            },
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    async def run(self, key: str, factory: Callable[[], Awaitable[T]]) -> tuple[T, str]:
        now = self._clock()
        owner = False
        async with self._lock:
            state = self._states.setdefault(key, _ReadState())
            self._states.move_to_end(key)
            cutoff = now - self.policy.burst_window
            while state.hits and state.hits[0] < cutoff:
                state.hits.popleft()
            state.hits.append(now)

            task = state.in_flight
            if task is not None:
                # 已有读取在跑时，所有后来者先共享它；熔断不能把本可合并的
                # 并发请求变成错误风暴。
                self._evict_idle_locked(exclude=key)
            else:
                cached_age = now - state.cached_at
                has_stale = state.cached is not None and cached_age <= self.policy.stale_ttl
                if now < state.open_until:
                    if has_stale:
                        return copy.deepcopy(state.cached), "stale"
                    raise SurfaceReadThrottled("面板读取请求过密，已短暂熔断")

                if len(state.hits) > self.policy.burst_limit:
                    state.open_until = now + self.policy.cooldown
                    if has_stale:
                        return copy.deepcopy(state.cached), "stale"
                    raise SurfaceReadThrottled("面板读取请求过密，已短暂熔断")

                if state.cached is not None and cached_age <= self.policy.cache_ttl:
                    return copy.deepcopy(state.cached), "cache"

                task = asyncio.create_task(factory())
                state.in_flight = task
                owner = True
                self._evict_idle_locked(exclude=key)

        try:
            result = await task
        except Exception:
            if owner:
                async with self._lock:
                    state = self._states.get(key)
                    if state is not None and state.in_flight is task:
                        state.in_flight = None
            raise

        if owner:
            async with self._lock:
                state = self._states.get(key)
                if state is not None:
                    if state.in_flight is task:
                        state.in_flight = None
                    state.cached = copy.deepcopy(result)
                    state.cached_at = self._clock()
        return copy.deepcopy(result), "origin" if owner else "singleflight"

    def _evict_idle_locked(self, *, exclude: str) -> None:
        while len(self._states) > self.policy.max_keys:
            removed = False
            for candidate, state in self._states.items():
                if candidate != exclude and state.in_flight is None:
                    del self._states[candidate]
                    removed = True
                    break
            if not removed:
                break
