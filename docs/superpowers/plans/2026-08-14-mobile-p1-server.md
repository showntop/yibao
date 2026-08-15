# 手机伴生端 P1（服务端 HTTP 面 + 移动 API + SSE）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 aiohttp 替换手写 httpserver.py，在 sidecar 上建起手机伴生端需要的全部服务端 API（`/v1/*` + SSE 流式 + 远程审批），curl 即可全量验证。

**Architecture:** 新模块 `http_api.py` 承载 aiohttp 应用（扩展桥 `/save` 一并迁入，旧 `httpserver.py` 删除）；`serve_async` 里注入闭包集合（MobileDeps）连接 HTTP 面与现有 run/confirm/抢占机制；事件分接头（EventTap）包装 write_msg，stdio 照发的同时复制成 SSE 帧广播给手机。

**Tech Stack:** Python 3.12 / aiohttp（不引 uvicorn/starlette）/ pytest（仓内无 pytest-asyncio，测试一律 `asyncio.run` 风格）。

**Spec:** `docs/superpowers/specs/2026-08-14-mobile-companion-design.md`（§4 服务端设计、§8 扩展口、§9 错误处理）

## Global Constraints

- aiohttp 版本 `>=3.10,<4`；**禁止**引入 uvicorn/starlette/fastapi（sidecar 主线程托管 loop，uvicorn 信号捕获是坑——原 httpserver.py docstring 记录在案）
- HTTP 面仍只监听 `127.0.0.1`（frp 转发到本机），端口沿用 `http_port()`（默认 19527）
- 认证：扩展桥与移动端**双 token 隔离**（`http.token` / `http.mobile_token`）；常量时间比较（`hmac.compare_digest`）；失败 5 次/分钟锁 60 秒
- SSE 帧格式：`id: <seq>\nevent: <kind>\ndata: <json>\n\n`；seq 单调；环形缓冲 256 帧，`Last-Event-ID` 断点补发；30s 心跳 `: ping`
- 测试风格沿用仓内惯例：函数级 + `asyncio.run`，fake 注入，**不起** pytest-asyncio；中文注释/文档字符串
- settings 新键必须先加进 `config.py` 的 `_SETTINGS_DEFAULTS`（`save_settings` 只落已知键）
- 每个任务 TDD：先写失败测试 → 跑红 → 最小实现 → 跑绿 → 提交。提交信息风格沿用仓内（如 `feat(server): …` / `test(server): …`，中文描述）

---

### Task 1: aiohttp 依赖 + EventTap（事件分接头）

**Files:**
- Modify: `sidecar/pyproject.toml`（dependencies 数组）
- Create: `sidecar/src/yibao_brain/http_api.py`
- Test: `sidecar/tests/test_http_api.py`

**Interfaces:**
- Consumes: 无（首个任务）
- Produces: `EventTap(write_msg: Callable[[dict], None], capacity: int = 256)`，可调用对象（`__call__(msg)`）、`publish(event: str, data: dict) -> int`、`subscribe() -> asyncio.Queue`、`unsubscribe(q)`、`replay(last_seq: int) -> list[tuple[int, str, str]]`。帧形状 `(seq, event_name, data_json_str)`。后续 Task 3/6 依赖这些签名。

- [ ] **Step 1: 加依赖**

`sidecar/pyproject.toml` 的 `dependencies` 数组末尾（`"fastembed>=0.4.0",` 之后）加：

```toml
    # 手机伴生端 HTTP 面（P1）：扩展桥 + /v1/* 移动 API + SSE。程序化嵌入 serve_async
    # 的 asyncio loop（AppRunner/TCPSite），无 uvicorn 信号处理器/事件循环集成坑。
    "aiohttp>=3.10,<4",
```

Run: `cd sidecar && uv sync`
Expected: 依赖装上，无冲突

- [ ] **Step 2: 写失败测试**

`sidecar/tests/test_http_api.py`：

```python
"""http_api（aiohttp HTTP 面）：EventTap / RateLimiter / build_app 单元测试。
函数级 + asyncio.run 风格（仓内无 pytest-asyncio）；路由级测试用 aiohttp TestServer。"""
import asyncio

from yibao_brain.http_api import EventTap


def test_tap_passes_through_and_frames_event_and_run_done():
    out = []
    tap = EventTap(lambda m: out.append(m))
    tap({"type": "hello", "version": 1})  # 非 event/run_done：透传不进 SSE
    tap({"type": "event", "surface": "mobile", "event": {"kind": "final_reply_chunk", "text": "你好"}})
    tap({"type": "run_done", "id": "r1"})
    assert out[0] == {"type": "hello", "version": 1}  # stdio 原样照发
    frames = tap.replay(0)
    assert [f[1] for f in frames] == ["final_reply_chunk", "run_done"]
    assert frames[0][0] == 1 and frames[1][0] == 2  # seq 单调
    import json
    assert json.loads(frames[0][2])["kind"] == "final_reply_chunk"
    assert json.loads(frames[1][2]) == {"id": "r1"}


def test_tap_replay_from_last_seq_only():
    tap = EventTap(lambda m: None)
    for i in range(5):
        tap.publish("chunk", {"i": i})
    frames = tap.replay(3)  # 只要 seq>3
    assert [f[0] for f in frames] == [4, 5]


def test_tap_subscriber_gets_frames_and_slow_consumer_drops_oldest():
    async def main():
        tap = EventTap(lambda m: None)
        q = tap.subscribe()
        tap.publish("chunk", {"i": 1})
        assert (await q.get())[2]  # 收到帧
        tap.unsubscribe(q)
        tap.publish("chunk", {"i": 2})
        assert q.empty()  # 退订后不再收

        # 慢消费：小容量队列满后丢最旧保活（客户端靠 Last-Event-ID 重连补齐）
        small = EventTap(lambda m: None)
        sq = small.subscribe()
        small._subs.clear(); small._subs.add(sq)  # 复用订阅但直接压小容量
        # 直接构造满队列场景：容量 256 默认，改为手动灌满
        for _ in range(256):
            small.publish("chunk", {"x": 1})
        small.publish("chunk", {"x": 2})  # 第 257 帧 → 触发丢最旧
        assert sq.qsize() == 256  # 没炸、没断订阅

    asyncio.run(main())
```

- [ ] **Step 3: 跑测试确认失败**

Run: `cd sidecar && uv run pytest tests/test_http_api.py -v`
Expected: FAIL（`No module named 'yibao_brain.http_api'`）

- [ ] **Step 4: 最小实现**

`sidecar/src/yibao_brain/http_api.py`：

```python
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
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd sidecar && uv run pytest tests/test_http_api.py -v`
Expected: 3 PASS

- [ ] **Step 6: 提交**

