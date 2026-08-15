# 手机伴生端 P3（App 骨架 + 配对 + 流式对话）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建起 `mobile/` Capacitor+Vue 工程：配对（host+token）+ 流式对话页（SSE 拼接 + 打断），开发期用桌面浏览器直连本地 sidecar 全链路可验；外加两笔 spec §14 挂的 P3 前置服务端小活（CORS、interrupt 域收紧）。

**Architecture:** 新顶层 `mobile/`（Vite SPA + vue-router，状态用 composables 不引 pinia）；SSE 用浏览器原生 EventSource（自动重连 + Last-Event-ID 免费续传）；服务端 `http_api.py` 加 CORS 反射中间件（WKWebView/开发浏览器跨域必需）、`server.py` 加 `running_surface` 追踪让手机 interrupt 只杀真正在跑的 mobile 轮。

**Tech Stack:** Vue 3.5 / vue-router 4 / Vite 6 / TS 5.6 / vitest / Capacitor ^7（@capacitor/app + @capacitor/preferences）/ pnpm。服务端不新增依赖。

**Spec:** `docs/superpowers/specs/2026-08-14-mobile-companion-design.md`（§5 客户端设计、§14 开放问题的 P3 两条、§13 P3 阶段注记）

## Global Constraints

- P2 外网已封存：**所有开发与验证用 `http://127.0.0.1:19527`**（sidecar 直连，本机浏览器同源策略下走 CORS）
- mobile/ 用 **pnpm**；依赖版本对齐桌面端：vue ^3.5、vite ^6、typescript ~5.6、vue-tsc ^2.1、vitest ^4；新增：vue-router ^4、@capacitor/{core,cli,app,preferences} ^7、@vue/test-utils ^2
- **不引**：pinia、axios、任何 SSE 封装库（EventSource 原生）
- 服务端改动仅 `http_api.py` / `server.py`；测试沿用各自惯例（pytest 函数级 + asyncio.run / vitest 函数级）；中文注释文案
- sidecar 帧格式（P1 已定）：`id: <seq>` + `event: <kind>` + `data: <json>`；chunk 帧 data 含 `surface`/`conversation_id`（信封字段，仅 surface==="mobile" 的帧属手机）
- 每任务 TDD（先红后绿）+ 独立提交；提交信息仓内风格（`feat(mobile): …` / `feat(server): …`，中文）

---

### Task 1:（server）CORS 反射中间件

**Files:**
- Modify: `sidecar/src/yibao_brain/http_api.py`（build_app 里加 `_cors` 中间件，注册在 `_auth` **之前**——aiohttp 列表前面的先包外层，401/429 响应也要带 CORS 头）
- Test: `sidecar/tests/test_http_api.py`（追加）

**Interfaces:**
- Produces: `GET/POST/OPTIONS` 响应对允许的 Origin 反射 `Access-Control-Allow-Origin`；预检 OPTIONS **无需 token** 返回 204。允许的 origin：`capacitor://*`（iOS WKWebView）、host 为 `localhost`/`127.0.0.1` 的 http(s)（Android webview 与 vite 开发服务器，任意端口）。

- [ ] **Step 1: 写失败测试**

追加到 `sidecar/tests/test_http_api.py`：

```python
def test_cors_preflight_204_without_token():
    async def main():
        app = build_app(bridge_token="btok", mobile_token="mtok", tap=EventTap(lambda m: None))
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            r = await client.options("/v1/chat", headers={
                "Origin": "capacitor://localhost",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type, x-yibao-token"})
            assert r.status == 204  # 预检无自定义 token 头，不能被 auth 拦
            assert r.headers["Access-Control-Allow-Origin"] == "capacitor://localhost"
            assert "x-yibao-token" in r.headers["Access-Control-Allow-Headers"].lower()
        finally:
            await client.close()

    asyncio.run(main())


def test_cors_reflects_on_auth_failure_and_allows_localhost_any_port():
    async def main():
        app = build_app(bridge_token="btok", mobile_token="mtok", tap=EventTap(lambda m: None))
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            r = await client.get("/v1/health", headers={"Origin": "http://localhost:5173", "X-Yibao-Token": "bad"})
            assert r.status == 401
            assert r.headers["Access-Control-Allow-Origin"] == "http://localhost:5173"  # 401 也要带（客户端要能读到状态码）
        finally:
            await client.close()

    asyncio.run(main())


def test_cors_foreign_origin_gets_nothing():
    async def main():
        app = build_app(bridge_token="btok", mobile_token="mtok", tap=EventTap(lambda m: None))
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            r = await client.get("/v1/health", headers={"Origin": "http://evil.com", "X-Yibao-Token": "mtok"})
            assert r.status == 200
            assert "Access-Control-Allow-Origin" not in r.headers
        finally:
            await client.close()

    asyncio.run(main())
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd sidecar && uv run pytest tests/test_http_api.py -v -k cors`
Expected: FAIL（预检 204 断言——现 OPTIONS 无 token 会 401）

- [ ] **Step 3: 实现**

`sidecar/src/yibao_brain/http_api.py`：模块级加

```python
def _cors_allow(origin: str | None) -> str | None:
    """移动端 origin 白名单（反射式）：Capacitor iOS=capacitor://localhost、
    Android=http://localhost、开发浏览器=http://localhost:任意端口 / 127.0.0.1:任意端口。
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
    if u.scheme in ("http", "https") and u.hostname in ("localhost", "127.0.0.1"):
        return origin
    return None


def _cors_headers(allow: str) -> dict:
    return {
        "Access-Control-Allow-Origin": allow,
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        # Last-Event-ID：EventSource 断线重连带的头，不在 CORS 安全清单里，预检必查
        "Access-Control-Allow-Headers": "Content-Type, X-Yibao-Token, Last-Event-ID",
        "Access-Control-Max-Age": "600",
    }
```

`build_app` 内、`_auth` 定义之前加：

```python
    @web.middleware
    async def _cors(request: web.Request, handler):
        """CORS 外层中间件：预检直接 204（无自定义头，auth 拦不得）；
        其余响应按白名单反射 Origin——401/429 也要带，客户端要能读状态。"""
        allow = _cors_allow(request.headers.get("Origin"))
        if request.method == "OPTIONS" and allow:
            return web.Response(status=204, headers=_cors_headers(allow))
        resp = await handler(request)
        if allow:
            resp.headers.update(_cors_headers(allow))
        return resp
```

