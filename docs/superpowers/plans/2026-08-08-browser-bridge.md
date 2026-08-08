# 浏览器扩展闭环 实施计划（存页/划词 → 素材库/选题看板）

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Chromium MV3 扩展一键把当前页/选中文字存进译宝素材库或选题看板，团子气泡回执——打通「浏览器 → 素材 → 选题 → 写稿召回」闭环的浏览器入口。

**Architecture:** 顶层 `extension/`（零构建纯 JS MV3：popup/右键/options）→ `POST 127.0.0.1:19527/save`（X-Yibao-Token 认证）→ sidecar 原生 asyncio 微 HTTP 桥（`httpserver.py` + `_start_bridge`）→ zimeiti quiet 直调（mat_save 兼容 url+text / 新 invoke_add_topic）→ action_result 事件 → App.vue 既有气泡（零改动）。

**Tech Stack:** Python asyncio（sidecar）、Chrome MV3 纯 JS、Vue3/TS（设置页一区块）、pytest。

**关联 spec：** `docs/superpowers/specs/2026-08-08-browser-bridge-design.md`

## Global Constraints

- **零新依赖**：HTTP 桥用原生 asyncio（不引 starlette/uvicorn/aiohttp）；扩展零构建（无 npm/bundler）。
- **回执同协议**：桥发 action_result 的 action.id 必须 `pa_http_<n>`（pa_ 前缀是 App.vue:515 气泡认领条件；不与壳侧 pa_<Date.now> 撞号）。
- **阻塞调用走 `_offload`**（LLM 摘要在 invoker.execute 内，必须挪线程池，防看门狗误杀）。
- **sidecar pytest 全绿（基线 831）；vue-tsc/vite build exit 0；cargo check 不动**（本特性不改 Rust）。
- **commit**：每任务一 commit，中文 scope（`feat(bridge): …`），仅 stage 本任务文件，不动 `.gitignore`，提交在 `feat/browser-bridge` 分支。
- 认证底线：只监听 127.0.0.1 + X-Yibao-Token 校验；token 自动生成持久化（settings `http.token`），不设为空。

## File Structure

**新建：**
- `sidecar/src/yibao_brain/httpserver.py`、`sidecar/tests/test_httpserver.py`
- `sidecar/tests/test_zimeiti_mat_save.py`、`sidecar/tests/test_bridge.py`
- `extension/manifest.json`、`extension/background.js`、`extension/shared.js`、`extension/popup.html`、`extension/popup.js`、`extension/options.html`、`extension/options.js`、`extension/README.md`

**改：**
- `plugins/zimeiti/tools/mat_save.py`（url+text 兼容）
- `plugins/zimeiti/api.toml`（invoke_add_topic quiet 条目）
- `sidecar/src/yibao_brain/config.py`（_SETTINGS_DEFAULTS 加 http.token；http_port()）
- `sidecar/src/yibao_brain/server.py`（_ensure_bridge_token/_make_bridge_route/_start_bridge + serve_async http_enabled 参数 + 挂载/关闭）
- `app/src/lib/brain.ts`（SettingsValues 加 "http.token"）
- `app/src/components/SettingsView.vue`（「浏览器扩展」区）

---

### Task 1: httpserver.py 微 HTTP（原生 asyncio）

**Files:**
- Create: `sidecar/src/yibao_brain/httpserver.py`
- Test: `sidecar/tests/test_httpserver.py`

**Interfaces:**
- Consumes: 无（纯 asyncio + json + sys）
- Produces: `async def serve(host: str, port: int, handler) -> asyncio.AbstractServer`；`handler` 签名 `async (method: str, path: str, headers: dict, body: dict) -> tuple[int, dict]`——Task 3 的 `_make_bridge_route` 实现此签名

- [ ] **Step 1: 写失败测试**

`sidecar/tests/test_httpserver.py`：