```bash
git add sidecar/pyproject.toml sidecar/uv.lock sidecar/src/yibao_brain/http_api.py sidecar/tests/test_http_api.py
git commit -m "feat(server): 引入 aiohttp 并实现 EventTap 事件分接头"
```

---

### Task 2: RateLimiter（认证失败限速）

**Files:**
- Modify: `sidecar/src/yibao_brain/http_api.py`
- Test: `sidecar/tests/test_http_api.py`（追加）

**Interfaces:**
- Produces: `RateLimiter(fails: int = 5, window: float = 60.0, lock: float = 60.0)`，方法 `allow() -> bool`、`record_fail() -> None`。Task 3 的 auth 中间件消费。

- [ ] **Step 1: 写失败测试**

追加到 `sidecar/tests/test_http_api.py`：

```python
def test_rate_limiter_locks_after_5_fails():
    from yibao_brain.http_api import RateLimiter

    rl = RateLimiter(fails=5, window=60.0, lock=60.0)
    for _ in range(4):
        assert rl.allow() is True
        rl.record_fail()
    assert rl.allow() is True  # 第 5 次尝试仍放行（失败后才锁）
    rl.record_fail()
    assert rl.allow() is False  # 已锁


def test_rate_limiter_lock_expires():
    from yibao_brain.http_api import RateLimiter

    rl = RateLimiter(fails=2, window=60.0, lock=0.05)
    rl.record_fail(); rl.record_fail()
    assert rl.allow() is False
    time.sleep(0.06)
    assert rl.allow() is True  # 锁过期
```

（文件顶部 import 需补 `import time`）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd sidecar && uv run pytest tests/test_http_api.py -v -k rate`
Expected: FAIL（RateLimiter 不存在）

- [ ] **Step 3: 最小实现**

追加到 `sidecar/src/yibao_brain/http_api.py`（EventTap 之后）：

```python
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
```

（文件顶部 import 需补 `import time`）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd sidecar && uv run pytest tests/test_http_api.py -v`
Expected: 5 PASS

- [ ] **Step 5: 提交**

```bash
git add sidecar/src/yibao_brain/http_api.py sidecar/tests/test_http_api.py
git commit -m "feat(server): 认证失败限速器"
```

---

### Task 3: build_app 骨架（auth 中间件 + /health + /v1/health）

**Files:**
- Modify: `sidecar/src/yibao_brain/http_api.py`
- Test: `sidecar/tests/test_http_api.py`（追加路由级测试）

**Interfaces:**
- Consumes: Task 1 `EventTap`、Task 2 `RateLimiter`
- Produces: `MobileDeps`（dataclass，字段全部可空：`save`/`state`/`submit_run`/`interrupt`/`confirm`/`register_push`）+ `build_app(*, bridge_token: str, mobile_token: str, tap: EventTap, limiter: RateLimiter | None = None, deps: MobileDeps | None = None) -> web.Application` + `run_server(app, host, port) -> web.AppRunner`。后续任务只往 build_app 里加路由处理器，不改签名。

- [ ] **Step 1: 写失败测试**

追加到 `sidecar/tests/test_http_api.py`：

```python
from aiohttp.test_utils import TestClient, TestServer

from yibao_brain.http_api import MobileDeps, build_app


def _mkapp():
    return build_app(bridge_token="btok", mobile_token="mtok", tap=EventTap(lambda m: None))


def test_health_with_bridge_token():
    async def main():
        client = TestClient(TestServer(_mkapp()))
        await client.start_server()
        try:
            r = await client.get("/health", headers={"X-Yibao-Token": "btok"})
            assert r.status == 200 and (await r.json())["service"] == "yibao-bridge"
            r = await client.get("/v1/health", headers={"X-Yibao-Token": "mtok"})
            assert r.status == 200 and (await r.json())["service"] == "yibao"
        finally:
            await client.close()

    asyncio.run(main())


def test_tokens_are_isolated():
    async def main():
        client = TestClient(TestServer(_mkapp()))
        await client.start_server()
        try:
            # 扩展 token 打移动端 → 401（隔离）
            r = await client.get("/v1/health", headers={"X-Yibao-Token": "btok"})
            assert r.status == 401
            # 移动 token 打扩展桥 → 401
            r = await client.get("/health", headers={"X-Yibao-Token": "mtok"})
            assert r.status == 401
            # 移动端允许 token 走 query（EventSource 不能设 header）
            r = await client.get("/v1/health", params={"token": "mtok"})
            assert r.status == 200
        finally:
            await client.close()

    asyncio.run(main())


def test_auth_lockout_after_5_fails():
    async def main():
        from yibao_brain.http_api import RateLimiter

        app = build_app(bridge_token="btok", mobile_token="mtok",
                        tap=EventTap(lambda m: None), limiter=RateLimiter(fails=5, window=60, lock=60))
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            for _ in range(5):
                r = await client.get("/v1/health", headers={"X-Yibao-Token": "bad"})
                assert r.status == 401
            r = await client.get("/v1/health", headers={"X-Yibao-Token": "mtok"})  # 对 token 也被锁
            assert r.status == 429
        finally:
            await client.close()

    asyncio.run(main())
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd sidecar && uv run pytest tests/test_http_api.py -v -k "health or lockout or isolated"`
Expected: FAIL（build_app 不存在）

- [ ] **Step 3: 最小实现**

追加到 `sidecar/src/yibao_brain/http_api.py`：

```python
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

    save: Any = None
    state: Any = None
    submit_run: Any = None
    interrupt: Any = None
    confirm: Any = None
    register_push: Any = None


_VERSION = "1"


def build_app(*, bridge_token: str, mobile_token: str, tap: EventTap,
              limiter: RateLimiter | None = None, deps: MobileDeps | None = None) -> web.Application:
    limiter = limiter or RateLimiter()
    deps = deps or MobileDeps()

    @web.middleware
    async def _auth(request: web.Request, handler):
        """双 token 隔离：/v1/* 认移动 token，其余认扩展桥 token。
        移动端允许 token 走 query（EventSource 不能设自定义 header）。"""
        mobile = request.path.startswith("/v1/")
        token = request.headers.get("x-yibao-token") or request.query.get("token") or ""
        expected = mobile_token if mobile else bridge_token
        if not hmac.compare_digest(token.encode(), expected.encode()):
            if not limiter.allow():
                return web.json_response({"ok": False, "error": "失败太多次，稍后再试"}, status=429)
            limiter.record_fail()
            return web.json_response({"ok": False, "error": "token 不对"}, status=401)
        return await handler(request)

    app = web.Application(middlewares=[_auth])

    async def health(request):
        return web.json_response({"ok": True, "service": "yibao-bridge"})

    async def v1_health(request):
        return web.json_response({"ok": True, "service": "yibao", "version": _VERSION})

    app.router.add_get("/health", health)
    app.router.add_get("/v1/health", v1_health)
    return app


async def run_server(app: web.Application, host: str, port: int) -> web.AppRunner:
    """程序化起服务（嵌入 serve_async 的 loop）；调用方持有 runner，收尾 runner.cleanup()。"""
    runner = web.AppRunner(app, access_log=None)  # SSE 长连接会刷爆 access log，关掉
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    return runner
```