应用注册改为 `web.Application(middlewares=[_cors, _auth])`。

- [ ] **Step 4: 跑测试确认通过 + 全量回归**

Run: `cd sidecar && uv run pytest tests/test_http_api.py -v && uv run pytest -x -q`
Expected: 全 PASS

- [ ] **Step 5: 提交**

```bash
git add sidecar/src/yibao_brain/http_api.py sidecar/tests/test_http_api.py
git commit -m "feat(server): CORS 反射中间件——Capacitor/开发浏览器跨域放行"
```

---

### Task 2:（server）interrupt 域收紧——running_surface 追踪

**Files:**
- Modify: `sidecar/src/yibao_brain/server.py`（run_state 加 `running_surface`；`_schedule_run` 包装 start；panel_action 分支同样包装；`_interrupt_mobile` 改判据）
- Test: `sidecar/tests/test_server.py`（追加 1 个：排队窗口不误杀桌面）

**Interfaces:**
- Produces: `run_state["running_surface"]` = 实际在跑的 surface（排队结束开跑才写入，区别于 `run_state["surface"]`=最近受理的 surface）；`_interrupt_mobile()` 仅当 `running_surface == "mobile"` 时打断。spec §14「跨 surface 排队窗口内手机 interrupt 连环杀」勾销。

- [ ] **Step 1: 写失败测试**

追加到 `sidecar/tests/test_server.py`（沿用文件内现有 mobile 系列测试的 held-reader + 真stdin 慢流式模式，fake provider 每 chunk 0.08s 延迟那套）：

```python
def test_mobile_interrupt_does_not_kill_desktop_while_mobile_queued(tmp_path):
    """spec §14：pet 慢流式在跑、mobile chat 跨 surface 排队中——此刻手机 interrupt
    应返回 False 且 pet 轮不死（旧行为会连环杀：preempt 顶掉 pet + 排队的 mobile 秒跳）。"""
    inbox = queue.Queue()
    inbox.put({"id": 10, "type": "run", "surface": "pet", "text": "长任务"})

    def _reader():
        while True:  # 阻塞读：测试全程可控，结束时放 None 收尾
            m = inbox.get()
            if m is None:
                return None

    async def main():
        import os

        os.environ["YIBAO_HTTP_PORT"] = "19867"
        out = []
        import yibao_brain.server as S

        orig_load = S.load_settings
        S.load_settings = lambda: {"http.token": "btok", "http.mobile_token": "mtok"}
        try:
            serve_task = asyncio.ensure_future(S.serve_async(
                _reader, lambda m: out.append(m), use_real=False,
                db_path=str(tmp_path / "q.db"),
                provider=_SlowStreamProvider(chunks=["桌", "面", "回", "复"], delay=0.3),
                http_enabled=True))
            await asyncio.sleep(0.4)
            import aiohttp

            async with aiohttp.ClientSession() as sess:
                # pet 在跑（0.3s/chunk × 4 ≈ 1.2s 窗口），手机 chat 跨 surface 排队
                r = await sess.post("http://127.0.0.1:19867/v1/chat",
                                    headers={"X-Yibao-Token": "mtok"}, json={"text": "手机消息"})
                assert r.status == 200
                ir = await sess.post("http://127.0.0.1:19867/v1/interrupt",
                                     headers={"X-Yibao-Token": "mtok"}, json={})
                assert (await ir.json()) == {"ok": True, "interrupted": False}  # 排队中不误杀
            # 等桌面轮自然跑完：run_done id=10 到达且 final_reply 完整
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if any(m.get("type") == "run_done" and m.get("id") == 10 for m in out):
                    break
                await asyncio.sleep(0.05)
            assert any(m.get("type") == "run_done" and m.get("id") == 10 for m in out), "桌面轮被误杀或未完成"
            assert any(m.get("type") == "event" and m.get("surface") == "pet"
                       and m.get("event", {}).get("kind") == "final_reply" for m in out), "桌面回复被截断"
        finally:
            S.load_settings = orig_load
            os.environ.pop("YIBAO_HTTP_PORT", None)
            inbox.put(None)
            await asyncio.wait_for(serve_task, 5)

    asyncio.run(main())
```

（文件顶部如无 `import queue` / `import time` 需补。`_SlowStreamProvider` 若文件内已有同款慢流式 provider 则复用；没有则补：）

```python
class _SlowStreamProvider:
    """慢流式 fake：astream 按 chunks 逐段吐字、段间 delay 秒——制造稳定的「在跑中」窗口。"""

    def __init__(self, chunks: list[str], delay: float):
        self._chunks, self._delay = chunks, delay

    def chat(self, messages, tools=None):
        return FakeProvider(text="".join(self._chunks)).chat(messages, tools)

    async def astream(self, messages, tools=None):
        from yibao_brain.llm import LLMDelta

        for c in self._chunks:
            await asyncio.sleep(self._delay)
            yield LLMDelta(text=c)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd sidecar && uv run pytest tests/test_server.py -v -k interrupt_does_not_kill`
Expected: FAIL（`interrupted` 为 True——旧判据 `run_state["surface"]=="mobile"` 在排队时已翻成 mobile）

- [ ] **Step 3: 实现**

`sidecar/src/yibao_brain/server.py`：

(a) `run_state` 初始化（`:561` 一带）加一个键：

```python
    run_state: dict = {"task": None, "cancel": None, "preempt_gen": 0, "surface": None,
                       "running_surface": None}  # running_surface=实际在跑的（排队结束才写）；surface=最近受理的
```

(b) `_schedule_run` 整体替换为包装版：

```python
    def _schedule_run(surface: str, rid, start) -> None:
        """受理尾巴（run/voice_start/手机 chat 共用）：同 surface 抢占 + 跨 surface 链式排队。
        running_surface 在真正开跑时才写——手机 interrupt 按它判域，排队窗口不误杀桌面轮。"""
        _preempt_if_same_surface(surface)
        prev = run_state["task"]
        run_state["surface"] = surface  # 受理即记录：下次 dispatch 判断同/跨 surface 无调度竞态

        async def _marked(cancel, s=start, sf=surface):
            run_state["running_surface"] = sf
            await s(cancel)

        run_state["task"] = asyncio.ensure_future(
            _chain_start(prev, _marked, run_state["preempt_gen"]))
```

(c) `panel_action` 分支末尾的 `run_state["task"] = asyncio.ensure_future(_chain_start(prev, start, run_state["preempt_gen"]))`（该分支自建的 start lambda 处）同样包一层（保持 `running_surface` 全路径一致）：

