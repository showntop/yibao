# 手机伴生端设置区块（P5 提前）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 桌面设置页新增「手机伴生端」区块：mobile token 管理（显示/复制/重置且热生效）、`http.bind` 局域网开关、配对二维码（手机扫码即连）；顺手修掉 spec §14 遗留的 token 热失效。

**Architecture:** sidecar 加 `http_pair_info` IPC（报内网 IP/端口/bind）+ build_app 的 token 改闭包读取（热生效）；桌面走既有 settings IPC 模式（Rust 命令 + brain.ts once/listen + SettingsView 区块），二维码用 `qrcode` npm 库渲染配对 URL。

**Spec:** `docs/superpowers/specs/2026-08-14-mobile-companion-design.md` §5 设置页、§14（token 热失效 P5 项）

## Global Constraints

- 桌面 IPC 全走既有模式：sidecar dispatch → Rust `write_to_brain`/事件转发 → brain.ts `once/listen` → Vue
- `settings_set` 只落已知键（http.bind 已在默认表）；bind 改动重启大脑生效（UI 注明）；token 重置经热生效修复后即时生效
- 二维码内容 v1 = 手机浏览器配对 URL（`http://<lan_ip>:5173/?host=&token=#/pairing`；原生 App 上线后换 `yibao://pair` 同码位）
- 测试：sidecar pytest 函数级 + asyncio.run；桌面 app vitest；Rust `cargo check` 过；中文注释文案

---

### Task 1:（server）http_pair_info IPC + token 热生效

**Files:**
- Modify: `sidecar/src/yibao_brain/http_api.py`（build_app 的 token 参数改闭包）
- Modify: `sidecar/src/yibao_brain/server.py`（_start_http_api 传闭包；dispatch 加 http_pair_info；`_lan_ip` helper）
- Test: `sidecar/tests/test_http_api.py`、`sidecar/tests/test_server.py`

**Interfaces:**
- Produces:
  - `build_app(*, get_bridge_token: Callable[[], str], get_mobile_token: Callable[[], str], tap, limiter=None, deps=None)`——auth 中间件每次请求现取 token（重置即生效）
  - stdio IPC `{"type":"http_pair_info"}` → `{"type":"http_pair_info","lan_ip":"192.168.x.x","port":19527,"bind":"0.0.0.0"}`

- [ ] **Step 1: 失败测试（热生效）**：test_http_api.py 追加——

```python
def test_token_hot_reload():
    async def main():
        toks = {"b": "btok", "m": "mtok"}
        app = build_app(get_bridge_token=lambda: toks["b"], get_mobile_token=lambda: toks["m"],
                        tap=EventTap(lambda m: None))
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            r = await client.get("/v1/health", headers={"X-Yibao-Token": "mtok"})
            assert r.status == 200
            toks["m"] = "newtok"  # 桌面重置 token → 立即生效
            r = await client.get("/v1/health", headers={"X-Yibao-Token": "mtok"})
            assert r.status == 401
            r = await client.get("/v1/health", headers={"X-Yibao-Token": "newtok"})
            assert r.status == 200
        finally:
            await client.close()

    asyncio.run(main())
```

同时把 test_http_api.py 里所有 `build_app(bridge_token="btok", mobile_token="mtok", ...)` 调用改为 `build_app(get_bridge_token=lambda: "btok", get_mobile_token=lambda: "mtok", ...)`（约 10 处）。

- [ ] **Step 2: 失败测试（http_pair_info）**：test_server.py 追加（沿用 make_reader + out 捕获模式）——

```python
def test_http_pair_info_ipc(tmp_path):
    async def main():
        out = []
        serve_task = asyncio.ensure_future(serve_async(
            make_reader([{"id": 1, "type": "http_pair_info"}]),
            lambda m: out.append(m), use_real=False, db_path=str(tmp_path / "p.db"),
            provider=FakeProvider(text="x")))
        await asyncio.wait_for(serve_task, 5)
        msg = next(m for m in out if m.get("type") == "http_pair_info")
        assert msg["port"] == 19527 and msg["bind"] == "127.0.0.1"
        assert isinstance(msg["lan_ip"], str)  # 环境相关（可能空串），只断言类型与格式

    asyncio.run(main())
```