（文件顶部 import 补：`import hmac`、`from dataclasses import dataclass`）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd sidecar && uv run pytest tests/test_http_api.py -v`
Expected: 8 PASS

- [ ] **Step 5: 提交**

```bash
git add sidecar/src/yibao_brain/http_api.py sidecar/tests/test_http_api.py
git commit -m "feat(server): aiohttp 应用骨架与双 token 认证中间件"
```

---

### Task 4: /save 迁移 + 删除 httpserver.py + serve_async 接线

**Files:**
- Modify: `sidecar/src/yibao_brain/config.py:294`（`_SETTINGS_DEFAULTS` 加三键）
- Modify: `sidecar/src/yibao_brain/server.py`（`_make_bridge_route` → 抽 `_bridge_save`；`_start_bridge` → `_start_http_api`；`_ensure_bridge_token` 泛化）
- Create: 删除 `sidecar/src/yibao_brain/httpserver.py`、`sidecar/tests/test_httpserver.py`
- Modify: `sidecar/tests/test_bridge.py`（迁移到新接口）
- Modify: `sidecar/src/yibao_brain/http_api.py`（加 `/save` + `/v1/save` 路由）

**Interfaces:**
- Consumes: Task 3 `build_app/MobileDeps/run_server`
- Produces: `server._bridge_save(agent, emit, body) -> tuple[int, dict]`（async，无 HTTP 细节的纯核心，emit 签名 `(action, result) -> None`）；`server._ensure_http_token(settings, key) -> str`；`MobileDeps.save` 接线完成。

- [ ] **Step 1: config 加默认键**

`sidecar/src/yibao_brain/config.py` `_SETTINGS_DEFAULTS` 中 `"http.token": "",` 行后加：

```python
    "http.mobile_token": "",  # 手机伴生端 token（与扩展桥隔离，可单独重置）
    "http.public_url": "",    # 对外域名（VPS Caddy）；配对二维码用，空=仅局域网调试
    "push.devices": [],       # 已注册推送设备 [{registration_id, platform, added_at}]
```

- [ ] **Step 2: 改造 server.py——token 泛化 + 抽 _bridge_save**

`sidecar/src/yibao_brain/server.py`：

把 `_ensure_bridge_token`（:349）改为泛化（保留旧名兼容现有调用/测试是**不**保留——一并改）：

```python
def _ensure_http_token(settings: dict, key: str) -> str:
    """HTTP 面共享 token（http.token=扩展桥 / http.mobile_token=手机伴生端）：
    空则生成并持久化（save_settings 只落已知键，两键均已在默认表）。"""
    tok = str(settings.get(key) or "")
    if not tok:
        import secrets

        tok = secrets.token_hex(16)
        save_settings({key: tok})
        settings[key] = tok
    return tok
```

把 `_make_bridge_route`（:361-416）整体替换为纯核心函数（去掉 HTTP 路由壳，逻辑逐行保留）：

```python
_BRIDGE_SEQ = itertools.count(1)  # 桥/分享保存的 action id 序（跨调用唯一）


async def _bridge_save(agent: AgentLoop, emit, body: dict) -> tuple[int, dict]:
    """存素材/选题核心（扩展桥 /save 与手机 /v1/save 共用；原 _make_bridge_route._route 主体）。
    emit(action, result)：回执出口（经 EventTap → stdio 壳 + SSE 手机）。"""
    url = str(body.get("url") or "").strip()
    title = str(body.get("title") or "").strip()[:200]
    text = str(body.get("text") or "").strip()[:20000]
    mode = str(body.get("mode") or "material")
    if not text:
        return 400, {"ok": False, "error": "text 为空"}
    if mode == "material":
        api_name = "zimeiti.invoke_mat_save"
        # 先存后整理：defer 跳过 LLM 摘要立刻落库（秒回），mat_enrich 后台补元数据
        params = {"url": url, "text": f"{title}\n\n{text}" if title else text, "title": title, "defer": True}
    elif mode == "topic":
        api_name = "zimeiti.invoke_add_topic"
        params = {"title": title or text[:30], "source": url or "浏览器扩展"}
    else:
        return 400, {"ok": False, "error": f"未知 mode：{mode}"}
    api = get_api(api_name)
    if api is None or not api.direct:
        return 500, {"ok": False, "error": f"方法不可用：{api_name}"}
    rid = f"http_{next(_BRIDGE_SEQ)}"
    action = agent.invoker.propose(ToolCall(id=f"pa_{rid}", skill_id=api.handler, params=params))
    action.id = f"pa_{rid}"  # 壳侧靠 pa_ 前缀认领回执（与 panel_action 同协议）
    if api.risk is not None:
        action.risk = max(action.risk, api.risk)
    decision = agent.invoker.decide(action)
    if decision != Decision.AUTO:
        return 403, {"ok": False, "error": "策略要求确认或禁止（桥场景无确认通道），未执行"}
    result = await _offload(agent.invoker.execute, action, params)
    emit(action, result)
    if not result.success:
        return 500, {"ok": False, "error": result.error or "执行失败"}
    data = result.data or {}
    if mode == "material" and data.get("pending"):
        asyncio.ensure_future(_enrich_later(agent, data.get("id")))
    return 200, {"ok": True, "title": data.get("title", title)}
```

- [ ] **Step 3: http_api.py 加 /save 与 /v1/save 路由**

`build_app` 内（`v1_health` 定义之后）加：

```python
    async def save(request):
        if deps.save is None:
            return web.json_response({"ok": False, "error": "not wired"}, status=503)
        status, obj = await deps.save(await request.json())
        return web.json_response(obj, status=status)
```

路由注册区加两行：

```python
    app.router.add_post("/save", save)
    app.router.add_post("/v1/save", save)
```

- [ ] **Step 4: 写失败测试（/save 经 aiohttp + 核心函数迁移）**

改写 `sidecar/tests/test_bridge.py`：保留文件头 fixture `_bridge_api_whitelist` 与 `_FakeInvoker/_FakeAgent` 不动；`_run` 不变；把 `_route(...)` 构造与调用替换为：

```python
def _mkdeps(invoker, events):
    from yibao_brain.http_api import MobileDeps
    from yibao_brain.server import _bridge_save

    agent = _FakeAgent(invoker)

    def emit(action, result):
        events.append({"kind": "action_result", "action": {"id": action.id, "skill_id": action.skill_id}})

    async def save(body):
        return await _bridge_save(agent, emit, body)

    return MobileDeps(save=save)