```python
            async def _marked_panel(cancel, s=start, sf=surface):
                run_state["running_surface"] = sf
                await s(cancel)

            run_state["task"] = asyncio.ensure_future(
                _chain_start(prev, _marked_panel, run_state["preempt_gen"]))
```

(d) `_interrupt_mobile` 判据改为：

```python
    def _interrupt_mobile() -> bool:
        """只打断真正在跑的 mobile 轮（running_surface；排队中 surface 已翻但没开跑）。
        壳 interrupt 是「全都停」；手机不该误伤桌面对话。"""
        if run_state.get("running_surface") == "mobile" and run_state["cancel"] is not None:
            _preempt_current()
            return True
        return False
```

- [ ] **Step 4: 跑测试确认通过 + 全量回归**

Run: `cd sidecar && uv run pytest tests/test_server.py -v -k interrupt && uv run pytest -x -q`
Expected: 新旧 interrupt 测试（含 T5 期的正/负路径）全 PASS

- [ ] **Step 5: 提交**

```bash
git add sidecar/src/yibao_brain/server.py sidecar/tests/test_server.py
git commit -m "fix(server): interrupt 域收紧——running_surface 追踪，排队窗口不误杀桌面"
```

---

### Task 3:（mobile）工程脚手架

**Files:**
- Create: `mobile/`（package.json、vite.config.ts、tsconfig.json、tsconfig.node.json、index.html、src/{main.ts,App.vue,router.ts,views/Pairing.vue,views/Chat.vue 占位}、vitest 冒烟测试）
- 不 npm create 脚手架（交互式命令没法在执行环境跑）——下面给全部文件内容手写

**Interfaces:**
- Produces: 可 `pnpm dev`/`pnpm build`/`pnpm test` 的 Vue SPA 骨架；路由 `/pairing`、`/chat`；守卫：未配对一律回 `/pairing`（配对态读 `loadConn()`，Task 4 实装前先返回 null=未配对）。

- [ ] **Step 1: 写工程文件**

`mobile/package.json`：

```json
{
  "name": "yibao-mobile",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vue-tsc --noEmit && vite build",
    "preview": "vite preview",
    "test": "vitest run",
    "cap": "cap"
  },
  "dependencies": {
    "@capacitor/app": "^7.0.0",
    "@capacitor/core": "^7.0.0",
    "@capacitor/preferences": "^7.0.0",
    "vue": "^3.5.13",
    "vue-router": "^4.5.0"
  },
  "devDependencies": {
    "@capacitor/cli": "^7.0.0",
    "@vitejs/plugin-vue": "^5.2.1",
    "@vue/test-utils": "^2.4.6",
    "typescript": "~5.6.2",
    "vite": "^6.0.3",
    "vitest": "^4.1.10",
    "vue-tsc": "^2.1.10"
  }
}
```

`mobile/vite.config.ts`：

```typescript
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  clearScreen: false,
  server: { port: 5173, strictPort: true },
});
```

`mobile/tsconfig.json`：

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "strict": true,
    "jsx": "preserve",
    "noEmit": true,
    "isolatedModules": true,
    "esModuleInterop": true,
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "types": ["vite/client"],
    "skipLibCheck": true
  },
  "include": ["src/**/*.ts", "src/**/*.vue"]
}
```

`mobile/index.html`：

```html
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover, user-scalable=no" />
    <title>译宝</title>
  </head>
  <body>
    <div id="app"></div>
    <script type="module" src="/src/main.ts"></script>
  </body>
</html>
```

`mobile/src/main.ts`：

```typescript
import { createApp } from "vue";
import App from "./App.vue";
import { router } from "./router";
import "./style.css";

createApp(App).use(router).mount("#app");
```

`mobile/src/style.css`（移动端基线，暗色随系统）：

```css
:root { color-scheme: light dark; font-family: -apple-system, "PingFang SC", sans-serif; }
* { box-sizing: border-box; }
body { margin: 0; background: #f5f5f7; color: #1d1d1f; }
@media (prefers-color-scheme: dark) { body { background: #111; color: #f0f0f0; } }
#app { max-width: 640px; margin: 0 auto; min-height: 100dvh; display: flex; flex-direction: column; }
```

`mobile/src/router.ts`：

```typescript
import { createRouter, createWebHashHistory } from "vue-router";
// hash 路由：Capacitor webview 加载本地文件，history 模式在 file:// 下路由会断
import { loadConn } from "./api/connection";

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", redirect: "/chat" },
    { path: "/pairing", component: () => import("./views/Pairing.vue") },
    { path: "/chat", component: () => import("./views/Chat.vue") },
  ],
});

router.beforeEach(async (to) => {
  if (to.path !== "/pairing" && !(await loadConn())) return "/pairing";
});
```

（`loadConn` Task 4 实装；本任务先建 `mobile/src/api/connection.ts` 占位：`export async function loadConn(): Promise<null> { return null }`——Task 4 会整体替换该文件。）

`mobile/src/App.vue`：

```vue
<template>
  <router-view />
</template>
```

`mobile/src/views/Pairing.vue` / `Chat.vue` 占位：

```vue
<template><main style="padding:24px"><h2>配对</h2><p>Task 4 实装</p></main></template>
```
```vue
<template><main style="padding:24px"><h2>对话</h2><p>Task 6 实装</p></main></template>
```

`mobile/capacitor.config.ts`：

```typescript
import type { CapacitorConfig } from "@capacitor/cli";

const config: CapacitorConfig = {
  appId: "com.denny.yibao",
  appName: "译宝",
  webDir: "dist",
};

export default config;
```

`mobile/.gitignore`：

```
node_modules/
dist/
ios/App/Pods/
ios/App/App/public/
android/.gradle/
android/app/build/
*.log
.DS_Store
```

`mobile/src/router.test.ts`（冒烟：路由表形状）：

```typescript
import { describe, expect, it } from "vitest";
import { router } from "./router";