- [ ] **Step 3: 跑红**：`cd sidecar && uv run --extra dev pytest tests/test_http_api.py tests/test_server.py -q` → 新用例 FAIL（签名不符 / 消息无响应）

- [ ] **Step 4: 实现**
  - http_api.py：`build_app` 两参数改 `get_bridge_token: Callable[[], str]` / `get_mobile_token: Callable[[], str]`；`_auth` 里 `expected = (get_mobile_token() if mobile else get_bridge_token()).encode()`（若空串则照常比对失败）
  - server.py `_start_http_api`：`_ensure_http_token` 保留（启动兜底生成），传 `get_bridge_token=lambda: str(settings.get("http.token") or "")`、mobile 同理
  - server.py 模块级加 `_lan_ip()`：

```python
def _lan_ip() -> str:
    """内网 IPv4（配对 URL 用）：UDP connect 到 RFC5737 地址做路由选择，不实际发包。"""
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 1))
        return s.getsockname()[0]
    except OSError:
        return ""
    finally:
        s.close()
```

  - dispatch（settings_get 旁）加：

```python
        elif rtype == "http_pair_info":
            write_msg({"type": "http_pair_info", "lan_ip": _lan_ip(),
                       "port": http_port(), "bind": str(settings.get("http.bind") or "127.0.0.1")})
```

- [ ] **Step 5: 全量绿 + 提交**：`uv run --extra dev pytest -x -q` → `feat(server): http_pair_info IPC + HTTP 面 token 热生效`

---

### Task 2:（desktop）Rust 命令 + brain.ts + 设置区块 + 二维码

**Files:**
- Modify: `app/src-tauri/src/lib.rs`（:639 settings 转发旁加 http_pair_info → brain-http-pair-info；get_settings 旁加命令 + invoke_handler 注册）
- Modify: `app/src/lib/brain.ts`（getSettingsOnce 旁加 HttpPairInfo + getHttpPairInfoOnce）
- Create: `app/src/lib/pair.ts` + `app/src/lib/pair.test.ts`（配对 URL 纯函数）
- Modify: `app/src/components/SettingsView.vue`（浏览器扩展区块（:690-706）后加「手机伴生端」section）
- Modify: `app/package.json`（+`qrcode`、devDep `@types/qrcode`）

**Interfaces:**
- Consumes: Task 1 的 `{"type":"http_pair_info"}` IPC；既有 settings_get/set 通道（token 与 bind 都从 settings 快照走）
- Produces: `buildPairUrl(lanIp: string, port: number, token: string): string`（`http://<ip>:5173/?host=<enc>&token=<enc>#/pairing`，host 为 `http://<ip>:<port>`）；设置页区块三件套

- [ ] **Step 1: 失败测试**：app/src/lib/pair.test.ts——

```typescript
import { describe, expect, it } from "vitest";
import { buildPairUrl } from "./pair";

describe("buildPairUrl", () => {
  it("拼出带预填参数的配对 URL", () => {
    expect(buildPairUrl("192.168.31.52", 19527, "a b&c"))
      .toBe("http://192.168.31.52:5173/?host=http%3A%2F%2F192.168.31.52%3A19527&token=a%20b%26c#/pairing");
  });
  it("无内网 IP（离线/仅公网）返回空串（UI 隐藏二维码）", () => {
    expect(buildPairUrl("", 19527, "t")).toBe("");
  });
});
```

- [ ] **Step 2: 实现 pair.ts**：