```

原 `test_route_*` 系列逐一迁移——示例（其余同法，断言不变）：

```python
def test_save_material_executes_mat_save_and_emits_pa_http():
    async def main():
        events = []
        invoker = _FakeInvoker()
        app = build_app(bridge_token="btok", mobile_token="mtok",
                        tap=EventTap(lambda m: None), deps=_mkdeps(invoker, events))
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            r = await client.post("/save", headers={"X-Yibao-Token": "btok"},
                                  json={"url": "https://a.com/x", "title": "标题", "text": "正文", "mode": "material"})
            assert r.status == 200 and await r.json() == {"ok": True, "title": "存好了"}
            exe = [c for c in invoker.calls if c[0] == "execute"]
            assert exe and exe[0][1] == "zimeiti.mat_save"
            assert "正文" in exe[0][2]["text"] and "标题" in exe[0][2]["text"]
            assert events[0]["action"]["id"].startswith("pa_http_")
        finally:
            await client.close()

    _run(main())
```

`test_ensure_bridge_token_*` 两个测试改为对 `_ensure_http_token`：

```python
def test_ensure_http_token_generates_and_persists(monkeypatch):
    saved = {}
    monkeypatch.setattr("yibao_brain.server.save_settings", lambda v: saved.update(v))
    settings = {"http.mobile_token": ""}
    tok = _ensure_http_token(settings, "http.mobile_token")
    assert len(tok) == 32 and settings["http.mobile_token"] == tok
    assert saved == {"http.mobile_token": tok}
```

（import 行同步改：`from yibao_brain.server import _bridge_save, _ensure_http_token`，补 `from aiohttp.test_utils import TestClient, TestServer`、`from yibao_brain.http_api import EventTap, MobileDeps, build_app`）

`test_route_wrong_token_401`、`test_route_health` 删除（Task 3 已在 test_http_api.py 覆盖认证与 health）；`test_route_empty_text_400_and_bad_mode_400_and_confirm_403`、`test_route_topic_executes_add`、`test_route_material_defers_and_schedules_enrich`、`test_zimeiti_api_toml_has_quiet_bridge_entries` 迁移保留（前三个改经 HTTP 客户端调用，最后一个不动）。

Run: `cd sidecar && uv run pytest tests/test_bridge.py -v`
Expected: FAIL（`_bridge_save` 未完成接线前先红——本 Step 顺序上 Step 2 已实现核心，此处应仅路由测试红/绿交替验证；若已全绿属正常，继续）

- [ ] **Step 5: serve_async 接线——_start_bridge → _start_http_api**

`sidecar/src/yibao_brain/server.py`：删除 `_start_bridge`（:435-446），替换为：

```python
async def _start_http_api(agent: AgentLoop, write_msg: WriteMsg, settings: dict, tap, deps) -> "object | None":
    """起 aiohttp HTTP 面（扩展桥 + 移动 API）；失败 → stderr + None（不拖垮大脑）。"""
    try:
        from .http_api import build_app, run_server

        app = build_app(
            bridge_token=_ensure_http_token(settings, "http.token"),
            mobile_token=_ensure_http_token(settings, "http.mobile_token"),
            tap=tap,
            deps=deps,
        )
        runner = await run_server(app, "127.0.0.1", http_port())
        print(f"[yibao] HTTP 面（桥+移动 API）已监听 127.0.0.1:{http_port()}", file=sys.stderr)
        return runner
    except Exception as e:
        print(f"[yibao] HTTP 面启动失败（{e}，已禁用）", file=sys.stderr)
        return None
```

serve_async 内（:913-915 一带）原：

```python
    bridge_server = None
    if http_enabled:
        bridge_server = await _start_bridge(agent, write_msg, settings)
```

改为**占位**（真正的 deps 组装在后面任务逐步补齐；本任务只接 save）：

```python
    bridge_server = None  # aiohttp runner（Task 4 起）：deps 闭包依赖后文定义的 _drive_run 等，启动挪到主循环前
```

并删除 `from .httpserver import serve` 相关残留。再到主派发循环开始前（搜 `while True:` 主循环 / `queue.get()`，`_reader` 线程启动之后的派发入口处），插入：

```python
    # HTTP 面（扩展桥+移动 API）：deps 里的闭包依赖上文 _drive_run 等，故在主循环前才组装启动
    _http_deps = MobileDeps()
    if http_enabled:
        async def _http_save(body: dict) -> tuple[int, dict]:
            def _emit(action, result) -> None:
                ev = Event(kind="action_result", action=action.model_dump(mode="json") if hasattr(action, "model_dump") else action,
                           result=result.model_dump(mode="json") if hasattr(result, "model_dump") else result)
                write_msg({"type": "event", "event": ev.model_dump(mode="json")})
            return await _bridge_save(agent, _emit, body)

        _http_deps.save = _http_save
        bridge_server = await _start_http_api(agent, write_msg, settings, tap, _http_deps)
```

（`MobileDeps` 需在 server.py 顶部 import：`from .http_api import MobileDeps`——放函数内延迟 import 也行，与仓内 `.httpserver` 延迟 import 惯例一致则放 `_start_http_api` 内部时需同时在该处 import；本计划选择顶部 `from .http_api import MobileDeps`）

关闭清理：`bridge_server.close()`（:1146-1147）改为：

```python
            if bridge_server is not None:
                bridge_server.cleanup()  # aiohttp AppRunner
```

注意：`tap` 变量此时还不存在（Task 9 才重绑 write_msg）。本任务在 serve_async 开头（`ai_loop = asyncio.get_running_loop()` 之后）先加：

```python
    tap = EventTap(write_msg)  # 事件分接头：stdio 照发 + SSE 广播（Task 9 起替换 write_msg）