```python
"""httpserver 微 HTTP：ephemeral 端口真 socket 请求（同步测试 + asyncio.run，仓内无 pytest-asyncio）。"""
import asyncio
import json

from yibao_brain.httpserver import serve


def _run(coro):
    return asyncio.run(coro)  # 仿 test_server.py 的 _run_async 惯例


async def _request(port: int, raw: bytes) -> bytes:
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write(raw)
    await w.drain()
    data = await r.read(-1)
    w.close()
    return data


def test_options_preflight_204_with_cors():
    async def main():
        async def handler(m, p, h, b):
            return 200, {"ok": True}

        srv = await serve("127.0.0.1", 0, handler)
        port = srv.sockets[0].getsockname()[1]
        resp = await _request(port, b"OPTIONS /save HTTP/1.1\r\nHost: x\r\n\r\n")
        srv.close()
        head = resp.decode("latin-1")
        assert "204" in head.split("\r\n")[0]
        assert "Access-Control-Allow-Headers" in head
        assert "X-Yibao-Token" in head

    _run(main())


def test_post_json_passed_to_handler_and_json_response():
    seen = {}

    async def main():
        async def handler(m, p, h, b):
            seen.update({"method": m, "path": p, "token": h.get("x-yibao-token"), "body": b})
            return 200, {"ok": True, "echo": b.get("x")}

        srv = await serve("127.0.0.1", 0, handler)
        port = srv.sockets[0].getsockname()[1]
        body = json.dumps({"x": 42}).encode()
        raw = (b"POST /save HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
               b"X-Yibao-Token: t0\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
        resp = await _request(port, raw)
        srv.close()
        assert seen == {"method": "POST", "path": "/save", "token": "t0", "body": {"x": 42}}
        assert b'"ok":true' in resp.replace(b" ", b"")
        assert b'"echo":42' in resp.replace(b" ", b"")

    _run(main())


def test_bad_json_body_400():
    async def main():
        async def handler(m, p, h, b):
            return 200, {"ok": True}

        srv = await serve("127.0.0.1", 0, handler)
        port = srv.sockets[0].getsockname()[1]
        raw = b"POST /save HTTP/1.1\r\nHost: x\r\nContent-Length: 3\r\n\r\nnot"
        resp = await _request(port, raw)
        srv.close()
        assert "400" in resp.decode("latin-1").split("\r\n")[0]

    _run(main())


def test_post_without_content_length_gets_empty_body():
    seen = {}

    async def main():
        async def handler(m, p, h, b):
            seen["body"] = b
            return 200, {"ok": True}

        srv = await serve("127.0.0.1", 0, handler)
        port = srv.sockets[0].getsockname()[1]
        resp = await _request(port, b"POST /save HTTP/1.1\r\nHost: x\r\n\r\n")
        srv.close()
        assert seen["body"] == {}
        assert "200" in resp.decode("latin-1").split("\r\n")[0]

    _run(main())


def test_handler_exception_becomes_500():
    async def main():
        async def handler(m, p, h, b):
            raise RuntimeError("boom")

        srv = await serve("127.0.0.1", 0, handler)
        port = srv.sockets[0].getsockname()[1]
        resp = await _request(port, b"POST /save HTTP/1.1\r\nHost: x\r\nContent-Length: 2\r\n\r\n{}")
        srv.close()
        assert "500" in resp.decode("latin-1").split("\r\n")[0]

    _run(main())
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd sidecar && uv run pytest tests/test_httpserver.py -x -q`
Expected: FAIL（ModuleNotFoundError: yibao_brain.httpserver）

- [ ] **Step 3: 实现**

`sidecar/src/yibao_brain/httpserver.py`：

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd sidecar && uv run pytest tests/test_httpserver.py -x -q`
Expected: 5 PASS（测试用同步函数 + `asyncio.run` 驱动——仓内无 pytest-asyncio，仿 test_server.py 的 `_run_async` 惯例，不新增依赖）

- [ ] **Step 5: Commit**

```bash
git add sidecar/src/yibao_brain/httpserver.py sidecar/tests/test_httpserver.py sidecar/pyproject.toml
git commit -m "feat(bridge): httpserver 微 HTTP（原生 asyncio 零依赖，OPTIONS/POST JSON，ephemeral 可测）"
```

（pyproject.toml 未动则从 add 里去掉。）

---

### Task 2: mat_save url+text 兼容

**Files:**
- Modify: `plugins/zimeiti/tools/mat_save.py`（run :92-106 + schema 描述 :78-90）
- Test: `sidecar/tests/test_zimeiti_mat_save.py`（新建）

**Interfaces:**
- Consumes: 现有 `_fetch_text`/`_summarize`/materials 表
- Produces: mat_save 新语义——**url+text 同给：text 为正文、url 仅作来源（不调 _fetch_text）**；仅 url：抓正文（旧行为）；仅 text：直存（旧行为）。Task 3 桥 material 路径依赖「同给不重抓」

- [ ] **Step 1: 写失败测试**

`sidecar/tests/test_zimeiti_mat_save.py`：

```python
"""zimeiti.mat_save：url+text 同给不重抓（浏览器扩展链路：正文由扩展提取，url 仅作来源）。"""
import importlib.util
from pathlib import Path