```typescript
/** 手机浏览器配对 URL（预填 host/token；原生 App 上线后此处换 yibao://pair 深链）。 */
export function buildPairUrl(lanIp: string, port: number, token: string): string {
  if (!lanIp) return "";
  const host = encodeURIComponent(`http://${lanIp}:${port}`);
  return `http://${lanIp}:5173/?host=${host}&token=${encodeURIComponent(token)}#/pairing`;
}
```

- [ ] **Step 3: Rust + brain.ts**（对齐既有模式）：
  - lib.rs :639 旁：`Some("http_pair_info") => { let _ = app.emit("brain-http-pair-info", v); }`
  - lib.rs get_settings 命令旁：

```rust
/// 手机伴生端配对信息（回 {"type":"http_pair_info"} 经 brain-http-pair-info 广播）。
#[tauri::command]
fn get_http_pair_info(state: tauri::State<Brain>) -> Result<(), String> {
    write_to_brain(&state, serde_json::json!({ "id": 0, "type": "http_pair_info" }))
}
```

  - invoke_handler 的 generate_handler 列表加 `get_http_pair_info`
  - brain.ts getSettingsOnce 旁：

```typescript
export interface HttpPairInfo {
  lan_ip: string;
  port: number;
  bind: string;
}

/** 一次性取手机伴生端配对信息；超时返回 null。 */
export async function getHttpPairInfoOnce(timeoutMs = 3000): Promise<HttpPairInfo | null> {
  return new Promise((resolve) => {
    void once<HttpPairInfo>("brain-http-pair-info", (ev) => resolve(ev.payload));
    setTimeout(() => resolve(null), timeoutMs);
    void invoke("get_http_pair_info");
  });
}
```

（brain.ts 里 once/listen/invoke 的现有 import 与泛型签名照抄邻近代码；若 brain-settings 的事件负载形如 {values} 则此处 payload 直接是 HttpPairInfo——以 lib.rs `emit("brain-http-pair-info", v)` 的 v 为准，v 即整个消息 dict，取字段时按 `ev.payload.lan_ip` 类似 brain-settings 的取法核对层级，必要时剥层。）

- [ ] **Step 4: SettingsView.vue 区块**（:706 浏览器扩展 section 后插入，样式类全部复用现有 s-* 体系）：
  - script：`mobileToken`（从 settings 快照 http.mobile_token，:533 旁同法）、`lanInfo = ref<HttpPairInfo | null>`（onMounted getHttpPairInfoOnce + 每次分类切入刷新）、`pairQr = ref("")`、`buildQr()`（QRCode.toDataURL(buildPairUrl(...)) → pairQr；qrcode import：`import QRCode from "qrcode"`）；「重置」= `setSettings({"http.mobile_token": <32位随机hex>})`（`crypto.randomUUID().replace(/-/g,"")`）后刷新快照 + 提示「已重置并即时生效，旧手机需重新配对」；「局域网」switch：`setSettings({"http.bind": on ? "0.0.0.0" : "127.0.0.1"})` + 提示「重启大脑后生效」
  - template：三行——token（masked/显示/复制/重置，照抄 bridgeToken 那行的结构）、局域网开关（照抄 watchEnabled switch 行）、二维码（`<img v-if="pairQr" :src="pairQr" />` 120px + 说明「手机与电脑同一 WiFi，扫码直达配对」；lanInfo 为 null 或 lan_ip 空时显示提示行不显示码）
  - `cd app && pnpm add qrcode && pnpm add -D @types/qrcode`

- [ ] **Step 5: 验证 + 提交**：`cd app && pnpm test && npx vue-tsc --noEmit && cd src-tauri && cargo check` 全绿 → `feat(desktop): 设置页手机伴生端区块——token 热重置/局域网开关/配对二维码`

---

## 验收

- [ ] sidecar 全量 pytest 绿（含热生效 + pair_info 两新用例）
- [ ] app vitest 绿（pair.test 2 用例）+ vue-tsc + cargo check 净
- [ ] 真机（手机浏览器）扫设置页二维码 → 直达配对页预填好 → 保存进入（用户验收）