describe("router", () => {
  it("有 /pairing 与 /chat 两条路由且根重定向到 /chat", () => {
    const paths = router.getRoutes().map((r) => r.path);
    expect(paths).toContain("/pairing");
    expect(paths).toContain("/chat");
  });
});
```

（`mobile/vitest.config.ts` 不需要——vite.config.ts 即被 vitest 复用。）

- [ ] **Step 2: 安装与验证**

```bash
cd mobile && pnpm install && pnpm test && pnpm build
```
Expected: vitest 1 passed；`vue-tsc --noEmit` 无错；vite build 出 `dist/`

- [ ] **Step 3: 提交**

```bash
git add mobile/
git commit -m "feat(mobile): Capacitor+Vue 工程骨架——路由/配对守卫/构建测试链路"
```

---

### Task 4:（mobile）连接与配对

**Files:**
- Create: `mobile/src/api/connection.ts`（整体替换 Task 3 占位）
- Create: `mobile/src/api/connection.test.ts`
- Modify: `mobile/src/views/Pairing.vue`（实装表单）

**Interfaces:**
- Produces:
  - `interface ConnConfig { host: string; token: string }`
  - `normalizeHost(h: string): string`——无 scheme 补 `http://`、去尾部 `/`
  - `parsePairUrl(u: string): ConnConfig | null`——解析 `yibao://pair?host=…&token=…`，非法/null
  - `loadConn(): Promise<ConnConfig | null>` / `saveConn(c: ConnConfig): Promise<void>`——@capacitor/preferences 键 `yibao.conn`（浏览器降级 localStorage，Preferences 本身在 web 就落 localStorage）
  - `testConn(c: ConnConfig, fetchImpl: typeof fetch = fetch): Promise<{ ok: boolean; reason?: string }>`——GET `/v1/health` 带 token
  - Pairing.vue：host/token 表单 + 「测试连接」按钮（结果显示 ✓/错误原因）+ 「保存并进入」（testConn 通过才可点）

- [ ] **Step 1: 写失败测试**

`mobile/src/api/connection.test.ts`：

```typescript
import { beforeEach, describe, expect, it, vi } from "vitest";

const { normalizeHost, parsePairUrl, testConn } = await import("./connection");

describe("normalizeHost", () => {
  it("补 scheme 去尾斜杠", () => {
    expect(normalizeHost("127.0.0.1:19527")).toBe("http://127.0.0.1:19527");
    expect(normalizeHost("https://yibao.wuyill.com/")).toBe("https://yibao.wuyill.com");
    expect(normalizeHost(" http://a.com ")).toBe("http://a.com");
  });
});

describe("parsePairUrl", () => {
  it("解析深链配对参数", () => {
    expect(parsePairUrl("yibao://pair?host=https%3A%2F%2Fyibao.wuyill.com&token=abc"))
      .toEqual({ host: "https://yibao.wuyill.com", token: "abc" });
  });
  it("非配对路径或缺参返回 null", () => {
    expect(parsePairUrl("yibao://chat")).toBeNull();
    expect(parsePairUrl("yibao://pair?host=x")).toBeNull(); // 缺 token
    expect(parsePairUrl("https://evil.com/pair?host=x&token=y")).toBeNull();
  });
});

describe("testConn", () => {
  it("health 200 → ok；401 → 带 reason；网络错 → reason", async () => {
    const ok = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    expect(await testConn({ host: "http://127.0.0.1:19527", token: "t" }, ok as unknown as typeof fetch)).toEqual({ ok: true });
    const unauth = vi.fn(async () => new Response("{}", { status: 401 }));
    const r2 = await testConn({ host: "http://x", token: "bad" }, unauth as unknown as typeof fetch);
    expect(r2.ok).toBe(false);
    expect(r2.reason).toContain("token");
    const dead = vi.fn(async () => { throw new TypeError("fetch failed"); });
    const r3 = await testConn({ host: "http://x", token: "t" }, dead as unknown as typeof fetch);
    expect(r3.ok).toBe(false);
    expect(r3.reason).toBeTruthy();
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd mobile && pnpm vitest run src/api/connection.test.ts`
Expected: FAIL（模块无这些导出）

- [ ] **Step 3: 实现**

`mobile/src/api/connection.ts`：

```typescript
import { Preferences } from "@capacitor/preferences";

export interface ConnConfig {
  host: string; // 形如 http://127.0.0.1:19527 或 https://yibao.wuyill.com（无尾斜杠）
  token: string; // http.mobile_token
}

const KEY = "yibao.conn";

export function normalizeHost(h: string): string {
  let s = h.trim();
  if (!/^https?:\/\//i.test(s)) s = `http://${s}`;
  return s.replace(/\/+$/, "");
}

export function parsePairUrl(u: string): ConnConfig | null {
  // 深链：yibao://pair?host=<urlencoded>&token=<...>（桌面设置页二维码内容，P5 落地）
  try {
    const url = new URL(u);
    if (url.protocol !== "yibao:" || url.host !== "pair") return null;
    const host = normalizeHost(url.searchParams.get("host") || "");
    const token = (url.searchParams.get("token") || "").trim();
    if (!host || !token) return null;
    return { host, token };
  } catch {
    return null;
  }
}

export async function loadConn(): Promise<ConnConfig | null> {
  const { value } = await Preferences.get({ key: KEY });
  if (!value) return null;
  try {
    const c = JSON.parse(value) as ConnConfig;
    return c.host && c.token ? c : null;
  } catch {
    return null;
  }
}

export async function saveConn(c: ConnConfig): Promise<void> {
  await Preferences.set({ key: KEY, value: JSON.stringify(c) });
}

export async function testConn(
  c: ConnConfig,
  fetchImpl: typeof fetch = fetch,
): Promise<{ ok: boolean; reason?: string }> {
  try {
    const r = await fetchImpl(`${c.host}/v1/health`, { headers: { "X-Yibao-Token": c.token } });
    if (r.status === 200) return { ok: true };
    if (r.status === 401 || r.status === 429) return { ok: false, reason: `token 不对或被限速（${r.status}）` };
    return { ok: false, reason: `服务器返回 ${r.status}` };
  } catch (e) {
    return { ok: false, reason: `连不上：${e instanceof Error ? e.message : "网络错误"}（译宝在运行吗？）` };
  }
}
```

`Pairing.vue` 实装（表单两输入 + 两按钮 + 状态行；保存成功 `router.replace("/chat")`；同时支持页面 URL 查询参数 `?host=&token=` 预填，方便浏览器开发）：

```vue
<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import { normalizeHost, saveConn, testConn, type ConnConfig } from "../api/connection";

const router = useRouter();
const host = ref(new URLSearchParams(location.search).get("host") ?? "");
const token = ref(new URLSearchParams(location.search).get("token") ?? "");
const testing = ref(false);
const result = ref("");