def _load_mat_save():
    """按插件加载器同款方式按文件自包含加载 mat_save.py。"""
    path = Path(__file__).resolve().parents[2] / "plugins" / "zimeiti" / "tools" / "mat_save.py"
    spec = importlib.util.spec_from_file_location("zimeiti_mat_save", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeLlm:
    def chat(self, prompt):
        return '{"title": "测试标题", "summary": "测试摘要", "tags": ["a"]}'


class _FakeDb:
    def __init__(self):
        self.rows = []

    def insert(self, table, row):
        self.rows.append((table, dict(row)))
        return "rid-1"


class _FakeCtx:
    def __init__(self):
        self.llm = _FakeLlm()
        self.db = _FakeDb()


def _skill(mod):
    return mod.make_tools(None)[0]


def test_url_and_text_skips_fetch_and_keeps_source_url(monkeypatch):
    mod = _load_mat_save()
    monkeypatch.setattr(mod, "_fetch_text", lambda url: (_ for _ in ()).throw(AssertionError("不应重抓")))
    ctx = _FakeCtx()
    r = _skill(mod).run({"url": "https://example.com/a", "text": "页面正文"}, ctx)
    assert r.success, r.error
    table, row = ctx.db.rows[0]
    assert table == "materials"
    assert row["url"] == "https://example.com/a"   # 来源留住
    assert row["content"] == "页面正文"            # 正文用扩展给的
    assert row["kind"] == "link"


def test_url_only_fetches(monkeypatch):
    mod = _load_mat_save()
    calls = []
    monkeypatch.setattr(mod, "_fetch_text", lambda url: calls.append(url) or "抓到的正文")
    ctx = _FakeCtx()
    r = _skill(mod).run({"url": "https://example.com/b"}, ctx)
    assert r.success, r.error
    assert calls == ["https://example.com/b"]
    assert ctx.db.rows[0][1]["content"] == "抓到的正文"


def test_text_only_no_fetch(monkeypatch):
    mod = _load_mat_save()
    monkeypatch.setattr(mod, "_fetch_text", lambda url: (_ for _ in ()).throw(AssertionError("不应抓")))
    ctx = _FakeCtx()
    r = _skill(mod).run({"text": "纯文本"}, ctx)
    assert r.success, r.error
    assert ctx.db.rows[0][1]["kind"] == "note"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd sidecar && uv run pytest tests/test_zimeiti_mat_save.py -x -q`
Expected: FAIL（test_url_and_text 触发 AssertionError「不应重抓」——现实现同给也抓）

- [ ] **Step 3: 实现**

`plugins/zimeiti/tools/mat_save.py` `run` 方法（:92-106）的条件改为「仅 url 无 text 才抓」：

```python
    def run(self, params: dict, ctx: Any) -> ActionResult:
        url = str(params.get("url") or "").strip()
        text = str(params.get("text") or "").strip()
        if not url and not text:
            return ActionResult(success=False, error="url 和 text 至少给一个")
        kind = "link" if url else "note"
        if url and not text:
            # 仅链接：sidecar 抓正文（登录墙/SPA 由调用方改传 text 绕过）
            if not re.match(r"^https?://", url):
                return ActionResult(success=False, error=f"不是合法 http(s) 链接：{url}")
            try:
                text = _fetch_text(url)
            except Exception as e:
                return ActionResult(success=False, error=f"抓取失败：{e}")
            if not text:
                return ActionResult(success=False, error="抓到了页面但没提取出文字内容")
        # url+text 同给：text 为正文、url 仅作来源元数据（浏览器扩展链路，不重抓）
```

（其后 llm/_summarize/落库逻辑原样不动。）`openai_schema` 两个参数描述同步改为：

```python
                    "url": {"type": "string", "description": "网页链接（仅传它时抓正文；与 text 同给时仅作来源，不重抓）"},
                    "text": {"type": "string", "description": "直接存的文本内容（无 url 时必填；与 url 同给时为正文）"},
```

- [ ] **Step 4: 跑测试确认通过 + 全量回归**

Run: `cd sidecar && uv run pytest tests/test_zimeiti_mat_save.py -x -q && uv run pytest -q`
Expected: 新 3 PASS；全量 831+ 全绿

- [ ] **Step 5: Commit**

```bash
git add plugins/zimeiti/tools/mat_save.py sidecar/tests/test_zimeiti_mat_save.py
git commit -m "feat(bridge): mat_save url+text 同给不重抓（正文由调用方给，url 仅作来源）"
```

---

### Task 3: server 桥（_start_bridge + token + 路由 + serve_async 挂载）

**Files:**
- Modify: `sidecar/src/yibao_brain/config.py`（_SETTINGS_DEFAULTS 加 `"http.token": ""`；新增 `http_port()`，仿 config.py:31-166 的 env helper 先例）
- Modify: `sidecar/src/yibao_brain/server.py`（_ensure_bridge_token/_make_bridge_route/_start_bridge；serve_async 签名加 `http_enabled: bool | None = None`；reminder_task 后挂载；shutdown 关闭）
- Test: `sidecar/tests/test_bridge.py`（新建）

**Interfaces:**
- Consumes: Task 1 `serve(host, port, handler)`；Task 2 mat_save 新语义；`get_api`（plugins.py:406）；`save_settings`（config）；`ToolCall/Decision/Event`、`_offload`（server.py 内既有）
- Produces:
  - `_ensure_bridge_token(settings: dict) -> str`（空→生成 secrets.token_hex(16) + save_settings + 回填 settings）
  - `_make_bridge_route(agent, write_msg, token: str)` → async route handler（签名同 Task 1 Handler）
  - `http_port() -> int`（env `YIBAO_HTTP_PORT`，默认 19527）
  - serve_async 参数 `http_enabled: bool | None = None`（None → 取 use_real；测试默认关）

- [ ] **Step 1: 写失败测试**

`sidecar/tests/test_bridge.py`：

```python
"""浏览器扩展桥：token 确保 + 路由函数级测试（不起 socket；同步 + asyncio.run，仓内无 pytest-asyncio）。"""
import asyncio

from yibao_brain.ipc import Action, ActionResult, ToolCall
from yibao_brain.safety import Decision
from yibao_brain.server import _ensure_bridge_token, _make_bridge_route


def _run(coro):
    return asyncio.run(coro)


def test_ensure_bridge_token_generates_and_persists(monkeypatch):
    saved = {}
    monkeypatch.setattr("yibao_brain.server.save_settings", lambda v: saved.update(v))
    settings = {"http.token": ""}
    tok = _ensure_bridge_token(settings)
    assert len(tok) == 32 and settings["http.token"] == tok
    assert saved == {"http.token": tok}


def test_ensure_bridge_token_keeps_existing(monkeypatch):
    monkeypatch.setattr("yibao_brain.server.save_settings", lambda v: (_ for _ in ()).throw(AssertionError("不应再存")))
    settings = {"http.token": "abc123"}
    assert _ensure_bridge_token(settings) == "abc123"


class _FakeInvoker:
    def __init__(self, decision=Decision.AUTO, result=None):
        self.decision = decision
        self.result = result or ActionResult(success=True, data={"title": "存好了"})
        self.calls = []

    def propose(self, call):
        self.calls.append(("propose", call.skill_id, dict(call.params)))
        return Action(id=call.id, skill_id=call.skill_id)

    def decide(self, action):
        self.calls.append(("decide", action.skill_id))
        return self.decision

    def execute(self, action, params):
        self.calls.append(("execute", action.skill_id, dict(params)))
        return self.result


class _FakeAgent:
    def __init__(self, invoker):
        self.invoker = invoker


def _route(invoker, events):
    agent = _FakeAgent(invoker)
    return _make_bridge_route(agent, lambda m: None, "tok", emit=lambda e: events.append(e))


def test_route_wrong_token_401():
    async def main():
        route = _route(_FakeInvoker(), [])
        status, obj = await route("POST", "/save", {"x-yibao-token": "bad"}, {"text": "x"})
        assert status == 401 and obj["ok"] is False

    _run(main())


def test_route_health():
    async def main():
        route = _route(_FakeInvoker(), [])
        status, obj = await route("GET", "/health", {"x-yibao-token": "tok"}, {})
        assert status == 200 and obj["ok"] is True

    _run(main())


def test_route_material_executes_mat_save_and_emits_pa_http():
    async def main():
        events = []
        invoker = _FakeInvoker()
        route = _route(invoker, events)
        status, obj = await route("POST", "/save", {"x-yibao-token": "tok"},
                                  {"url": "https://a.com/x", "title": "标题", "text": "正文", "mode": "material"})
        assert status == 200 and obj == {"ok": True, "title": "存好了"}
        exe = [c for c in invoker.calls if c[0] == "execute"]
        assert exe and exe[0][1] == "zimeiti.mat_save"
        assert exe[0][2]["url"] == "https://a.com/x"
        assert "正文" in exe[0][2]["text"] and "标题" in exe[0][2]["text"]
        ev = events[0]
        assert ev["kind"] == "action_result"
        assert ev["action"]["id"].startswith("pa_http_")
        assert ev["action"]["skill_id"] == "zimeiti.mat_save"

    _run(main())


def test_route_topic_executes_add():
    async def main():
        invoker = _FakeInvoker()
        route = _route(invoker, [])
        status, obj = await route("POST", "/save", {"x-yibao-token": "tok"},
                                  {"url": "https://a.com/y", "title": "选题标题", "text": "正文", "mode": "topic"})
        assert status == 200
        exe = [c for c in invoker.calls if c[0] == "execute"]
        assert exe[0][1] == "zimeiti.add"
        assert exe[0][2]["title"] == "选题标题"
        assert exe[0][2]["source"] == "https://a.com/y"

    _run(main())


def test_route_empty_text_400_and_bad_mode_400_and_confirm_403():
    async def main():
        route = _route(_FakeInvoker(), [])
        status, _ = await route("POST", "/save", {"x-yibao-token": "tok"}, {"text": ""})
        assert status == 400
        status, _ = await route("POST", "/save", {"x-yibao-token": "tok"}, {"text": "x", "mode": "ghost"})
        assert status == 400
        route2 = _route(_FakeInvoker(decision=Decision.CONFIRM), [])
        status, obj = await route2("POST", "/save", {"x-yibao-token": "tok"}, {"text": "x", "mode": "material"})
        assert status == 403 and obj["ok"] is False

    _run(main())
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd sidecar && uv run pytest tests/test_bridge.py -x -q`
Expected: FAIL（cannot import name '_ensure_bridge_token'）

- [ ] **Step 3: 实现**

① `sidecar/src/yibao_brain/config.py`：`_SETTINGS_DEFAULTS` 加一行（位置随既有键分组）：

```python
    "http.token": "",  # 浏览器扩展桥共享 token（空 = 启动时生成并持久化）
```

并加（仿既有 env helper）：

```python
def http_port() -> int:
    """浏览器扩展桥监听端口（只绑 127.0.0.1）。"""
    try:
        return int(os.environ.get("YIBAO_HTTP_PORT", "19527"))
    except ValueError:
        return 19527
```

② `sidecar/src/yibao_brain/server.py`（模块级，`handle_panel_action` 之后加）：

```python
# ---------- 浏览器扩展桥（127.0.0.1 微 HTTP → zimeiti quiet 直调）----------


def _ensure_bridge_token(settings: dict) -> str:
    """扩展桥共享 token：空则生成并持久化（save_settings 只落已知键，http.token 已在默认表）。"""
    tok = str(settings.get("http.token") or "")
    if not tok:
        import secrets

        tok = secrets.token_hex(16)
        save_settings({"http.token": tok})
        settings["http.token"] = tok
    return tok


def _make_bridge_route(agent: AgentLoop, write_msg: WriteMsg, token: str, *, emit=None):
    """造桥路由（httpserver Handler 签名）：/save → zimeiti quiet 直调；回执与 panel_action 同协议（pa_ 前缀）。
    emit 注入便于测试；缺省直接 write_msg 发 event（不带 surface——壳侧分流过滤放行，与唤起条回执同行为）。
    """
    counter = itertools.count(1)

    def _emit(action, result) -> None:
        ev = Event(kind="action_result", action=action, result=result)
        if emit is not None:
            emit(ev.model_dump(mode="json"))
        else:
            write_msg({"type": "event", "event": ev.model_dump(mode="json")})

    async def _route(method: str, path: str, headers: dict, body: dict) -> tuple[int, dict]:
        if headers.get("x-yibao-token") != token:
            return 401, {"ok": False, "error": "token 不对"}
        if method == "GET" and path == "/health":
            return 200, {"ok": True, "service": "yibao-bridge"}
        if method != "POST" or path != "/save":
            return 404, {"ok": False, "error": "not found"}
        url = str(body.get("url") or "").strip()
        title = str(body.get("title") or "").strip()[:200]
        text = str(body.get("text") or "").strip()[:20000]
        mode = str(body.get("mode") or "material")
        if not text:
            return 400, {"ok": False, "error": "text 为空"}
        if mode == "material":
            api_name = "zimeiti.invoke_mat_save"
            params = {"url": url, "text": f"{title}\n\n{text}" if title else text}
        elif mode == "topic":
            api_name = "zimeiti.invoke_add_topic"
            params = {"title": title or text[:30], "source": url or "浏览器扩展"}
        else:
            return 400, {"ok": False, "error": f"未知 mode：{mode}"}
        api = get_api(api_name)
        if api is None or not api.direct:
            return 500, {"ok": False, "error": f"方法不可用：{api_name}"}
        rid = f"http_{next(counter)}"
        action = agent.invoker.propose(ToolCall(id=f"pa_{rid}", skill_id=api.handler, params=params))
        action.id = f"pa_{rid}"  # 壳侧靠 pa_ 前缀认领回执（与 panel_action 同协议）
        if api.risk is not None:
            action.risk = max(action.risk, api.risk)
        decision = agent.invoker.decide(action)
        if decision != Decision.AUTO:
            return 403, {"ok": False, "error": "策略要求确认或禁止（桥场景无确认通道），未执行"}
        result = await _offload(agent.invoker.execute, action, params)
        _emit(action, result)
        if not result.success:
            return 500, {"ok": False, "error": result.error or "执行失败"}
        return 200, {"ok": True, "title": (result.data or {}).get("title", title)}

    return _route


async def _start_bridge(agent: AgentLoop, write_msg: WriteMsg, settings: dict) -> "asyncio.AbstractServer | None":
    """起浏览器扩展桥；端口占用/任何失败 → stderr + None（不拖垮大脑）。"""
    from .httpserver import serve

    token = _ensure_bridge_token(settings)
    try:
        srv = await serve("127.0.0.1", http_port(), _make_bridge_route(agent, write_msg, token))
        print(f"[yibao] 浏览器扩展桥已监听 127.0.0.1:{http_port()}", file=sys.stderr)
        return srv
    except OSError as e:
        print(f"[yibao] 浏览器扩展桥启动失败（{e}，已禁用）", file=sys.stderr)
        return None
```

（文件顶部 import 区补：`itertools`、`from .config import http_port`（并入既有 config import 行）；`AgentLoop`/`WriteMsg` 若无注解需要可省。）

③ serve_async 签名加 `http_enabled: bool | None = None`；`reminder_task` 之后（server.py:786-788 区域）加：

```python
    if http_enabled is None:
        http_enabled = use_real  # 测试默认关；生产（use_real=True）默认开
    bridge_server = None
    if http_enabled:
        bridge_server = await _start_bridge(agent, write_msg, settings)
```

shutdown 处（找到 distiller_task/reminder_task cancel 的位置）加：

```python
    if bridge_server is not None:
        bridge_server.close()
```

- [ ] **Step 4: 跑测试确认通过 + 全量回归**

Run: `cd sidecar && uv run pytest tests/test_bridge.py -x -q && uv run pytest -q`
Expected: 新 7 PASS；全量 831+ 全绿

- [ ] **Step 5: Commit**

```bash
git add sidecar/src/yibao_brain/config.py sidecar/src/yibao_brain/server.py sidecar/tests/test_bridge.py
git commit -m "feat(bridge): server 桥——_start_bridge + token 生成持久化 + /save 路由（quiet 直调同协议回执）"
```

---

### Task 4: zimeiti.invoke_add_topic quiet 条目

**Files:**
- Modify: `plugins/zimeiti/api.toml`（invoke_mat_save 条目后追加）
- Test: `sidecar/tests/test_bridge.py`（追加一个解析测试）

**Interfaces:**
- Consumes: E 的 `quiet` 机制（plugins.py ApiMethod.quiet + handle_panel_action 抑制）
- Produces: api 方法全名 `zimeiti.invoke_add_topic`（direct+quiet）；Task 3 路由 topic 路径依赖它

- [ ] **Step 1: 写失败测试**

`sidecar/tests/test_bridge.py` 追加：

```python
def test_zimeiti_api_toml_has_quiet_bridge_entries():
    """真 api.toml：invoke_mat_save / invoke_add_topic 都是 direct+quiet（桥回执不发 panel 事件）。"""
    from pathlib import Path

    from yibao_brain import plugins
    from yibao_brain.skills import Skill, SkillRegistry

    class _Dummy(Skill):
        description = "dummy"

        def run(self, params, ctx):
            raise NotImplementedError

    reg = SkillRegistry()
    for sid in ("zimeiti.mat_save", "zimeiti.add"):
        d = _Dummy()
        d.id = sid
        reg.register(d, plugin="zimeiti")
    api_path = Path(__file__).resolve().parents[2] / "plugins" / "zimeiti" / "api.toml"
    plugins._load_api("zimeiti", api_path, reg)
    try:
        for name in ("zimeiti.invoke_mat_save", "zimeiti.invoke_add_topic"):
            m = plugins.get_api(name)
            assert m is not None, name
            assert m.direct is True and m.quiet is True, name
    finally:
        plugins._API.pop("zimeiti.invoke_mat_save", None)
        plugins._API.pop("zimeiti.invoke_add_topic", None)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd sidecar && uv run pytest tests/test_bridge.py -k api_toml -x -q`
Expected: FAIL（`zimeiti.invoke_add_topic` 不存在 → assert m is not None 失败）

- [ ] **Step 3: 实现**

`plugins/zimeiti/api.toml` 在 `invoke_mat_save` 条目后追加：

```toml
# 浏览器扩展「存为选题」：标题上看板；quiet 不弹看板面板，回执由壳侧气泡给
[[method]]
name = "invoke_add_topic"
handler = "zimeiti.add"
direct = true
quiet = true
```

- [ ] **Step 4: 跑测试确认通过 + 全量回归**

Run: `cd sidecar && uv run pytest tests/test_bridge.py -q && uv run pytest -q`
Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add plugins/zimeiti/api.toml sidecar/tests/test_bridge.py
git commit -m "feat(bridge): zimeiti.invoke_add_topic quiet 条目（扩展存为选题不弹看板）"
```

---

### Task 5: 设置页「浏览器扩展」区

**Files:**
- Modify: `app/src/lib/brain.ts`（SettingsValues 加 `"http.token": string;`——:717-745 区域）
- Modify: `app/src/components/SettingsView.vue`（「启动与快捷键」s-group 后加新 s-group，:745 附近）

**Interfaces:**
- Consumes: settings 线自动携带新键（server.py:1281 `{**settings, ...}` 全量下发；Task 3 已把 `http.token` 加进 _SETTINGS_DEFAULTS）；`getSettingsOnce`（brain.ts:748）；SettingsView 既有 s-group/s-row/s-row-label/s-row-value 结构与 `s-note` 样式
- Produces: 设置页可见 token（掩码/显示切换 + 复制按钮）与安装指引；扩展 options 页用户从此粘贴

- [ ] **Step 1: brain.ts**

`SettingsValues` 加一行：

```typescript
  "http.token": string; // 浏览器扩展桥共享 token（设置页「浏览器扩展」展示供复制）
```

- [ ] **Step 2: SettingsView.vue 新 s-group**

在「启动与快捷键」`</section>` 后插入（结构与样式类随既有 s-group）：

```html
        <section class="s-group">
          <div class="s-group-title">浏览器扩展</div>
          <div class="s-row">
            <span class="s-row-label">连接 token</span>
            <span class="s-row-value">
              <code class="bridge-token">{{ showToken ? bridgeToken : maskedToken }}</code>
              <button class="s-mini-btn" @click="showToken = !showToken">{{ showToken ? "隐藏" : "显示" }}</button>
              <button class="s-mini-btn" :disabled="!bridgeToken" @click="copyToken">复制</button>
            </span>
          </div>
          <div v-if="tokenMsg" class="s-msg ok">{{ tokenMsg }}</div>
          <div class="s-row">
            <span class="s-row-label">端口</span>
            <span class="s-row-value">19527（YIBAO_HTTP_PORT 可覆盖，重启大脑生效）</span>
          </div>
          <div class="s-note">安装：chrome://extensions → 开发者模式 → 加载已解压 → 选仓库 extension/ 目录；扩展选项页粘贴 token。右键或工具栏按钮即可「存素材 / 存为选题」。</div>
        </section>
```

script 部分（组合式 API，随文件既有风格；`settings` 已在该组件经 getSettingsOnce 加载——找到存设置的 ref，照其用法取 `["http.token"]`）：

```typescript
const showToken = ref(false);
const tokenMsg = ref("");
const bridgeToken = computed(() => String(settings.value?.["http.token"] ?? ""));
const maskedToken = computed(() => (bridgeToken.value ? "•".repeat(Math.min(bridgeToken.value.length, 12)) : "（大脑未连接）"));
async function copyToken() {
  try {
    await navigator.clipboard.writeText(bridgeToken.value);
    tokenMsg.value = "已复制";
  } catch {
    showToken.value = true; // clipboard 被拒（无手势/权限）→ 显示全文手选复制
    tokenMsg.value = "复制失败，已为你显示全文";
  }
  setTimeout(() => (tokenMsg.value = ""), 2000);
}
```

（`settings` ref 的具体名字以该文件既有为准——如 `vals`/`form`；照 `proactive.level` 等键的读取方式。样式类 `s-mini-btn`/`bridge-token` 若无，在文件 style 区加：

```css
.s-mini-btn {
  margin-left: 6px;
  border: 1px solid var(--yb-border-base);
  border-radius: var(--yb-radius-pill);
  background: transparent;
  color: var(--yb-text);
  font-size: var(--yb-fs-xs);
  padding: 2px 8px;
  cursor: pointer;
}
.s-mini-btn:hover { background: var(--yb-surface-2); }
.bridge-token { font-family: var(--yb-mono); font-size: var(--yb-fs-sm); }
```

）

- [ ] **Step 3: 验证**

Run: `cd app && npx vue-tsc --noEmit && npx vite build`
Expected: 全 exit 0

- [ ] **Step 4: Commit**

```bash
git add app/src/lib/brain.ts app/src/components/SettingsView.vue
git commit -m "feat(bridge): 设置页「浏览器扩展」区——token 显示/复制 + 端口与安装指引"
```

---

### Task 6: extension/ MV3 扩展

**Files:**
- Create: `extension/manifest.json`、`extension/shared.js`、`extension/background.js`、`extension/popup.html`、`extension/popup.js`、`extension/options.html`、`extension/options.js`、`extension/README.md`

**Interfaces:**
- Consumes: Task 3 的 `POST /save`（{url, title, text, mode} → {ok, title} / {ok:false, error}）与 `GET /health`；token 头 `X-Yibao-Token`
- Produces: 无仓库内接口（Chrome 加载产物）

- [ ] **Step 1: manifest.json**

```json
{
  "manifest_version": 3,
  "name": "译宝素材桥",
  "version": "0.1.0",
  "description": "把当前页/选中文字一键存进译宝：素材库（摘要打标）或选题看板",
  "permissions": ["contextMenus", "notifications", "storage", "activeTab", "scripting"],
  "host_permissions": ["http://127.0.0.1/*"],
  "background": { "service_worker": "background.js", "type": "module" },
  "action": { "default_popup": "popup.html", "default_title": "存进译宝" },
  "options_page": "options.html"
}
```

- [ ] **Step 2: shared.js**（popup 与 background 共用）：

```javascript
// 共享：配置读写 + 页面提取 + 保存请求。ES module（manifest background type=module；popup 用 <script type="module">）。
export const DEFAULT_PORT = 19527;

export async function getConfig() {
  const { token = "", port = DEFAULT_PORT } = await chrome.storage.sync.get(["token", "port"]);
  return { token, port: Number(port) || DEFAULT_PORT };
}

// 注入页面提取（chrome.scripting executeScript 的 func）：选区优先，否则正文截 20000 字
export function extractPage() {
  const sel = (window.getSelection()?.toString() || "").trim();
  const text = (sel || document.body?.innerText || "").trim().slice(0, 20000);
  return { url: location.href, title: document.title || "", text };
}

export async function saveToYibao(payload) {
  const { token, port } = await getConfig();
  if (!token) return { ok: false, error: "未配置 token（扩展选项页粘贴）" };
  try {
    const resp = await fetch(`http://127.0.0.1:${port}/save`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Yibao-Token": token },
      body: JSON.stringify(payload),
    });
    return await resp.json();
  } catch (e) {
    return { ok: false, error: `连不上译宝大脑（127.0.0.1:${port}）——它在运行吗？` };
  }
}
```

- [ ] **Step 3: background.js**（右键菜单）：

```javascript
import { extractPage, saveToYibao } from "./shared.js";

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "yibao-save",
    title: "存进译宝素材库",
    contexts: ["page", "selection"],
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== "yibao-save" || !tab?.id) return;
  const [{ result }] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: extractPage });
  const r = await saveToYibao({ ...result, mode: "material" });
  chrome.notifications.create({
    type: "basic",
    iconUrl: "icon128.png",
    title: r.ok ? "已存素材" : "存素材失败",
    message: r.ok ? `《${r.title}》` : r.error || "未知错误",
  });
});
```

- [ ] **Step 4: popup.html + popup.js**

popup.html：

```html
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <style>
    body { width: 220px; margin: 0; padding: 12px; font-family: -apple-system, "PingFang SC", sans-serif; }
    h1 { font-size: 13px; margin: 0 0 10px; }
    button { width: 100%; padding: 7px 0; margin-bottom: 8px; border: 1px solid #cbd5e1; border-radius: 8px;
             background: #fff; font-size: 13px; cursor: pointer; }
    button:hover { background: #f1f5f9; }
    #status { font-size: 12px; color: #475569; min-height: 16px; }
    #opt { font-size: 11px; color: #0284c7; cursor: pointer; display: block; margin-top: 6px; }
  </style>
</head>
<body>
  <h1>存进译宝</h1>
  <button id="save-material">存素材（摘要打标）</button>
  <button id="save-topic">存为选题（上看板）</button>
  <div id="status"></div>
  <a id="opt">设置 token / 端口</a>
  <script type="module" src="popup.js"></script>
</body>
</html>
```

popup.js：

```javascript
import { extractPage, saveToYibao } from "./shared.js";

const status = (t) => (document.getElementById("status").textContent = t);

async function save(mode) {
  status("提取页面…");
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) return status("没有活动标签页");
  const [{ result }] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: extractPage });
  if (!result?.text) return status("页面没有可存的内容（先选中或打开正文页）");
  status("保存中…");
  const r = await saveToYibao({ ...result, mode });
  status(r.ok ? `✓ ${mode === "topic" ? "已存选题" : "已存素材"}：《${r.title}》` : `✗ ${r.error}`);
}