```

并在顶部 import `from .http_api import EventTap, MobileDeps`。**本任务先不重绑 write_msg**（保持 stdio 行为零变化，Task 9 统一切换），SSE 广播在本阶段测试里通过手动 `tap.publish` 验证。

- [ ] **Step 6: 删除旧文件 + 全量测试**

```bash
git rm sidecar/src/yibao_brain/httpserver.py sidecar/tests/test_httpserver.py
cd sidecar && uv run pytest -x -q
```

Expected: 全绿（httpserver 的测试职责已由 test_http_api.py/test_bridge.py 接管；若 test_server.py 有引用 `_start_bridge`/`httpserver` 的残留断言，按新行为修——预期没有，:914 只有调用点已改）

- [ ] **Step 7: 提交**

```bash
git add -A sidecar/
git commit -m "refactor(server): 扩展桥迁移 aiohttp，httpserver.py 退役；移动 API 骨架接线"
```

---

### Task 5: /v1/chat + /v1/interrupt（surface=mobile 的 run 受理）

**Files:**
- Modify: `sidecar/src/yibao_brain/server.py`（提取 `_schedule_run`；加 `_submit_run`/`_interrupt_mobile`；dispatch 分支改用 `_schedule_run`）
- Modify: `sidecar/src/yibao_brain/http_api.py`（两路由）
- Test: `sidecar/tests/test_http_api.py`（fake deps 路由级）+ `sidecar/tests/test_server.py`（serve_async 级 1 个）

**Interfaces:**
- Consumes: Task 3 `MobileDeps`（`submit_run`/`interrupt` 字段）
- Produces: serve_async 闭包 `_submit_run(text: str, conversation_id: str) -> dict`（返回 `{"ok": True, "run_id": str, "conversation_id": str}`）、`_interrupt_mobile() -> bool`；`_schedule_run(surface, rid, start)`（run/voice_start/手机共用受理尾巴）。Task 9 总装消费。

- [ ] **Step 1: 写失败测试（路由级，fake deps）**

追加到 `sidecar/tests/test_http_api.py`：

```python
def test_v1_chat_and_interrupt_routes():
    async def main():
        calls = []

        def submit_run(text, conversation_id):
            calls.append((text, conversation_id))
            return {"ok": True, "run_id": "mob_1", "conversation_id": conversation_id}

        def interrupt():
            return True

        deps = MobileDeps(submit_run=submit_run, interrupt=interrupt)
        app = build_app(bridge_token="btok", mobile_token="mtok",
                        tap=EventTap(lambda m: None), deps=deps)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            r = await client.post("/v1/chat", headers={"X-Yibao-Token": "mtok"},
                                  json={"text": "你好", "conversation_id": "c1"})
            assert r.status == 200
            assert await r.json() == {"ok": True, "run_id": "mob_1", "conversation_id": "c1"}
            assert calls == [("你好", "c1")]
            r = await client.post("/v1/chat", headers={"X-Yibao-Token": "mtok"}, json={"text": "  "})
            assert r.status == 400
            r = await client.post("/v1/interrupt", headers={"X-Yibao-Token": "mtok"}, json={})
            assert r.status == 200 and (await r.json()) == {"ok": True, "interrupted": True}
        finally:
            await client.close()

    asyncio.run(main())
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd sidecar && uv run pytest tests/test_http_api.py -v -k chat`
Expected: FAIL（404——路由未注册）

- [ ] **Step 3: http_api.py 实现两路由**

`build_app` 内 `save` 处理器后加：

```python
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
```

路由注册区加：

```python
    app.router.add_post("/v1/chat", v1_chat)
    app.router.add_post("/v1/interrupt", v1_interrupt)
```

Run: `cd sidecar && uv run pytest tests/test_http_api.py -v -k chat`
Expected: PASS

- [ ] **Step 4: serve_async——提取 _schedule_run + 两个闭包**

`sidecar/src/yibao_brain/server.py`：

(a) 在 `_preempt_if_same_surface`（:1062）定义之后加：

```python
    def _schedule_run(surface: str, rid, start) -> None:
        """受理尾巴（run/voice_start/手机 chat 共用）：同 surface 抢占 + 跨 surface 链式排队。"""
        _preempt_if_same_surface(surface)
        prev = run_state["task"]
        run_state["surface"] = surface  # 受理即记录：下次 dispatch 判断同/跨 surface 无调度竞态
        run_state["task"] = asyncio.ensure_future(
            _chain_start(prev, start, run_state["preempt_gen"]))
```

(b) dispatch 的 run/voice_start 分支（`_preempt_if_same_surface(surface)` 三行 + 末尾 `run_state["task"] = asyncio.ensure_future(...)`）改为调 `_schedule_run`（分支内构造 `start` 后调用，voice_start 分支同理）：

```python
            if rtype == "run":
                text, rid = msg.get("text", ""), msg.get("id")
                # 截图唤起：新鲜（<60s）的屏幕描述注入本次 run，一次性消费
                ctx_text = _consume_invoke_context(invoke_ctx)
                if ctx_text:
                    text = f"[屏幕上下文] {ctx_text}\n\n{text}"
                start = lambda c, t=text, r=rid, s=surface, ci=conversation_id: _drive_run(t, r, c, s, ci)
                print(f"[yibao] run 受理 rid={rid} surface={surface} conv={conversation_id}：{text[:30]!r}", file=sys.stderr)
                _schedule_run(surface, rid, start)
            elif voice is not None:
                rid = msg.get("id")
                cont = bool(msg.get("continuous"))
                start = lambda c, r=rid, s=surface, ci=conversation_id, ct=cont: _drive_voice_start(r, c, s, ci, ct)
                print(f"[yibao] voice_start 受理 rid={rid} surface={surface} conv={conversation_id} continuous={cont}", file=sys.stderr)
                _schedule_run(surface, rid, start)
            else:
                continue
```

（分支顶部的 `_preempt_if_same_surface(surface)` / `prev = ...` / `run_state["surface"] = surface` 三行删除——已收进 `_schedule_run`。注意 print 里不要引入未绑定名，直接沿用原 print 文案。）

(c) `_schedule_run` 之后加两个闭包：

```python
    _MOB_SEQ = itertools.count(1)

    def _submit_run(text: str, conversation_id: str) -> dict:
        """手机 /v1/chat 受理：surface=mobile（不抢桌宠，桌宠也不抢手机）。"""
        rid = f"mob_{next(_MOB_SEQ)}"
        ctx_text = _consume_invoke_context(invoke_ctx)
        if ctx_text:
            text = f"[屏幕上下文] {ctx_text}\n\n{text}"
        start = lambda c, t=text, r=rid, ci=conversation_id: _drive_run(t, r, c, "mobile", ci)
        print(f"[yibao] run 受理 rid={rid} surface=mobile conv={conversation_id}：{text[:30]!r}", file=sys.stderr)
        _schedule_run("mobile", rid, start)
        return {"ok": True, "run_id": rid, "conversation_id": conversation_id}

    def _interrupt_mobile() -> bool:
        """只打断 mobile surface 的 run。壳 interrupt 是「全都停」；手机不该误伤桌面对话。"""
        if run_state["surface"] == "mobile" and run_state["cancel"] is not None:
            _preempt_current()
            return True
        return False
```

(d) Task 4 的 deps 组装处（主循环前）追加：

```python
        _http_deps.submit_run = _submit_run
        _http_deps.interrupt = _interrupt_mobile