async function onTest() {
  testing.value = true;
  result.value = "";
  const r = await testConn({ host: normalizeHost(host.value), token: token.value });
  result.value = r.ok ? "✓ 连接成功" : `✗ ${r.reason}`;
  testing.value = false;
}

async function onSave() {
  const c: ConnConfig = { host: normalizeHost(host.value), token: token.value.trim() };
  const r = await testConn(c);
  if (!r.ok) {
    result.value = `✗ ${r.reason}`;
    return;
  }
  await saveConn(c);
  router.replace("/chat");
}
</script>

<template>
  <main class="pair">
    <h2>连接译宝</h2>
    <p class="hint">服务器地址与 token 在桌面端「设置 → 手机伴生端」获取（P5 提供；开发期从 settings.json 的 http.mobile_token 取）。</p>
    <label>服务器地址<input v-model="host" placeholder="http://127.0.0.1:19527" inputmode="url" /></label>
    <label>Token<input v-model="token" placeholder="http.mobile_token" autocapitalize="off" /></label>
    <div class="row">
      <button :disabled="testing || !host || !token" @click="onTest">测试连接</button>
      <button class="primary" :disabled="!host || !token" @click="onSave">保存并进入</button>
    </div>
    <p v-if="result" class="result">{{ result }}</p>
  </main>
</template>