document.getElementById("save-material").addEventListener("click", () => save("material"));
document.getElementById("save-topic").addEventListener("click", () => save("topic"));
document.getElementById("opt").addEventListener("click", () => chrome.runtime.openOptionsPage());
```

- [ ] **Step 5: options.html + options.js**

options.html：

```html
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <style>
    body { max-width: 460px; margin: 24px auto; font-family: -apple-system, "PingFang SC", sans-serif; }
    h1 { font-size: 15px; }
    label { display: block; font-size: 12px; color: #475569; margin: 12px 0 4px; }
    input { width: 100%; box-sizing: border-box; padding: 6px 8px; border: 1px solid #cbd5e1; border-radius: 6px; font-size: 13px; }
    button { margin-top: 14px; padding: 6px 14px; border: 1px solid #cbd5e1; border-radius: 8px; background: #fff; cursor: pointer; }
    #msg { font-size: 12px; margin-top: 10px; color: #475569; }
    .hint { font-size: 11px; color: #94a3b8; margin-top: 4px; }
  </style>
</head>
<body>
  <h1>译宝素材桥设置</h1>
  <label for="token">连接 token</label>
  <input id="token" type="password" placeholder="译宝设置页「浏览器扩展」区复制" />
  <div class="hint">在译宝 设置 → 通用 → 浏览器扩展 里点「复制」。</div>
  <label for="port">端口</label>
  <input id="port" type="number" placeholder="19527" />
  <button id="save">保存</button>
  <button id="test">测试连接</button>
  <div id="msg"></div>
  <script type="module" src="options.js"></script>
</body>
</html>
```

options.js：

```javascript
import { DEFAULT_PORT, getConfig } from "./shared.js";

const $ = (id) => document.getElementById(id);
const msg = (t) => ($("msg").textContent = t);

const cfg = await getConfig();
$("token").value = cfg.token;
$("port").value = cfg.port;

$("save").addEventListener("click", async () => {
  await chrome.storage.sync.set({
    token: $("token").value.trim(),
    port: Number($("port").value) || DEFAULT_PORT,
  });
  msg("已保存");
});

$("test").addEventListener("click", async () => {
  const token = $("token").value.trim();
  const port = Number($("port").value) || DEFAULT_PORT;
  try {
    const resp = await fetch(`http://127.0.0.1:${port}/health`, { headers: { "X-Yibao-Token": token } });
    const j = await resp.json();
    msg(j.ok ? "✓ 连接成功，译宝大脑在线" : `✗ ${j.error || "连接失败"}`);
  } catch {
    msg(`✗ 连不上 127.0.0.1:${port}——译宝在运行吗？端口对吗？`);
  }
});
```

- [ ] **Step 6: icon128.png + README.md**

icon：临时用 128×128 纯色 PNG（一行 Python 生成：`uv run python -c "from PIL import Image; Image.new('RGB',(128,128),(56,189,248)).save('extension/icon128.png')"`——PIL 在 sidecar 依赖内），后续换团子图标（README 注明）。

README.md：

```markdown
# 译宝素材桥（浏览器扩展）

把当前页/选中文字一键存进译宝：素材库（LLM 摘要打标）或选题看板。

## 安装（Chromium 系：Chrome/Edge/Arc）

1. 打开 `chrome://extensions`，右上角开「开发者模式」
2. 「加载已解压的扩展程序」→ 选本目录（`extension/`）
3. 右键扩展图标 →「选项」：粘贴 token（译宝 设置 → 通用 → 浏览器扩展 区点「复制」），端口默认 19527
4. 点「测试连接」确认 ✓

## 用法

- **右键**（页面或选中文字）：存进译宝素材库
- **工具栏按钮**：存素材 / 存为选题
- 选中文字优先；未选中则存整页正文（截 20000 字）

## 排错

- 连不上：译宝在运行吗？（桥随大脑启动，监听 127.0.0.1:19527）
- 401：token 不对——重新从设置页复制（token 由译宝首次启动生成，不会变）
- 端口被改：设了 `YIBAO_HTTP_PORT` 的话两边要一致
- icon128.png 为占位纯色图，后续换团子图标
```

- [ ] **Step 7: Commit**

```bash
git add extension/
git commit -m "feat(bridge): Chromium MV3 扩展——popup 双动作 + 右键菜单 + options（token/端口/测试连接）"
```

---

### Task 7: 验收

- [ ] **Step 1: 自动化全绿**

```bash
cd sidecar && uv run pytest -q          # 831 + 新增 ≈ 846 passed
cd ../app && npx vue-tsc --noEmit && npx vite build && cargo check --manifest-path src-tauri/Cargo.toml
```

- [ ] **Step 2: 真机（人工）验收清单**

1. 启动译宝（大脑起来）→ `curl -H "X-Yibao-Token: $(grep -o '\"http.token\": \"[^\"]*\"' ~/Library/Application\ Support/yibao/settings.json | cut -d'\"' -f4)" http://127.0.0.1:19527/health` → `{"ok":true,...}`
2. 设置页「浏览器扩展」区：token 掩码 → 显示 → 复制成功
3. Chrome 加载扩展 → options 粘贴 token → 测试连接 ✓
4. **登录墙页**（公众号文章/知乎）：右键「存进译宝素材库」→ 通知「已存素材《…》」+ 团子气泡「已存素材：《…》」；插件页 → 自媒体 → 素材库可见（带来源链接 + LLM 摘要）——验证 text 路径绕过重抓
5. 另一页选中一段 → popup「存素材」→ 存的是选区
6. popup「存为选题」→ 看板「候选」列出现该标题
7. token 改错一位 → popup 显示 401 提示
8. 关掉译宝 → popup 显示「连不上」提示

- [ ] **Step 3: 收尾 commit（如有验收修小补）**

```bash
git add -p
git commit -m "fix(bridge): 真机验收小修"
```

---

## 自审

- spec 覆盖：微 HTTP（Task 1）✅、mat_save 兼容（Task 2）✅、桥+token（Task 3）✅、invoke_add_topic（Task 4）✅、设置页（Task 5）✅、扩展（Task 6）✅、真机含登录墙/401/断连（Task 7）✅
- 类型一致：`serve(host, port, handler)`（Task 1 定义 = Task 3 调用）✅；route handler 签名 `(method, path, headers, body) -> (status, dict)` 跨 Task 1/3 ✅；`pa_http_<n>`（Task 3 发 = App.vue pa_ 前缀认领）✅；`zimeiti.invoke_add_topic`（Task 4 注册 = Task 3 路由引用）✅；`http.token`（Task 3 写入 = Task 5 展示）✅；扩展 POST 体 {url,title,text,mode} = Task 3 路由解析 ✅
- 无占位：每步含完整代码/命令/预期 ✅