```

- [ ] **Step 5: serve_async 级测试（真排队/打断语义）**

追加到 `sidecar/tests/test_server.py`（沿用文件内 make_reader/`_TwoStepProvider` 模式；该文件已有 serve_async 大量用例可参照格式）：

```python
def test_mobile_submit_run_uses_mobile_surface_and_interrupt_scoped(tmp_path):
    """serve_async 内 /v1/chat 走 mobile surface：受理事件带 surface=mobile；
    _interrupt_mobile 只在当前 surface=mobile 时打断。"""
    from types import SimpleNamespace

    srv = None

    async def main():
        nonlocal srv
        out = []
        # 直接驱动 serve_async 太重；此处借 http_enabled 走真 HTTP（端口 19862 避冲突）
        import os
        os.environ["YIBAO_HTTP_PORT"] = "19862"
        provider = FakeProvider(text="手机你好")
        import yibao_brain.server as S

        orig_load = S.load_settings
        S.load_settings = lambda: {"http.token": "btok", "http.mobile_token": "mtok"}
        try:
            serve_task = asyncio.ensure_future(S.serve_async(
                _held_reader()[0], lambda m: out.append(m), use_real=False,
                db_path=str(tmp_path / "m.db"), provider=provider, http_enabled=True))
            await asyncio.sleep(0.4)  # 等服务起
            import aiohttp

            async with aiohttp.ClientSession() as sess:
                async with sess.post("http://127.0.0.1:19862/v1/chat",
                                     headers={"X-Yibao-Token": "mtok"},
                                     json={"text": "你好", "conversation_id": "c9"}) as r:
                    body = await r.json()
                    assert r.status == 200 and body["run_id"].startswith("mob_")
            await asyncio.sleep(0.3)
            surfaces = [m.get("surface") for m in out if m.get("type") == "event"]
            assert "mobile" in surfaces
            kinds = [m["event"]["kind"] for m in out if m.get("type") == "event" and m.get("surface") == "mobile"]
            assert "final_reply" in kinds or "final_reply_chunk" in kinds
            assert any(m.get("type") == "run_done" for m in out)
        finally:
            S.load_settings = orig_load
            os.environ.pop("YIBAO_HTTP_PORT", None)
            _held_reader_done()
            await asyncio.wait_for(serve_task, 5)

    asyncio.run(main())
```

同文件加 held-reader helper（模块级，末尾 None 语义与 make_reader 相同但由测试控制结束时机）：

```python
_HELD = {"done": False, "reader": None}


def _held_reader():
    _HELD["done"] = False

    def _r():
        import time as _t
        while not _HELD["done"]:
            _t.sleep(0.01)
        return None

    _HELD["reader"] = _r
    return _r, None


def _held_reader_done():
    _HELD["done"] = True
```

Run: `cd sidecar && uv run pytest tests/test_server.py -v -k mobile_submit`
Expected: PASS（先按 TDD 顺序：本测试在 Step 4 完成前跑应为红）

- [ ] **Step 6: 全量回归 + 提交**

```bash
cd sidecar && uv run pytest -x -q
git add -A sidecar/
git commit -m "feat(server): /v1/chat 与 /v1/interrupt——mobile surface 受理与域内打断"
```

---

### Task 6: /v1/events SSE（流式帧 + 心跳 + Last-Event-ID 补发）

**Files:**
- Modify: `sidecar/src/yibao_brain/http_api.py`
- Test: `sidecar/tests/test_http_api.py`（追加）

**Interfaces:**
- Consumes: Task 1 `EventTap.subscribe/unsubscribe/replay`、Task 3 auth（token 走 query）
- Produces: `GET /v1/events` SSE 端点。帧格式 `id: <seq>\nevent: <kind>\ndata: <json>\n\n`；心跳 `: ping\n\n`；响应头含 `X-Accel-Buffering: no`。

- [ ] **Step 1: 写失败测试**

追加到 `sidecar/tests/test_http_api.py`：

```python
def test_v1_events_streams_frames_and_replays():
    async def main():
        tap = EventTap(lambda m: None)
        app = build_app(bridge_token="btok", mobile_token="mtok", tap=tap)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            resp = await client.get("/v1/events", params={"token": "mtok"})
            assert resp.status == 200
            assert resp.headers["Content-Type"].startswith("text/event-stream")
            tap.publish("chunk", {"text": "你好"})  # 已连接后发布
            line = await asyncio.wait_for(resp.content.readline(), 2)
            assert line == b"id: 1\n"
            await resp.close()  # 断开（EventSource 会自动重连）

            tap.publish("chunk", {"text": "断线期间"})  # 断线期间继续发布
            resp2 = await client.get("/v1/events", params={"token": "mtok"},
                                     headers={"Last-Event-ID": "1"})
            line2 = await asyncio.wait_for(resp2.content.readline(), 2)
            assert line2 == b"id: 2\n"  # 从断点补发
            await resp2.close()
        finally:
            await client.close()

    asyncio.run(main())


def test_v1_events_requires_token():
    async def main():
        app = build_app(bridge_token="btok", mobile_token="mtok", tap=EventTap(lambda m: None))
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            r = await client.get("/v1/events")  # 无 token
            assert r.status == 401
        finally:
            await client.close()

    asyncio.run(main())
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd sidecar && uv run pytest tests/test_http_api.py -v -k events`
Expected: FAIL（404）

- [ ] **Step 3: 实现 SSE 端点**

`sidecar/src/yibao_brain/http_api.py` 顶部加常量：

```python
_HEARTBEAT_S = 30.0  # SSE 心跳：连接保活 + 让死连接尽快暴露


def _sse_frame(seq: int, event: str, data: str) -> bytes:
    return f"id: {seq}\nevent: {event}\ndata: {data}\n\n".encode()