<style scoped>
.pair { padding: 24px 16px; display: flex; flex-direction: column; gap: 14px; }
.hint { font-size: 13px; opacity: 0.65; }
label { display: flex; flex-direction: column; gap: 6px; font-size: 14px; }
input { padding: 10px; border: 1px solid #ccc; border-radius: 8px; font-size: 15px; background: transparent; color: inherit; }
.row { display: flex; gap: 10px; }
button { flex: 1; padding: 12px; border-radius: 10px; border: 1px solid #ccc; background: transparent; color: inherit; font-size: 15px; }
button.primary { background: #2f6fed; border-color: #2f6fed; color: #fff; }
.result { font-size: 14px; }
</style>
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd mobile && pnpm test && pnpm build`
Expected: 全 PASS（浏览器环境 vitest 下 Preferences web 实现走 localStorage，无需 mock）

- [ ] **Step 5: 提交**

```bash
git add mobile/src
git commit -m "feat(mobile): 配对页——host/token 表单/测试连接/深链 URL 解析/Preferences 持久化"
```

---

### Task 5:（mobile）SSE 事件流 composable

**Files:**
- Create: `mobile/src/api/events.ts`
- Create: `mobile/src/api/events.test.ts`

**Interfaces:**
- Produces:
  - `useEventStream(url: () => string, makeES: (u: string) => EventSourceLike = (u) => new EventSource(u))` 返回 `{ state: Ref<"idle"|"connecting"|"open"|"error">, on(kind, fn): () => void, start(): void, stop(): void }`
  - `interface EventSourceLike { addEventListener(k: string, cb: (e: { data: string }) => void): void; close(): void; onopen: (() => void) | null; onerror: (() => void) | null }`——测试注入 fake 用
  - `KNOWN_KINDS`：订阅的服务端事件名列表
  - `buildEventsUrl(c: ConnConfig): string`——`${c.host}/v1/events?token=${encodeURIComponent(c.token)}`（token 走 query：EventSource 不能设 header，P1 已定）
  - 回调收到的是**已 JSON.parse 的 data 对象**；心跳注释行 EventSource 自动忽略，无需处理

- [ ] **Step 1: 写失败测试**

`mobile/src/api/events.test.ts`：

```typescript
import { describe, expect, it, vi } from "vitest";
import { buildEventsUrl, useEventStream, type EventSourceLike } from "./events";

function fakeES() {
  const listeners = new Map<string, (e: { data: string }) => void>();
  const es: EventSourceLike = {
    addEventListener: (k, cb) => listeners.set(k, cb),
    close: vi.fn(),
    onopen: null,
    onerror: null,
  };
  return {
    es,
    open: () => es.onopen?.(),
    err: () => es.onerror?.(),
    emit: (k: string, data: unknown) => listeners.get(k)?.({ data: JSON.stringify(data) }),
  };
}

describe("buildEventsUrl", () => {
  it("token 走 query 参数", () => {
    expect(buildEventsUrl({ host: "http://127.0.0.1:19527", token: "a b&c" }))
      .toBe("http://127.0.0.1:19527/v1/events?token=a%20b%26c");
  });
});

describe("useEventStream", () => {
  it("start→open 状态迁移；帧 JSON 解析后分发；stop 关闭", async () => {
    const f = fakeES();
    const stream = useEventStream(() => "http://x/v1/events?token=t", () => f.es);
    const chunk = vi.fn();
    const off = stream.on("final_reply_chunk", chunk);
    expect(stream.state.value).toBe("idle");
    stream.start();
    expect(stream.state.value).toBe("connecting");
    f.open();
    expect(stream.state.value).toBe("open");
    f.emit("final_reply_chunk", { kind: "final_reply_chunk", text: "你好", surface: "mobile" });
    expect(chunk).toHaveBeenCalledWith({ kind: "final_reply_chunk", text: "你好", surface: "mobile" });
    off();
    f.emit("final_reply_chunk", { text: "不再收" });
    expect(chunk).toHaveBeenCalledTimes(1);
    stream.stop();
    expect(f.es.close).toHaveBeenCalled();
  });

  it("onerror → error 状态（EventSource 自带重连，状态只反映当前）", () => {
    const f = fakeES();
    const stream = useEventStream(() => "u", () => f.es);
    stream.start();
    f.open();
    f.err();
    expect(stream.state.value).toBe("error");
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd mobile && pnpm vitest run src/api/events.test.ts`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现**

`mobile/src/api/events.ts`：

```typescript
import { ref, type Ref } from "vue";
import type { ConnConfig } from "./connection";

// 订阅的服务端事件名（P1 定的 SSE kind；未列的 kind 被丢弃——事件面小，列全即可）
export const KNOWN_KINDS = [
  "final_reply_chunk", "final_reply", "run_done", "interrupted", "error",
  "notice", "thinking", "speaking", "speaking_done", "confirmation_needed", "reminder",
] as const;

export interface EventSourceLike {
  addEventListener(k: string, cb: (e: { data: string }) => void): void;
  close(): void;
  onopen: (() => void) | null;
  onerror: (() => void) | null;
}

export function buildEventsUrl(c: ConnConfig): string {
  // token 走 query：EventSource 不能设自定义 header（P1 协议决定，TLS/局域网下可接受）
  return `${c.host}/v1/events?token=${encodeURIComponent(c.token)}`;
}

export function useEventStream(
  url: () => string,
  makeES: (u: string) => EventSourceLike = (u) => new EventSource(u) as unknown as EventSourceLike,
) {
  const state: Ref<"idle" | "connecting" | "open" | "error"> = ref("idle");
  let es: EventSourceLike | null = null;
  const handlers = new Map<string, Set<(data: any) => void>>();

  function on(kind: string, fn: (data: any) => void): () => void {
    if (!handlers.has(kind)) handlers.set(kind, new Set());
    handlers.get(kind)!.add(fn);
    return () => handlers.get(kind)?.delete(fn);
  }

  function start(): void {
    stop();
    es = makeES(url()); // 每次 start 重新取 url()：host/token 可能刚配对完
    state.value = "connecting";
    es.onopen = () => (state.value = "open");
    es.onerror = () => (state.value = "error"); // 原生 EventSource 自动重连，连上会再触发 onopen
    for (const kind of KNOWN_KINDS) {
      es.addEventListener(kind, (e) => {
        try {
          const data = JSON.parse(e.data);
          handlers.get(kind)?.forEach((fn) => fn(data));
        } catch {
          // 非 JSON data（理论不会发生）：静默丢弃
        }
      });
    }
  }

  function stop(): void {
    es?.close();
    es = null;
    state.value = "idle";
  }

  return { state, on, start, stop };
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd mobile && pnpm test && pnpm build`
Expected: 全 PASS

- [ ] **Step 5: 提交**

```bash
git add mobile/src
git commit -m "feat(mobile): SSE 事件流 composable——EventSource 包装/状态机/fake 注入测试"
```

---

### Task 6:（mobile）对话页（流式气泡 + 发送 + 打断）+ 连接状态条

**Files:**
- Create: `mobile/src/state/chat.ts`（useChat composable，纯逻辑可测）
- Create: `mobile/src/state/chat.test.ts`
- Create: `mobile/src/components/ConnBar.vue`
- Modify: `mobile/src/views/Chat.vue`（实装）

**Interfaces:**
- Consumes: Task 4 `ConnConfig/loadConn`、Task 5 `useEventStream/buildEventsUrl`
- Produces: `useChat()` 返回 `{ conn, stream, messages, busy, send(text), interrupt(), newChat(), error }`；`messages: Ref<Msg[]>`，`Msg = { role: "user" | "assistant"; text: string; done: boolean; interrupted?: boolean }`。ConnBar props：`state: "idle"|"connecting"|"open"|"error"`。

- [ ] **Step 1: 写失败测试**

`mobile/src/state/chat.test.ts`（fake stream/fetch，测纯逻辑）：

```typescript
import { describe, expect, it, vi } from "vitest";
import { useChat } from "./chat";
import type { EventSourceLike } from "../api/events";
import type { ConnConfig } from "../api/connection";

function mkChat() {
  const listeners = new Map<string, (e: { data: string }) => void>();
  const es: EventSourceLike = {
    addEventListener: (k, cb) => listeners.set(k, cb),
    close: vi.fn(),
    onopen: () => {},
    onerror: () => {},
  };
  const posts: any[] = [];
  const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
    posts.push({ url, body: JSON.parse(String(init?.body)) });
    return new Response(JSON.stringify({ ok: true, run_id: "mob_1", conversation_id: "" }), { status: 200 });
  });
  const chat = useChat(
    { host: "http://x", token: "t" } as ConnConfig,
    () => "u",
    () => es,
    fetchImpl as unknown as typeof fetch,
  );
  return { chat, emit: (k: string, data: unknown) => listeners.get(k)?.({ data: JSON.stringify(data) }), posts };
}

describe("useChat", () => {
  it("发送→chunk 流式拼接→final_reply 收口→run_done 置 done", async () => {
    const { chat, emit, posts } = mkChat();
    await chat.send("你好");
    expect(posts[0].body).toEqual({ text: "你好", conversation_id: chat.conversationId.value });
    expect(chat.messages.value.map((m) => m.role)).toEqual(["user", "assistant"]);
    emit("final_reply_chunk", { kind: "final_reply_chunk", text: "嗨", surface: "mobile" });
    emit("final_reply_chunk", { kind: "final_reply_chunk", text: "，我是译宝", surface: "mobile" });
    emit("final_reply_chunk", { kind: "final_reply_chunk", text: "桌面的", surface: "pet" }); // 桌面帧不进手机气泡
    expect(chat.messages.value[1].text).toBe("嗨，我是译宝");
    emit("final_reply", { kind: "final_reply", text: "嗨，我是译宝", surface: "mobile" });
    emit("run_done", { id: "mob_1" });
    expect(chat.messages.value[1].text).toBe("嗨，我是译宝");
    expect(chat.messages.value[1].done).toBe(true);
    expect(chat.busy.value).toBe(false);
  });

  it("interrupt → interrupted 帧收口；新对话换 conversation_id 且清空", async () => {
    const { chat, emit } = mkChat();
    await chat.send("长任务");
    chat.interrupt();
    emit("interrupted", { kind: "interrupted", surface: "mobile" });
    emit("run_done", { id: "mob_1" });
    const last = chat.messages.value[1];
    expect(last.done).toBe(true);
    expect(last.interrupted).toBe(true);
    const oldId = chat.conversationId.value;
    chat.newChat();
    expect(chat.conversationId.value).not.toBe(oldId);
    expect(chat.messages.value).toHaveLength(0);
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd mobile && pnpm vitest run src/state/chat.test.ts`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 useChat + ConnBar + Chat.vue**

`mobile/src/state/chat.ts`：

```typescript
import { computed, ref, type Ref } from "vue";
import { buildEventsUrl, useEventStream, type EventSourceLike } from "../api/events";
import type { ConnConfig } from "../api/connection";

export interface Msg {
  role: "user" | "assistant";
  text: string;
  done: boolean;
  interrupted?: boolean;
}

export function useChat(
  conn: ConnConfig,
  url: () => string = () => buildEventsUrl(conn),
  makeES: (u: string) => EventSourceLike = (u) => new EventSource(u) as unknown as EventSourceLike,
  fetchImpl: typeof fetch = fetch,
) {
  const messages: Ref<Msg[]> = ref([]);
  const conversationId = ref(crypto.randomUUID());
  const error = ref("");
  const stream = useEventStream(url, makeES);
  const busy = computed(() => messages.value.some((m) => m.role === "assistant" && !m.done));

  // 只认 surface==="mobile" 的帧（P1 信封字段；桌面/面板事件不进手机气泡）
  const mine = (d: { surface?: string }) => d.surface === "mobile";

  stream.on("final_reply_chunk", (d) => {
    if (!mine(d) || !d.text) return;
    const last = messages.value[messages.value.length - 1];
    if (last && last.role === "assistant" && !last.done) last.text += d.text;
  });
  stream.on("final_reply", (d) => {
    if (!mine(d)) return;
    const last = messages.value[messages.value.length - 1];
    if (last && last.role === "assistant") last.text = d.text ?? last.text;
  });
  stream.on("interrupted", (d) => {
    if (!mine(d)) return;
    const last = messages.value[messages.value.length - 1];
    if (last && last.role === "assistant") last.interrupted = true;
  });
  stream.on("run_done", () => {
    const last = messages.value[messages.value.length - 1];
    if (last && last.role === "assistant") last.done = true;
  });
  stream.on("error", (d) => {
    if (mine(d)) error.value = d.text ?? "大脑出错";
  });

  async function send(text: string): Promise<void> {
    const t = text.trim();
    if (!t || busy.value) return;
    error.value = "";
    messages.value.push({ role: "user", text: t, done: true }, { role: "assistant", text: "", done: false });
    try {
      const r = await fetchImpl(`${conn.host}/v1/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Yibao-Token": conn.token },
        body: JSON.stringify({ text: t, conversation_id: conversationId.value }),
      });
      if (!r.ok) throw new Error(`chat ${r.status}`);
    } catch (e) {
      error.value = `发送失败：${e instanceof Error ? e.message : "网络错误"}`;
      const last = messages.value[messages.value.length - 1];
      if (last?.role === "assistant") last.done = true;
    }
  }

  async function interrupt(): Promise<void> {
    try {
      await fetchImpl(`${conn.host}/v1/interrupt`, {
        method: "POST",
        headers: { "X-Yibao-Token": conn.token },
        body: "{}",
      });
    } catch { /* 状态由 interrupted/run_done 帧收敛 */ }
  }

  function newChat(): void {
    messages.value = [];
    conversationId.value = crypto.randomUUID();
    error.value = "";
  }

  return { conn, stream, messages, busy, conversationId, error, send, interrupt, newChat };
}
```

`mobile/src/components/ConnBar.vue`：

```vue
<script setup lang="ts">
defineProps<{ state: "idle" | "connecting" | "open" | "error" }>();
const TEXT = { idle: "未连接", connecting: "连接中…", open: "已连接", error: "连接断开，重连中…" } as const;
const COLOR = { idle: "#999", connecting: "#e6a700", open: "#34c759", error: "#ff453a" } as const;
</script>

<template>
  <div class="bar"><span class="dot" :style="{ background: COLOR[state] }" />{{ TEXT[state] }}</div>
</template>

<style scoped>
.bar { display: flex; align-items: center; gap: 6px; padding: 6px 14px; font-size: 12px; opacity: 0.8; }
.dot { width: 8px; height: 8px; border-radius: 50%; }
</style>
```

`mobile/src/views/Chat.vue`（实装替换占位；SSE 生命周期：onMounted start、onUnmounted stop）：

```vue
<script setup lang="ts">
import { onMounted, onUnmounted, ref, shallowRef } from "vue";
import { useRouter } from "vue-router";
import { loadConn } from "../api/connection";
import { useChat } from "../state/chat";
import ConnBar from "../components/ConnBar.vue";

const router = useRouter();
const input = ref("");
// shallowRef：onMounted 里赋值要触发重渲染（普通 let 赋值模板不更新）
const chat = shallowRef<ReturnType<typeof useChat> | null>(null);

onMounted(async () => {
  const conn = await loadConn();
  if (!conn) return router.replace("/pairing");
  chat.value = useChat(conn);
  chat.value.stream.start();
});
onUnmounted(() => chat?.stream.stop());

async function onSend() {
  if (!chat || !input.value.trim()) return;
  const t = input.value;
  input.value = "";
  await chat.send(t);
}
</script>

<template>
  <div class="chat" v-if="chat">
    <header class="head">
      <ConnBar :state="chat.stream.state.value" />
      <button class="ghost" @click="chat.newChat()">新对话</button>
    </header>
    <main class="list">
      <p v-for="(m, i) in chat.messages.value" :key="i" class="msg" :class="m.role">
        {{ m.text }}<span v-if="m.role === 'assistant' && !m.done" class="cursor">▍</span>
        <span v-if="m.interrupted" class="stopped">（已打断）</span>
      </p>
      <p v-if="chat.error.value" class="err">{{ chat.error.value }}</p>
    </main>
    <footer class="inputbar">
      <button v-if="chat.busy.value" class="stop" @click="chat.interrupt()">⏹</button>
      <input
        v-model="input"
        placeholder="对译宝说…"
        enterkeyhint="send"
        @keydown.enter.prevent="onSend"
        :disabled="chat.busy.value"
      />
      <button class="send" :disabled="!input.trim() || chat.busy.value" @click="onSend">发送</button>
    </footer>
  </div>
  <p v-else style="padding:24px">加载中…</p>
</template>

<style scoped>
.chat { display: flex; flex-direction: column; height: 100dvh; }
.head { display: flex; justify-content: space-between; align-items: center; }
.ghost { background: none; border: none; color: #2f6fed; font-size: 14px; }
.list { flex: 1; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 10px; }
.msg { max-width: 82%; padding: 10px 12px; border-radius: 14px; font-size: 15px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; }
.msg.user { align-self: flex-end; background: #2f6fed; color: #fff; }
.msg.assistant { align-self: flex-start; background: rgba(128, 128, 128, 0.16); }
.cursor { animation: blink 1s infinite; }
.stopped { font-size: 12px; opacity: 0.6; }
.err { color: #ff453a; font-size: 13px; }
.inputbar { display: flex; gap: 8px; padding: 10px 12px calc(10px + env(safe-area-inset-bottom)); }
.inputbar input { flex: 1; padding: 10px 12px; border-radius: 12px; border: 1px solid #ccc; background: transparent; color: inherit; font-size: 15px; }
.stop, .send { padding: 10px 14px; border-radius: 12px; border: none; }
.stop { background: #ff453a; color: #fff; }
.send { background: #2f6fed; color: #fff; }
.send:disabled { opacity: 0.4; }
@keyframes blink { 50% { opacity: 0; } }
</style>
```

- [ ] **Step 4: 跑测试 + 构建**

Run: `cd mobile && pnpm test && pnpm build`
Expected: 全 PASS

- [ ] **Step 5: 浏览器端到端手工验证（必须做）**

1. 起 sidecar：`cd sidecar && nohup sh -c 'tail -f /dev/null | uv run yibao-brain-server' > /tmp/yibao-server.log 2>&1 &`
2. 起 App：`cd mobile && pnpm dev` → 浏览器开 `http://localhost:5173`
3. 配对页填 `http://127.0.0.1:19527` + mobile token（取法见 P2 runbook）→ 测试连接 ✓ → 保存进入
4. 发「你好」→ 气泡逐字流式出现 → ⏹ 打断生效 → 新对话后 conversation_id 更换（服务端 stderr 可见 conv 变化）
5. `kill` sidecar → ConnBar 变「连接断开」；重启 sidecar → 自动回「已连接」（EventSource 原生重连）
6. 验完停 sidecar。

- [ ] **Step 6: 提交**

```bash
git add mobile/src
git commit -m "feat(mobile): 对话页——SSE 流式气泡/发送/打断/新对话 + 连接状态条"
```

---

### Task 7:（mobile）深链接线 + iOS 平台

**Files:**
- Modify: `mobile/src/App.vue`（appUrlOpen 监听）
- Create: `mobile/src/deeplink.test.ts`
- Create: `ios/`（`pnpm cap add ios` 生成，需 Xcode；Info.plist 注册 `yibao` scheme）

**Interfaces:**
- Consumes: Task 4 `parsePairUrl/saveConn`
- Produces: `wireDeepLink(onUrl: (u: string) => void): Promise<void>`（App.vue 用 @capacitor/app 的 appUrlOpen 接线，浏览器环境 no-op）；iOS 工程 + `yibao://` URL scheme 注册。spec §14 P3 两条（CORS/interrupt 收紧）勾销注记。

- [ ] **Step 1: 写失败测试**

`mobile/src/deeplink.test.ts`：

```typescript
import { describe, expect, it, vi } from "vitest";
import { handlePairUrl } from "./deeplink";

describe("handlePairUrl", () => {
  it("合法配对深链 → 保存并跳转 chat；非法 → 不动", async () => {
    const save = vi.fn();
    const push = vi.fn();
    const ok = await handlePairUrl("yibao://pair?host=http%3A%2F%2F127.0.0.1%3A19527&token=abc", { save, push });
    expect(ok).toBe(true);
    expect(save).toHaveBeenCalledWith({ host: "http://127.0.0.1:19527", token: "abc" });
    expect(push).toHaveBeenCalledWith("/chat");
    const bad = await handlePairUrl("yibao://chat", { save, push });
    expect(bad).toBe(false);
    expect(save).not.toHaveBeenCalledTimes(2);
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd mobile && pnpm vitest run src/deeplink.test.ts`
Expected: FAIL

- [ ] **Step 3: 实现**

`mobile/src/deeplink.ts`：

```typescript
import { parsePairUrl } from "./api/connection";

export async function handlePairUrl(
  url: string,
  io: { save: (c: { host: string; token: string }) => Promise<void>; push: (to: string) => void },
): Promise<boolean> {
  const c = parsePairUrl(url);
  if (!c) return false;
  await io.save(c);
  io.push("/chat");
  return true;
}
```

`mobile/src/App.vue`：

```vue
<script setup lang="ts">
import { onMounted } from "vue";
import { App as CapApp } from "@capacitor/app";
import { useRouter } from "vue-router";
import { handlePairUrl } from "./deeplink";
import { saveConn } from "./api/connection";

const router = useRouter();
onMounted(() => {
  // 浏览器 dev 无 appUrlOpen 事件，no-op；真机上扫桌面二维码 → yibao://pair → 自动配对
  CapApp.addListener("appUrlOpen", ({ url }) => {
    void handlePairUrl(url, { save: saveConn, push: (to) => router.replace(to) });
  });
});
</script>

<template>
  <router-view />
</template>
```

- [ ] **Step 4: iOS 平台 + scheme 注册**

```bash
cd mobile && pnpm build && pnpm cap add ios && pnpm cap sync ios
```
（需 Xcode + CocoaPods；若环境缺，报告注明并保留 Task 其余成果。）成功后编辑 `ios/App/App/Info.plist`，在根 dict 加：

```xml
<key>CFBundleURLTypes</key>
<array>
  <dict>
    <key>CFBundleURLName</key>
    <string>com.denny.yibao</string>
    <key>CFBundleURLSchemes</key>
    <array><string>yibao</string></array>
  </dict>
</array>
```

再 `pnpm cap sync ios`。`pnpm cap open ios` 能打开 Xcode 工程即通过（模拟器运行验证列为真机阶段项）。Android（`pnpm cap add android`）需 Android SDK——本机没有则记录 deferred，不阻塞。

- [ ] **Step 5: spec §14 勾销注记 + 提交**

`docs/superpowers/specs/2026-08-14-mobile-companion-design.md` §14 两条 P3 项后追加「（P3 已解决：Task 1 CORS / Task 2 running_surface）」；§5 深链行补「App.vue appUrlOpen 已接线（浏览器 no-op）」。

```bash
git add mobile/src mobile/ios docs/superpowers/specs/2026-08-14-mobile-companion-design.md
git commit -m "feat(mobile): 深链配对接线 + iOS 平台与 yibao scheme 注册"
```

---

## 验收（P3 完成定义）

- [ ] `cd mobile && pnpm test && pnpm build` 全绿（≥8 新用例）；`cd sidecar && uv run pytest -q` 全绿（+4 用例）
- [ ] 浏览器端到端（Task 6 Step 5 六步清单）通过
- [ ] iOS 工程存在且 `cap open ios` 可打开（Android 视 SDK 情况）
- [ ] 真机/外网联调留待联网方案恢复后补验（spec §13 注记）

## P4–P5 计划

P4（极光推送 + 深链审批页）依赖 Apple 开发者账号与真机，随联网方案一起排期；P5（分享模板 + 桌面设置页二维码 + token 热失效）里**设置页 token 区块不依赖外网**，可提前单独做——执行时按需拆 plan。