```

`build_app` 内加处理器与路由：

```python
    async def v1_events(request):
        """SSE 事件流：先补发 Last-Event-ID 之后的缓冲帧，再实时订阅。
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
            for seq, event, data in tap.replay(last_seq):
                await resp.write(_sse_frame(seq, event, data))
            q = tap.subscribe()
            try:
                while True:
                    try:
                        seq, event, data = await asyncio.wait_for(q.get(), timeout=_HEARTBEAT_S)
                        await resp.write(_sse_frame(seq, event, data))
                    except asyncio.TimeoutError:
                        await resp.write(b": ping\n\n")
            finally:
                tap.unsubscribe(q)
        except (ConnectionResetError, asyncio.CancelledError):
            pass  # 客户端断开 / 应用关闭：正常收尾
        return resp
```

```python
    app.router.add_get("/v1/events", v1_events)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd sidecar && uv run pytest tests/test_http_api.py -v`
Expected: 全 PASS

- [ ] **Step 5: 提交**

```bash
git add sidecar/src/yibao_brain/http_api.py sidecar/tests/test_http_api.py
git commit -m "feat(server): /v1/events SSE 流式端点（心跳+Last-Event-ID 断点补发）"
```

---

### Task 7: 审批闭环（confirm_meta + /v1/confirm + /v1/state）

**Files:**
- Modify: `sidecar/src/yibao_brain/server.py`（batch_confirmer 登记 meta；`_confirm_mobile`/`_mobile_state` 闭包）
- Modify: `sidecar/src/yibao_brain/http_api.py`（两路由）
- Test: `sidecar/tests/test_http_api.py`（fake 路由级）

**Interfaces:**
- Consumes: Task 3 `MobileDeps`（`confirm`/`state` 字段）、`pending_confirms`/`early_answers`（serve_async 内部）
- Produces: `MobileDeps.confirm: (cid: str, approved: bool, remember: bool) -> bool`、`MobileDeps.state: () -> dict`（形状 `{"running": {"surface": str} | None, "pending": [{"id", "skill_id", "summary", "risk", "created_at"}]}`）。注意：待审批通知复用现有 `confirmation_needed` 事件（loop 在调 confirmer 前已 emit，经 Task 9 的 tap 自动进 SSE）——**不新增事件 kind**，spec §4.3 的 `confirm_request` 即它。

- [ ] **Step 1: 写失败测试（fake deps 路由级）**

追加到 `sidecar/tests/test_http_api.py`：

```python
def test_v1_confirm_and_state_routes():
    async def main():
        def confirm(cid, approved, remember):
            return cid != "already-done"

        def state():
            return {"running": {"surface": "mobile"},
                    "pending": [{"id": "pa_1", "skill_id": "danger", "summary": "rm -rf", "risk": 3, "created_at": 1}]}

        deps = MobileDeps(confirm=confirm, state=state)
        app = build_app(bridge_token="btok", mobile_token="mtok",
                        tap=EventTap(lambda m: None), deps=deps)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            r = await client.post("/v1/confirm", headers={"X-Yibao-Token": "mtok"},
                                  json={"id": "pa_1", "approved": True, "remember": False})
            assert r.status == 200
            r = await client.post("/v1/confirm", headers={"X-Yibao-Token": "mtok"},
                                  json={"id": "already-done", "approved": True})
            assert r.status == 404  # 已处理/未知
            r = await client.post("/v1/confirm", headers={"X-Yibao-Token": "mtok"}, json={"id": ""})
            assert r.status == 400
            r = await client.get("/v1/state", headers={"X-Yibao-Token": "mtok"})
            body = await r.json()
            assert body["running"] == {"surface": "mobile"}
            assert body["pending"][0]["id"] == "pa_1"
        finally:
            await client.close()

    asyncio.run(main())
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd sidein sidecar 2>/dev/null; cd sidecar && uv run pytest tests/test_http_api.py -v -k "confirm or state"`
Expected: FAIL（404）

- [ ] **Step 3: http_api.py 实现两路由**

```python
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
```

```python
    app.router.add_post("/v1/confirm", v1_confirm)
    app.router.add_get("/v1/state", v1_state)
```

- [ ] **Step 4: server.py——confirm_meta + 两闭包**

(a) serve_async 开头（`pending_confirms` 声明处，:562-563 旁）加：

```python
    confirm_meta: dict[str, dict] = {}  # cid -> {skill_id, summary, risk, created_at}：手机 /v1/state 待批列表
    _confirm_done: deque[str] = deque(maxlen=100)  # 已处理确认（防手机重复点击 404）
```

（顶部 import 补 `from collections import deque`）

(b) `batch_confirmer` 内 `fut = pending_confirms.setdefault(cid, ai_loop.create_future())` 之后登记；`finally` 块 `del pending_confirms[cid]` 旁清理：

```python
            fut = pending_confirms.setdefault(cid, ai_loop.create_future())
            confirm_meta[cid] = {
                "skill_id": skill_id,
                "summary": str(getattr(action, "params", "") or "")[:120] or skill_id,
                "risk": int(getattr(getattr(action, "risk", None), "value", getattr(action, "risk", 0)) or 0),
                "created_at": int(time.time()),
            }
```

```python
                if pending_confirms.get(cid) is fut:
                    del pending_confirms[cid]
                confirm_meta.pop(cid, None)
```

(c) `_interrupt_mobile` 之后加两闭包：

```python
    def _confirm_mobile(cid: str, approved: bool, remember: bool) -> bool:
        """与壳 confirm_batch 同路径：兑现 future；confirmer 未注册（SSE 事件先到一步）
        存 early_answers 待兑现。重复点击 → False（404）。"""
        if cid in _confirm_done:
            return False
        fut = pending_confirms.get(cid)
        if fut is not None and not fut.done():
            fut.set_result((approved, remember))
        else:
            early_answers[cid] = (approved, remember)
        _confirm_done.append(cid)
        return True

    def _mobile_state() -> dict:
        task = run_state["task"]
        running = {"surface": run_state["surface"]} if (task is not None and not task.done()) else None
        return {"running": running, "pending": [{"id": cid, **meta} for cid, meta in confirm_meta.items()]}
```

(d) deps 组装处追加：

```python
        _http_deps.confirm = _confirm_mobile
        _http_deps.state = _mobile_state
```

- [ ] **Step 5: 全量测试 + 提交**

Run: `cd sidecar && uv run pytest -x -q`
Expected: 全绿（test_server.py 现有确认用例覆盖 confirmer 路径未破坏）

```bash
git add -A sidecar/
git commit -m "feat(server): 远程审批闭环——confirm_meta 登记 + /v1/confirm + /v1/state"
```

---

### Task 8: /v1/push/register（推送设备登记）

**Files:**
- Modify: `sidecar/src/yibao_brain/server.py`（`_register_push` 闭包）
- Modify: `sidecar/src/yibao_brain/http_api.py`（一路由）
- Test: `sidecar/tests/test_http_api.py`（追加）

**Interfaces:**
- Consumes: Task 4 已加的 settings 键 `push.devices`
- Produces: `MobileDeps.register_push: (registration_id: str, platform: str) -> None`；设备存 settings `push.devices`（同 registration_id 覆盖更新，防重复堆积）。

- [ ] **Step 1: 写失败测试**

追加到 `sidecar/tests/test_http_api.py`：

```python
def test_v1_push_register_route():
    async def main():
        saved = []

        def register_push(rid, platform):
            saved.append((rid, platform))

        deps = MobileDeps(register_push=register_push)
        app = build_app(bridge_token="btok", mobile_token="mtok",
                        tap=EventTap(lambda m: None), deps=deps)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            r = await client.post("/v1/push/register", headers={"X-Yibao-Token": "mtok"},
                                  json={"registration_id": "jp-abc", "platform": "ios"})
            assert r.status == 200 and (await r.json())["ok"] is True
            assert saved == [("jp-abc", "ios")]
            r = await client.post("/v1/push/register", headers={"X-Yibao-Token": "mtok"},
                                  json={"registration_id": "", "platform": "ios"})
            assert r.status == 400
        finally:
            await client.close()

    asyncio.run(main())
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd sidecar && uv run pytest tests/test_http_api.py -v -k push`
Expected: FAIL（404）

- [ ] **Step 3: 实现**

`http_api.py` 路由：

```python
    async def v1_push_register(request):
        if deps.register_push is None:
            return web.json_response({"ok": False, "error": "not wired"}, status=503)
        body = await request.json()
        rid = str(body.get("registration_id") or "").strip()
        if not rid:
            return web.json_response({"ok": False, "error": "registration_id 为空"}, status=400)
        deps.register_push(rid, str(body.get("platform") or ""))
        return web.json_response({"ok": True})
```

```python
    app.router.add_post("/v1/push/register", v1_push_register)
```

`server.py` `_mobile_state` 之后加闭包（deps 组装处接 `_http_deps.register_push = _register_push`）：

```python
    def _register_push(registration_id: str, platform: str) -> None:
        """推送设备登记（P4 极光发送消费）。同 registration_id 覆盖，防重复堆积。"""
        devices = [d for d in (settings.get("push.devices") or [])
                   if d.get("registration_id") != registration_id]
        devices.append({"registration_id": registration_id, "platform": platform, "added_at": int(time.time())})
        settings["push.devices"] = devices
        save_settings({"push.devices": devices})
```

- [ ] **Step 4: 跑测试 + 提交**

Run: `cd sidecar && uv run pytest -x -q`
Expected: 全绿

```bash
git add -A sidecar/
git commit -m "feat(server): /v1/push/register 推送设备登记"
```

---

### Task 9: 总装——write_msg 重绑 EventTap + 端到端测试 + spec 微修

**Files:**
- Modify: `sidecar/src/yibao_brain/server.py`（serve_async 开头重绑）
- Test: `sidecar/tests/test_server.py`（端到端 1 个：SSE 收到 chat 流式帧）
- Modify: `docs/superpowers/specs/2026-08-14-mobile-companion-design.md`（两处微修，见 Step 4）

**Interfaces:**
- Consumes: Task 4 的 `tap = EventTap(write_msg)`（只建未绑）
- Produces: 完整闭环——所有 stdio 事件自动进 SSE；`/v1/events` 收到 `final_reply_chunk`/`run_done`/`confirmation_needed`。

- [ ] **Step 1: 写失败测试（端到端：SSE 收到 mobile run 的流式帧）**

追加到 `sidecar/tests/test_server.py`：

```python
def test_mobile_end_to_end_sse_receives_stream(tmp_path):
    """/v1/chat → 经 EventTap → /v1/events 收到 final_reply(_chunk) 与 run_done 帧。"""

    async def main():
        import os

        os.environ["YIBAO_HTTP_PORT"] = "19863"
        out = []
        import yibao_brain.server as S

        orig_load = S.load_settings
        S.load_settings = lambda: {"http.token": "btok", "http.mobile_token": "mtok"}
        try:
            serve_task = asyncio.ensure_future(S.serve_async(
                _held_reader()[0], lambda m: out.append(m), use_real=False,
                db_path=str(tmp_path / "e2e.db"), provider=FakeProvider(text="端到端回复"),
                http_enabled=True))
            await asyncio.sleep(0.4)
            import aiohttp

            async with aiohttp.ClientSession() as sess:
                events = await sess.get("http://127.0.0.1:19863/v1/events", params={"token": "mtok"})
                chat = await sess.post("http://127.0.0.1:19863/v1/chat",
                                       headers={"X-Yibao-Token": "mtok"}, json={"text": "你好"})
                assert chat.status == 200
                buf = b""
                deadline = time.monotonic() + 5
                while b"run_done" not in buf and time.monotonic() < deadline:
                    chunk = await asyncio.wait_for(events.content.read(64), 2)
                    buf += chunk
                assert b"final_reply" in buf  # 流式回复帧（kind=final_reply 或 final_reply_chunk）
                assert b"run_done" in buf
                events.close()
        finally:
            S.load_settings = orig_load
            os.environ.pop("YIBAO_HTTP_PORT", None)
            _held_reader_done()
            await asyncio.wait_for(serve_task, 5)

    asyncio.run(main())
```

Run: `cd sidecar && uv run pytest tests/test_server.py -v -k end_to_end_sse`
Expected: FAIL（SSE 收不到事件帧——tap 未重绑，write_msg 不经分接头）

- [ ] **Step 2: 重绑 write_msg**

`serve_async` 开头（`tap = EventTap(write_msg)` 行）改为：

```python
    tap = EventTap(write_msg)
    write_msg = tap  # 重绑：本函数内所有闭包（_stream_agent/dispatcher/reader 等）经分接头
    # ——stdio 输出字节不变（tap 透传），event/run_done 额外复制进 SSE 环形缓冲
```

注意重绑位置必须在 `ProactiveDispatcher(...)`（:630）等任何闭包**构造之前**（放函数体第一行 `ai_loop = ...` 之后即可满足），闭包运行时按名字取到的是 tap。

Run: `cd sidecar && uv run pytest tests/test_server.py -v -k end_to_end_sse`
Expected: PASS

- [ ] **Step 3: 全量回归**

```bash
cd sidecar && uv run pytest -x -q
```
Expected: 全绿（stdio 协议字节不变——现有全部测试即回归证明；若有断言 write_msg 身份的测试，按 tap 透传语义修断言）

- [ ] **Step 4: spec 微修（实现与设计对齐）**

`docs/superpowers/specs/2026-08-14-mobile-companion-design.md`：
- §4.3 事件 kind 列表：`confirm_request` 改注「复用现有 `confirmation_needed` 事件，不新增 kind」
- §4.2 `/v1/state` 的 running 字段：`{surface, text, started_at}` 改为 `{surface}`（v1 不追文本/起始时间，YAGNI）

```bash
git add -A sidecar/ docs/superpowers/specs/2026-08-14-mobile-companion-design.md
git commit -m "feat(server): P1 总装——write_msg 经 EventTap 分发，SSE 端到端打通"
```

---

## 验收（P1 完成定义）

- [ ] `cd sidecar && uv run pytest -q` 全绿（原有用例零回归 + 新增 ~15 用例）
- [ ] 手工 curl 冒烟（`uv run yibao-brain` 起 sidecar 后）：
  - `curl -H "X-Yibao-Token: <mobile_token>" http://127.0.0.1:19527/v1/health` → `{"ok":true,...}`
  - `curl -N -H "X-Yibao-Token: <mobile_token>" http://127.0.0.1:19527/v1/events` 挂住 → 另一终端 POST `/v1/chat` → 看到流式 `id:/event:/data:` 帧
  - 扩展桥 `/save` 照常工作（浏览器扩展真机回归）

## P2–P5 计划

P2（VPS/Caddy/frp 基建 runbook）、P3（Capacitor 工程骨架+配对+对话）、P4（极光推送+深链+审批页）、P5（分享模板+桌面设置页+真机验收）各自独立成 plan，随阶段推进编写（仓内惯例：plans 按阶段分文件）。
