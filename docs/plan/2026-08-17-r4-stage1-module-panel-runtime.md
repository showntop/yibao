# R4 阶段一:插件前端运行时(module 面板)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把插件面板从「启动时读进内存的单文件 HTML 字符串」改为「plugins/ 下独立构建的静态 bundle,运行时经 `yibao-plugin://` 自定义协议按需加载」,并用 coding:studio hello 骨架打通全链路。

**Architecture:** sidecar 注册 module 面板只登记入口路径(不读全文),panel_payload 下发 `{url, v=mtime}` 引用;Rust 注册 `yibao-plugin://` 协议从 plugins/ 只读服务文件(防穿越 + CSP/SDK 注入 + CORS 头);前端 WebviewPanel 按 payload 分叉(url→iframe src / html→srcdoc);插件面板为多文件工程,共享构建脚本外置 Vue(importmap 由协议层注入)。

**Tech Stack:** Python(sidecar, pytest)、Rust(Tauri 2, cargo test)、Vue 3.5 + Vite 6 + TS(app, pnpm + vitest)。

**Spec:** docs/superpowers/specs/2026-08-17-coding-studio-r4-design.md(本计划只覆盖落地顺序第 1 步;后续阶段各自出计划)

**后续阶段计划清单**(本计划不含):阶段二 coding 前端重写(单工位能力对齐)、阶段三多工位(工位区+左栏+聚焦路由输入条)、阶段四统一 review 栏、阶段五收口(墙退役+留档小修四件)。

## Global Constraints

- 包管理/测试命令:app 用 pnpm(`pnpm test` = vitest run,`pnpm build` = vue-tsc --noEmit && vite build);sidecar 用 `cd sidecar && .venv/bin/pytest`;Rust 用 `cd app/src-tauri && cargo test`
- 旧 srcdoc 链路(webview/schema/widget 面板、gen 面板)行为不许变:tools.html/editor.html/各 schema 面板零回归
- 安全模型不变:sandbox iframe(仅 `allow-scripts`)+ 方法调用经 api.toml 白名单裁决;module 面板 CSP 禁网络(`connect-src 'none'`)
- Vue 由宿主在协议源共享:serve 的是 **runtime-only** 构建(`vue.runtime.esm-browser.prod.js`,SFC 预编译,无 runtime compiler → CSP 不需要 unsafe-eval)
- 注释/提交信息中文,conventional 前缀(如 `feat:` `fix:` `docs:`),每个 Task 结束独立 commit
- 面板进程热加载的兑现方式:payload `v` = 入口文件 mtime,前端 iframe `:key` 含 `v`——文件变 → key 变 → iframe 重载,sidecar 不重启

---

### Task 1: sidecar — module 面板注册 + 引用式 payload

**Files:**
- Modify: `sidecar/src/yibao_brain/plugins.py`(`_PANELS` 区 :298-336、`panel_payload` :361-375、`_load_panels` :404-448)
- Test: `sidecar/tests/test_module_panels.py`(新建)

**Interfaces:**
- Consumes: 现有 `_load_panels(child: Path, pid: str, manifest: dict, registry)`、`panel_payload(result)`(result 有 `.panel: str|None`、`.data: dict`)
- Produces: `_PLUGIN_DIRS: dict[str, Path]`(pid → 插件根目录);module 面板注册形状 `{"type": "module", "entry": "<相对路径>", "surfaces": [...], "min_width"?: int}`;`panel_payload` 对 module 面板返回 `{"panel", "title", "schema": None, "webview": {"url": "yibao-plugin://<pid>/<entry>", "v": <mtime int>}, "data", ...}`(Task 4 前端消费此形状)

- [ ] **Step 1: 写失败测试**

新建 `sidecar/tests/test_module_panels.py`:

```python
"""module 面板(R4 插件运行时):manifest type="module" 注册 + 引用式 payload。"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from yibao_brain import plugins


@pytest.fixture(autouse=True)
def _clean_registry():
    """_PANELS 等是进程级注册表,逐个测试后清掉本文件注册的 ref,防串扰。"""
    yield
    for ref in ("demo:main", "demo2:w", "demo2:s"):
        plugins._PANELS.pop(ref, None)
        plugins._PANEL_TITLES.pop(ref, None)
    plugins._PLUGIN_DIRS.pop("demo", None)
    plugins._PLUGIN_DIRS.pop("demo2", None)


def _make_plugin(tmp_path: Path, name: str = "demo", *, with_dist: bool = True) -> Path:
    child = tmp_path / name
    (child / "panel" / "dist").mkdir(parents=True)
    if with_dist:
        (child / "panel" / "dist" / "index.html").write_text("<html>hi</html>", encoding="utf-8")
    return child


def _manifest() -> dict:
    return {
        "name": "演示",
        "panel": [{"type": "module", "name": "main", "label": "演示面板", "src": "panel/dist/index.html"}],
    }


def test_module_panel_registered_without_reading_file(tmp_path):
    child = _make_plugin(tmp_path)
    plugins._load_panels(child, "demo", _manifest(), None)  # registry 仅 widget 分支用,module 传 None
    panel = plugins.get_panel("demo:main")
    assert panel["type"] == "module"
    assert panel["entry"] == "panel/dist/index.html"
    assert "html" not in panel          # 不读全文进内存
    assert isinstance(panel["surfaces"], list) and panel["surfaces"]  # 表面声明逻辑与既有面板一致


def test_module_payload_is_reference_with_mtime(tmp_path):
    child = _make_plugin(tmp_path)
    plugins._load_panels(child, "demo", _manifest(), None)
    payload = plugins.panel_payload(SimpleNamespace(panel="demo:main", data={"x": 1}))
    assert payload["schema"] is None
    assert payload["webview"]["url"] == "yibao-plugin://demo/panel/dist/index.html"
    assert payload["webview"]["v"] > 0  # mtime 作版本号
    assert "html" not in payload["webview"]
    assert payload["data"] == {"x": 1}


def test_module_payload_missing_dist_still_registered_v0(tmp_path):
    # dist 未构建时也登记(先声明后构建的 dev 流程),payload v=0 由前端照样加载(404 由协议层兜底)
    child = _make_plugin(tmp_path, with_dist=False)
    plugins._load_panels(child, "demo", _manifest(), None)
    payload = plugins.panel_payload(SimpleNamespace(panel="demo:main", data={}))
    assert payload["webview"]["url"] == "yibao-plugin://demo/panel/dist/index.html"
    assert payload["webview"]["v"] == 0


def test_legacy_webview_and_schema_panels_unchanged(tmp_path):
    # 回归:旧类型仍读全文 / 解析 JSON
    child = _make_plugin(tmp_path, name="demo2")
    (child / "panel" / "a.html").write_text("<html>old</html>", encoding="utf-8")
    (child / "panel" / "b.schema.json").write_text('{"type": "list", "bind": {"items": "$data.rows"}}', encoding="utf-8")
    manifest = {"name": "旧", "panel": [
        {"type": "webview", "name": "w", "src": "panel/a.html"},
        {"type": "schema", "name": "s", "src": "panel/b.schema.json"},
    ]}
    plugins._load_panels(child, "demo2", manifest, None)
    assert plugins.get_panel("demo2:w") == {"type": "webview", "html": "<html>old</html>",
                                           "surfaces": plugins.get_panel("demo2:w")["surfaces"]}
    assert plugins.get_panel("demo2:s")["type"] == "list"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd sidecar && .venv/bin/pytest tests/test_module_panels.py -v`
Expected: FAIL(collection 期 `AttributeError: module 'yibao_brain.plugins' has no attribute '_PLUGIN_DIRS'` 或断言失败——module 类型还没实现)

- [ ] **Step 3: 实现**

`plugins.py` 三处改动。

(1) `_PANELS` 区(:298 附近)加插件目录登记:

```python
_PANELS: dict[str, dict] = {}
_PANEL_TITLES: dict[str, str] = {}
_PLUGIN_INFO: dict[str, dict] = {}
_PLUGIN_DIRS: dict[str, Path] = {}  # pid → 插件根目录(module 面板 payload 算 url/mtime 用)
```

(2) `panel_payload`(:361-375)docstring 补一句 module 形状,并在 webview 分支前加 module 分支:

```python
def panel_payload(result) -> dict | None:
    """result.panel 非空时构造 panel 事件 payload(loop 与 panel_action 共用)。

    schema 面板:{panel, title, schema, data};webview 面板:{..., webview: {html}, data};
    module 面板(R4):{..., webview: {url, v}, data}——url 指 yibao-plugin:// 协议,v 为入口文件
    mtime(热加载版本号:文件变 → v 变 → 前端 iframe 重载,sidecar 不重启)。
    面板声明过 surfaces/min_width 时顶层同步带上(宿主裁决回落依据)。
    """
    if not result.panel:
        return None
    title = get_panel_title(result.panel)
    panel = get_panel(result.panel)
    decl = _surface_decl_from(panel)
    if isinstance(panel, dict) and panel.get("type") == "module":
        pid = result.panel.split(":", 1)[0]
        entry = str(panel.get("entry") or "")
        v = 0
        root = _PLUGIN_DIRS.get(pid)
        if root is not None and entry:
            try:
                v = int((root / entry).stat().st_mtime)
            except OSError:
                v = 0
        return {"panel": result.panel, "title": title, "schema": None,
                "webview": {"url": f"yibao-plugin://{pid}/{entry}", "v": v},
                "data": result.data, **decl}
    if isinstance(panel, dict) and panel.get("type") == "webview" and "html" in panel:
        return {"panel": result.panel, "title": title, "schema": None, "webview": {"html": panel["html"]}, "data": result.data, **decl}
    return {"panel": result.panel, "title": title, "schema": panel, "data": result.data, **decl}
```

(3) `_load_panels`(:404-448):类型白名单加 module、登记 `_PLUGIN_DIRS`、module 分支不读文件:

```python
    for p in manifest.get("panel") or []:
        name = p.get("name") or "main"
        ref = f"{pid}:{name}"
        ptype = p.get("type", "schema")
        if ptype not in ("schema", "webview", "widget", "module"):
            print(f"[yibao] 插件 {pid} panel {ref} 类型 {ptype!r} 暂不支持(已跳过)", file=sys.stderr)
            continue
        _PANEL_TITLES[ref] = f"{manifest.get('name') or pid} · {p.get('label') or name}"
        _PLUGIN_DIRS[pid] = child
        if ptype == "module":
            # module 面板(R4 插件运行时):不读文件全文,只登记入口相对路径;
            # 内容经 yibao-plugin:// 协议按需下发。dist 未构建也先登记(dev 先声明后构建)。
            entry = str(p.get("src") or "")
            if not entry:
                _PANEL_TITLES.pop(ref, None)
                print(f"[yibao] 插件 {pid} panel {ref} 缺 src(已跳过)", file=sys.stderr)
                continue
            if not (child / entry).is_file():
                print(f"[yibao] 插件 {pid} panel {ref} 入口文件暂缺(已登记,构建后生效):{entry}", file=sys.stderr)
            parsed = {"type": "module", "entry": entry}
        else:
            try:
                text = _inline_vendor((child / p["src"]).read_text(encoding="utf-8"), child)
                parsed = {"type": "webview", "html": text} if ptype == "webview" else json.loads(text)
            except Exception as e:
                print(f"[yibao] 插件 {pid} panel {ref} 读取失败(已跳过):{e}", file=sys.stderr)
                continue
        # (以下原有 widget 分支与 surfaces/min_width 块原样保留)
```

同时把 `get_panel` 的 docstring(:316-318)更新为:`"""按「plugin_id:name」查面板。schema 面板为 JSON dict;webview 面板为 {"type": "webview", "html": …};module 面板为 {"type": "module", "entry": …}。"""`

- [ ] **Step 4: 跑测试确认通过**

Run: `cd sidecar && .venv/bin/pytest tests/test_module_panels.py -v`
Expected: 4 passed

- [ ] **Step 5: 回归 + commit**

Run: `cd sidecar && .venv/bin/pytest tests/test_genpanel.py tests/test_coding_plugin.py -v`
Expected: 全绿(既有面板链路无回归)

```bash
git add sidecar/src/yibao_brain/plugins.py sidecar/tests/test_module_panels.py
git commit -m "feat: module 面板注册与引用式 payload(sidecar,R4 插件运行时)"
```

---

### Task 2: 桥 SDK 单源化(抽出 bridge.js)

**Files:**
- Create: `app/src/shared/bridge.js`
- Modify: `app/src/components/WebviewPanel.vue`(:28-70 BRIDGE_JS 内联常量 → import)

**Interfaces:**
- Consumes: 无(纯搬移,行为零变化)
- Produces: `app/src/shared/bridge.js`——Task 3 的 Rust 协议层 `include_bytes!` 同一文件(单一事实源);文件内导出 `window.yibao`(invoke/onInit/emitEvent/onMessage)+ `window.YIBAO_BRIDGE_VERSION = 1`

- [ ] **Step 1: 抽出 bridge.js**

新建 `app/src/shared/bridge.js`(内容 = WebviewPanel.vue 现有 BRIDGE_JS 字符串原文,顶部加版本号;注意本文件是独立 JS 不再是 Vue SFC 内字符串,可含任意字面量):

```js
// 译宝插件面板桥 SDK(单一事实源)。
// 两条注入路径共用本文件:旧 srcdoc 面板由 WebviewPanel 内联注入(?raw import);
// module 面板由 Rust yibao-plugin:// 协议层 serve /__yibao__/bridge.js(include_bytes!)。
// 必须出现在插件自有脚本之前(协议层注入到 <head> 之后),否则插件脚本执行时 window.yibao 未定义。
(function () {
  window.YIBAO_BRIDGE_VERSION = 1;
  var seq = 0;
  var pending = new Map();
  var initCbs = [];
  var msgCbs = [];
  window.yibao = {
    invoke: function (method, params) {
      return new Promise(function (resolve, reject) {
        var id = ++seq;
        pending.set(id, { resolve: resolve, reject: reject });
        parent.postMessage({ src: "yibao-webview", id: id, method: method, params: params || {} }, "*");
      });
    },
    onInit: function (cb) { initCbs.push(cb); },
    // 事件上报(iframe → 父,无 id 无回包):父侧 emit("panel-event", name, payload)
    emitEvent: function (name, payload) {
      parent.postMessage({ src: "yibao-webview", event: name, payload: payload }, "*");
    },
    // 收 host 任意消息(init 与 invoke 响应之外的,如 {type:"takeover-input", text})
    onMessage: function (cb) { msgCbs.push(cb); }
  };
  window.addEventListener("message", function (ev) {
    var d = ev.data;
    if (!d || d.src !== "yibao-host") return;
    if (d.type === "init") {
      initCbs.forEach(function (cb) { try { cb(d.data, d); } catch (e) { console.error(e); } });
      return;
    }
    var p = pending.get(d.id);
    if (p) {
      pending.delete(d.id);
      if (d.ok) p.resolve(d.result);
      else p.reject(new Error(d.error || "调用失败"));
      return;
    }
    msgCbs.forEach(function (cb) { try { cb(d); } catch (e) { console.error(e); } });
  });
})();
```

- [ ] **Step 2: WebviewPanel 改用 import**

`WebviewPanel.vue` 删除 :31-70 的 `const BRIDGE_JS = \`...\`` 内联常量(含 :28-30 的注释),替换为:

```ts
// 注入 iframe 的桥 JS(?raw 读 app/src/shared/bridge.js,与 Rust 协议层 include_bytes! 同一文件)。
// 必须出现在插件自有脚本之前——注入到 <head> 之后(无 <head> 则放最前),见 srcdoc computed。
import bridgeJs from "../shared/bridge.js?raw";
```

`srcdoc` computed(:80-86)里 `BRIDGE_JS` 引用改为 `bridgeJs`。

若 `vue-tsc` 报 `Cannot find module '..../bridge.js?raw'`:确认 `app/src/vite-env.d.ts` 有 `/// <reference types="vite/client" />`(没有就新建该行)。

- [ ] **Step 3: 验证(类型检查 + 既有测试全绿)**

Run: `cd app && pnpm build`
Expected: vue-tsc 无错,vite build 成功

Run: `cd app && pnpm test`
Expected: 全绿(本任务无新行为,不新增测试;桥行为由 Task 5 端到端验收覆盖)

- [ ] **Step 4: Commit**

```bash
git add app/src/shared/bridge.js app/src/components/WebviewPanel.vue app/src/vite-env.d.ts
git commit -m "refactor: 桥 SDK 抽成单源 bridge.js(srcdoc 内联与协议层共用)"
```

---

### Task 3: Rust — yibao-plugin:// 自定义协议 + plugins_dir prod 修复

**Files:**
- Create: `app/src-tauri/src/plugin_proto.rs`
- Create: `app/src-tauri/resources/sdk/vue.esm-browser.js`(vendor 自 app/node_modules/vue/dist/vue.runtime.esm-browser.prod.js)
- Modify: `app/src-tauri/src/lib.rs`(注册协议 + setup 里 plugins_dir 修复)

**Interfaces:**
- Consumes: `app/src/shared/bridge.js`(Task 2);`plugins_dir()`(lib.rs:1214)
- Produces: `plugin_proto::handle(request: &tauri::http::Request<Vec<u8>>, plugins_root: &Path) -> tauri::http::Response<Cow<'static, [u8]>>`;URL 约定 `yibao-plugin://<pid>/<相对路径>`;保留路径 `/<pid>/__yibao__/bridge.js`、`/<pid>/__yibao__/vue.esm-browser.js`(宿主 SDK,任意 pid 下都可访问);importmap 约定 `{"imports":{"vue":"/__yibao__/vue.esm-browser.js"}}`(Task 5 插件工程依赖)

- [ ] **Step 1: vendor Vue runtime**

```bash
mkdir -p app/src-tauri/resources/sdk
cp app/node_modules/vue/dist/vue.runtime.esm-browser.prod.js app/src-tauri/resources/sdk/vue.esm-browser.js
```

(选 runtime-only 构建:插件 SFC 经构建链预编译,无 runtime compiler → CSP 不需要 unsafe-eval。URL 名保持 vue.esm-browser.js 通用,内容与 app 的 vue 版本同源。)

- [ ] **Step 2: 写 plugin_proto.rs(含 #[cfg(test)] 失败测试先行)**

新建 `app/src-tauri/src/plugin_proto.rs`,先只写测试部分,`cargo test` 确认编译失败/测试失败,再补实现。完整文件内容(实现+测试)如下,TDD 顺序:先粘 `#[cfg(test)]` 块 + 空函数签名跑失败,再填实现体:

```rust
//! yibao-plugin:// 自定义协议:运行时从 plugins/ 目录只读服务 module 面板静态资源。
//!
//! 安全模型:
//! - 路径防穿越:pid 白名单字符 + 拒绝非 Normal 路径段(含 `..`)+ canonicalize 后前缀校验
//! - CSP 注入(仅 HTML):default-src 'none',脚本/样式/图片/字体仅放行 yibao-plugin: 与 data:,
//!   connect-src 'none' 断网络——插件对外通信只有 postMessage 桥(父侧再过 api.toml 白名单)
//! - Access-Control-Allow-Origin: *:sandbox iframe 是无 allow-same-origin 的透明源(opaque origin),
//!   ES module 加载走 CORS 模式,没有本头插件自己的 bundle 会被源校验拦下(与 vscode webview 同手法)
//! - /__yibao__/ 为宿主保留路径(桥 SDK / 共享 Vue runtime),由 include_bytes! 内嵌,不读盘

use std::borrow::Cow;
use std::path::{Component, Path, PathBuf};
use tauri::http::{Request, Response};

const BRIDGE_JS: &[u8] = include_bytes!(concat!(env!("CARGO_MANIFEST_DIR"), "/../src/shared/bridge.js"));
const VUE_JS: &[u8] = include_bytes!(concat!(env!("CARGO_MANIFEST_DIR"), "/resources/sdk/vue.esm-browser.js"));

const CSP_META: &str = "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; script-src yibao-plugin: 'unsafe-inline'; style-src yibao-plugin: 'unsafe-inline'; img-src yibao-plugin: data:; font-src yibao-plugin: data:; connect-src 'none'; object-src 'none'; base-uri 'none'\">";
const IMPORTMAP: &str = "<script type=\"importmap\">{\"imports\":{\"vue\":\"/__yibao__/vue.esm-browser.js\"}}</script>";
const BRIDGE_TAG: &str = "<script src=\"/__yibao__/bridge.js\"></script>";

fn mime_for(path: &Path) -> &'static str {
    match path.extension().and_then(|e| e.to_str()).unwrap_or("").to_ascii_lowercase().as_str() {
        "html" | "htm" => "text/html; charset=utf-8",
        "js" | "mjs" => "text/javascript; charset=utf-8",
        "css" => "text/css; charset=utf-8",
        "json" | "map" => "application/json; charset=utf-8",
        "svg" => "image/svg+xml",
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "gif" => "image/gif",
        "webp" => "image/webp",
        "woff" => "font/woff",
        "woff2" => "font/woff2",
        "ttf" => "font/ttf",
        "txt" => "text/plain; charset=utf-8",
        _ => "application/octet-stream",
    }
}

fn valid_pid(pid: &str) -> bool {
    !pid.is_empty() && pid.len() <= 64 && pid.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-' || c == '_')
}

/// 拼接并校验插件内相对路径:拒绝 `..`/根路径等非常规段;canonicalize 后必须仍在插件目录内。
/// 文件不存在 → None(调用方回 404)。
fn resolve_plugin_path(root: &Path, pid: &str, rel: &str) -> Option<PathBuf> {
    if !valid_pid(pid) || rel.is_empty() {
        return None;
    }
    let rel_path = Path::new(rel);
    if !rel_path.components().all(|c| matches!(c, Component::Normal(_))) {
        return None;
    }
    let base = root.join(pid).canonicalize().ok()?;
    let full = base.join(rel_path).canonicalize().ok()?;
    if full.starts_with(&base) { Some(full) } else { None }
}

/// 最小 %XX 解码(静态资源路径只需这个);非法序列/非 UTF-8 → None(按 404 处理)。
fn pct_decode(s: &str) -> Option<String> {
    let bytes = s.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' {
            if i + 3 > bytes.len() {
                return None;
            }
            let h = u8::from_str_radix(s.get(i + 1..i + 3)?, 16).ok()?;
            out.push(h);
            i += 3;
        } else {
            out.push(bytes[i]);
            i += 1;
        }
    }
    String::from_utf8(out).ok()
}

/// 在 <head> 之后注入 CSP + importmap + 桥 SDK(无 <head> 则放最前)——与 WebviewPanel srcdoc 注入同手法。
/// 在原串上做 ASCII 大小写不敏感查找:to_lowercase() 会改变字节长度,索引回切原串会错位/越界。
fn inject_sdk(html: &str) -> String {
    let tag = format!("{CSP_META}{IMPORTMAP}{BRIDGE_TAG}");
    let bytes = html.as_bytes();
    let needle = b"<head>";
    let pos = bytes
        .windows(needle.len())
        .position(|w| w.eq_ignore_ascii_case(needle));
    match pos {
        // 命中段是纯 ASCII,索引必落在字符边界上;保留原串标签大小写
        Some(i) => format!("{}{}{}", &html[..i + needle.len()], tag, &html[i + needle.len()..]),
        None => format!("{tag}{html}"),
    }
}

fn respond(status: u16, mime: &str, body: Cow<'static, [u8]>) -> Response<Cow<'static, [u8]>> {
    Response::builder()
        .status(status)
        .header("Content-Type", mime)
        .header("Access-Control-Allow-Origin", "*")
        .header("Cache-Control", "no-store") // mtime 版本号热加载的前提:响应不许缓存
        .body(body)
        .expect("protocol response build")
}

pub fn handle(request: &Request<Vec<u8>>, plugins_root: &Path) -> Response<Cow<'static, [u8]>> {
    let pid = request.uri().host().unwrap_or("");
    let raw = request.uri().path().trim_start_matches('/');
    if raw.starts_with("__yibao__/") {
        return match raw {
            "__yibao__/bridge.js" => respond(200, "text/javascript; charset=utf-8", Cow::Borrowed(BRIDGE_JS)),
            "__yibao__/vue.esm-browser.js" => respond(200, "text/javascript; charset=utf-8", Cow::Borrowed(VUE_JS)),
            _ => respond(404, "text/plain; charset=utf-8", Cow::Borrowed(&b"not found"[..])),
        };
    }
    let path = pct_decode(raw).and_then(|rel| resolve_plugin_path(plugins_root, pid, &rel));
    let Some(path) = path else {
        return respond(404, "text/plain; charset=utf-8", Cow::Borrowed(&b"not found"[..]));
    };
    let mime = mime_for(&path);
    match std::fs::read(&path) {
        Ok(bytes) if mime.starts_with("text/html") => match String::from_utf8(bytes) {
            Ok(html) => respond(200, mime, Cow::Owned(inject_sdk(&html).into_bytes())),
            Err(_) => respond(500, "text/plain; charset=utf-8", Cow::Borrowed(&b"bad html"[..])),
        },
        Ok(bytes) => respond(200, mime, Cow::Owned(bytes)),
        Err(_) => respond(404, "text/plain; charset=utf-8", Cow::Borrowed(&b"not found"[..])),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn req(uri: &str) -> Request<Vec<u8>> {
        Request::builder().uri(uri).body(Vec::new()).unwrap()
    }

    fn fixture_root() -> PathBuf {
        let root = std::env::temp_dir().join(format!("yibao-proto-test-{}", std::process::id()));
        let dist = root.join("demo/panel/dist");
        std::fs::create_dir_all(&dist).unwrap();
        std::fs::write(dist.join("index.html"), "<html><head></head><body>hi</body></html>").unwrap();
        std::fs::write(dist.join("app.js"), "console.log(1)").unwrap();
        root
    }

    #[test]
    fn rejects_traversal_and_bad_pid() {
        let root = fixture_root();
        assert!(resolve_plugin_path(&root, "demo", "../secret").is_none());
        assert!(resolve_plugin_path(&root, "demo", "a/../../b").is_none());
        assert!(resolve_plugin_path(&root, "../etc", "panel/dist/index.html").is_none());
        assert!(resolve_plugin_path(&root, "", "panel/dist/index.html").is_none());
        assert!(resolve_plugin_path(&root, "Demo", "x").is_none()); // 大写不收(WKWebView host 全小写,与插件 id 一致)
        assert!(resolve_plugin_path(&root, "demo", "panel/dist/index.html").is_some());
        assert!(resolve_plugin_path(&root, "demo", "missing.js").is_none());
    }

    #[test]
    fn mime_mapping() {
        assert_eq!(mime_for(Path::new("a/b.html")), "text/html; charset=utf-8");
        assert_eq!(mime_for(Path::new("a/b.JS")), "text/javascript; charset=utf-8");
        assert_eq!(mime_for(Path::new("a/b.woff2")), "font/woff2");
        assert_eq!(mime_for(Path::new("a/b.bin")), "application/octet-stream");
    }

    #[test]
    fn injects_after_head_or_prepends() {
        let out = inject_sdk("<html><head><title>t</title></head><body/></html>");
        let head = out.find("<head>").unwrap();
        let csp = out.find("Content-Security-Policy").unwrap();
        let map = out.find("importmap").unwrap();
        let bridge = out.find("/__yibao__/bridge.js").unwrap();
        assert!(head < csp && csp < map && map < bridge); // 桥在插件脚本前生效
        let bare = inject_sdk("<div/>");
        assert!(bare.find("Content-Security-Policy").unwrap() < bare.find("<div/>").unwrap());
    }

    #[test]
    fn pct_decode_basics() {
        assert_eq!(pct_decode("a%20b/c.js").as_deref(), Some("a b/c.js"));
        assert!(pct_decode("a%2f..%2f").is_some()); // 解码后含 .. 由 resolve_plugin_path 拦
        assert!(pct_decode("%zz").is_none());
        assert!(pct_decode("%4").is_none());
    }

    #[test]
    fn serves_sdk_and_injects_html() {
        let root = fixture_root();
        let r = handle(&req("yibao-plugin://demo/__yibao__/bridge.js"), &root);
        assert_eq!(r.status(), 200);
        assert!(String::from_utf8_lossy(r.body()).contains("window.yibao"));

        let r = handle(&req("yibao-plugin://demo/panel/dist/index.html"), &root);
        assert_eq!(r.status(), 200);
        let body = String::from_utf8_lossy(r.body()).into_owned();
        assert!(body.contains("Content-Security-Policy"));
        assert!(body.contains("/__yibao__/bridge.js"));
        assert_eq!(r.headers().get("Access-Control-Allow-Origin").unwrap(), "*");
        assert_eq!(r.headers().get("Cache-Control").unwrap(), "no-store");

        let r = handle(&req("yibao-plugin://demo/panel/dist/app.js"), &root);
        assert_eq!(r.status(), 200);
        assert!(!String::from_utf8_lossy(r.body()).contains("Content-Security-Policy")); // 只注入 HTML

        assert_eq!(handle(&req("yibao-plugin://demo/../etc/passwd"), &root).status(), 404);
        assert_eq!(handle(&req("yibao-plugin://demo/missing.js"), &root).status(), 404);
        assert_eq!(handle(&req("yibao-plugin://evil/__yibao__/nope.js"), &root).status(), 404);
    }
}
```

- [ ] **Step 3: 跑 Rust 测试**

Run: `cd app/src-tauri && cargo test plugin_proto`
Expected: 5 passed(若 closure/类型与 tauri 2 小版本有出入,按编译器提示微调;纯函数与协议逻辑不许动语义)

- [ ] **Step 4: lib.rs 注册协议 + plugins_dir prod 修复**

`lib.rs` 三处:

(1) 文件顶部 `mod` 区加:`mod plugin_proto;`

(2) Builder 链(:2085 起,放在 `.plugin(tauri_plugin_dialog::init())` 之后):

```rust
        // module 面板静态资源协议(R4 插件运行时):yibao-plugin://<pid>/<path>
        .register_uri_scheme_protocol("yibao-plugin", |_ctx, request| {
            plugin_proto::handle(&request, &plugins_dir())
        })
```

(3) `.setup(|app| {` 开头(:2125)加 prod 修复:

```rust
            // plugins_dir() 的 CARGO_MANIFEST_DIR 相对路径在 prod 是构建机残留:
            // bundle.resources 打进资源目录的 plugins 才是真相(dev 下资源目录无 plugins,自动落空走原逻辑)
            if std::env::var("YIBAO_PLUGINS_DIR").is_err() {
                if let Ok(rd) = app.path().resource_dir() {
                    let bundled = rd.join("plugins");
                    if bundled.is_dir() {
                        std::env::set_var("YIBAO_PLUGINS_DIR", bundled);
                    }
                }
            }
```

- [ ] **Step 5: 编译 + 全量测试**

Run: `cd app/src-tauri && cargo test`
Expected: 全绿(plugin_proto 5 个 + 既有 1 个)

- [ ] **Step 6: Commit**

```bash
git add app/src-tauri/src/plugin_proto.rs app/src-tauri/src/lib.rs app/src-tauri/resources/sdk/vue.esm-browser.js app/src/shared/bridge.js
git commit -m "feat: yibao-plugin:// 协议——module 面板静态资源运行时服务 + plugins_dir prod 修复"
```

(注:bridge.js 在 Task 2 已提交则此处自动少一个文件,git add 不存在的路径会报错,按实际调整。)

---

### Task 4: WebviewPanel url 分叉 + PanelApp/HomePlugins 接线

**Files:**
- Create: `app/src/lib/webview-source.ts`
- Test: `app/src/lib/webview-source.test.ts`
- Modify: `app/src/components/WebviewPanel.vue`(props + 模板分叉)
- Modify: `app/src/components/PanelApp.vue`(:38 与 :366 类型、:379 computed、:487-503 模板)
- Modify: `app/src/components/HomePlugins.vue`(:215 与 :566 类型、:587 computed、:698-712 模板)

**Interfaces:**
- Consumes: Task 1 的 payload 形状 `webview: {url, v}`;Task 3 的协议
- Produces: `resolveWebviewSource(w: {html?: string; url?: string; v?: number} | null | undefined): {kind:"url", url, key} | {kind:"srcdoc", html} | null`;WebviewPanel 新 props `url?: string; v?: number`

- [ ] **Step 1: 写失败测试**

新建 `app/src/lib/webview-source.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { resolveWebviewSource } from "./webview-source";

describe("resolveWebviewSource", () => {
  it("url 优先:module 面板走 iframe src,key 含版本号", () => {
    expect(resolveWebviewSource({ url: "yibao-plugin://coding/panel/dist/index.html", v: 1720000000 }))
      .toEqual({ kind: "url", url: "yibao-plugin://coding/panel/dist/index.html", key: "yibao-plugin://coding/panel/dist/index.html@1720000000" });
  });
  it("url 与 html 同现时 url 胜(新通道优先)", () => {
    const s = resolveWebviewSource({ url: "yibao-plugin://a/panel/dist/index.html", html: "<html/>" });
    expect(s?.kind).toBe("url");
  });
  it("v 缺省 key 落 @0", () => {
    const s = resolveWebviewSource({ url: "yibao-plugin://a/x.html" });
    expect(s?.kind === "url" && s.key.endsWith("@0")).toBe(true);
  });
  it("仅 html:旧 srcdoc 面板原样", () => {
    expect(resolveWebviewSource({ html: "<html>old</html>" }))
      .toEqual({ kind: "srcdoc", html: "<html>old</html>" });
  });
  it("空值与空串都归 null", () => {
    expect(resolveWebviewSource(null)).toBeNull();
    expect(resolveWebviewSource(undefined)).toBeNull();
    expect(resolveWebviewSource({})).toBeNull();
    expect(resolveWebviewSource({ url: "", html: "" })).toBeNull();
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd app && pnpm test -- webview-source`
Expected: FAIL(`Cannot find module './webview-source'`)

- [ ] **Step 3: 实现 webview-source.ts**

新建 `app/src/lib/webview-source.ts`:

```ts
// webview 面板载荷分流:module 面板(url,R4 插件运行时)优先,旧 html 面板走 srcdoc。
// key 含内容版本 v(入口文件 mtime):v 变 → iframe :key 变 → 重载新代码(sidecar 不重启)。
export interface WebviewPayload {
  html?: string;
  url?: string;
  v?: number;
}

export type WebviewSource =
  | { kind: "url"; url: string; key: string }
  | { kind: "srcdoc"; html: string };

export function resolveWebviewSource(w: WebviewPayload | null | undefined): WebviewSource | null {
  if (!w) return null;
  if (typeof w.url === "string" && w.url) {
    return { kind: "url", url: w.url, key: `${w.url}@${w.v ?? 0}` };
  }
  if (typeof w.html === "string" && w.html) {
    return { kind: "srcdoc", html: w.html };
  }
  return null;
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd app && pnpm test -- webview-source`
Expected: 5 passed

- [ ] **Step 5: WebviewPanel 分叉**

`WebviewPanel.vue`:

(1) props(:15-20)改为:

```ts
const props = defineProps<{
  panel: string; // 面板引用(plugin_id:name),推导可调方法的命名空间前缀
  html?: string; // 旧 srcdoc 面板 HTML(桥 JS 由本组件注入)
  url?: string; // module 面板 URL(yibao-plugin://,桥由协议层注入,CSP 见 plugin_proto.rs)
  v?: number; // module 面板内容版本(入口 mtime):变 → :key 变 → iframe 重载(热加载)
  data: Record<string, unknown>; // panel 事件注入的数据(init 推给 iframe)
  takeover?: boolean; // 接管标志:随 init 载荷下发给 iframe(默认 false)
}>();
```

(2) `srcdoc` computed(:80-86)里 `props.html` 改 `props.html ?? ""`;其上方加:

```ts
// module 面板源:有 url 即走真实 src(桥/CSP/importmap 由 yibao-plugin:// 协议层注入,本组件不再注)
const urlSource = computed(() => {
  const s = resolveWebviewSource({ url: props.url, v: props.v });
  return s && s.kind === "url" ? s : null;
});
```

并加 import:`import { resolveWebviewSource } from "../lib/webview-source";`

(3) 模板(:201-208)改为双 iframe:

```html
<template>
  <iframe
    v-if="urlSource"
    :key="urlSource.key"
    ref="iframeEl"
    class="webview"
    sandbox="allow-scripts"
    :src="urlSource.url"
    @load="postInit"
  />
  <iframe
    v-else
    ref="iframeEl"
    class="webview"
    sandbox="allow-scripts"
    :srcdoc="srcdoc"
    @load="postInit"
  />
</template>
```

(4) 文件头注释(:2-10)补一行:`// module 面板(R4):props.url 非空时走 iframe src(yibao-plugin://),桥由协议层注入;srcdoc 路径行为不变。`

- [ ] **Step 6: PanelApp / HomePlugins 接线**

`PanelApp.vue`:

(1) :38 与 :366 两处类型 `webview: { html?: string } | null;` 改为:

```ts
webview: { html?: string; url?: string; v?: number } | null;
```

(2) :379 `webviewHtml` computed 下加:

```ts
// module 面板(R4):url/v 直传 WebviewPanel;空串 → 与 html 一起判空走 schema/占位
const webviewUrl = computed(() => current.value?.webview?.url ?? "");
const webviewV = computed(() => current.value?.webview?.v ?? 0);
```

(3) 模板里 `<WebviewPanel` 用法(:487-503 区域):`v-if="webviewHtml"` 改 `v-if="webviewHtml || webviewUrl"`,并补传 `:url="webviewUrl"` `:v="webviewV"`(`:html="webviewHtml"` 等既有属性原样保留)。

`HomePlugins.vue`:同样三处(:215 与 :566 类型、:587 下加 computed、:698-712 模板同法改)。

- [ ] **Step 7: 验证**

Run: `cd app && pnpm build && pnpm test`
Expected: vue-tsc 无错,vitest 全绿

- [ ] **Step 8: Commit**

```bash
git add app/src/lib/webview-source.ts app/src/lib/webview-source.test.ts app/src/components/WebviewPanel.vue app/src/components/PanelApp.vue app/src/components/HomePlugins.vue
git commit -m "feat: WebviewPanel url 分叉——module 面板走 yibao-plugin:// iframe src"
```

---

### Task 5: coding:studio hello 面板 + 共享构建链

**Files:**
- Create: `scripts/panel-build/package.json`、`scripts/panel-build/build.mjs`
- Create: `plugins/coding/panel/index.html`、`plugins/coding/panel/src/main.js`、`plugins/coding/panel/src/App.vue`
- Modify: `plugins/coding/manifest.toml`(尾部加 studio panel 声明)
- Modify: `plugins/coding/api.toml`(尾部加 coding.studio 方法)
- Modify: `plugins/coding/skills/coding.py`(StudioSkill + make_tools 注册,`Skill` 基类见 :34 `from yibao_brain.skills import Skill`)
- Modify: `.gitignore`(根,加 `plugins/*/panel/dist/`)
- Test: `sidecar/tests/test_coding_studio.py`

**Interfaces:**
- Consumes: Task 3 的 importmap 约定(`import ... from "vue"`);Task 4 的前端分叉;Task 1 的 manifest `type="module"`
- Produces: 构建命令 `node scripts/panel-build/build.mjs <pid>`(产物 `plugins/<pid>/panel/dist/`);`coding.studio` L0 skill(`ActionResult(success=True, data={}, panel="coding:studio")`);插件页子入口「多工位(新)」(manifest `open = "studio"`)

- [ ] **Step 1: 写后端失败测试**

新建 `sidecar/tests/test_coding_studio.py`:

```python
"""coding.studio:打开多工位面板(module 面板入口 skill)。"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# 插件 skills 不在 src 下,单独加路径(仿 test_coding_plugin.py)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugins", "coding", "skills"))
from coding import StudioSkill  # noqa: E402


def test_studio_skill_points_at_module_panel():
    res = StudioSkill().run({}, None)
    assert res.success
    assert res.panel == "coding:studio"
```

Run: `cd sidecar && .venv/bin/pytest tests/test_coding_studio.py -v`
Expected: FAIL(`ImportError: cannot import name 'StudioSkill'`)

- [ ] **Step 2: StudioSkill + manifest + api.toml**

`plugins/coding/skills/coding.py` 加(放在 WallDataSkill 之后):

```python
class StudioSkill(Skill):
    """打开 coding:studio 多工位面板(R4 module 面板,新 coding UI 骨架)。

    L0 只读:只发 panel 事件。数据消费全部走面板内 invoke(coding.list 等既有方法),
    本 skill 不带数据(data={}),与 wall_data 的数据源职责分开。
    """
    id = "coding.studio"
    label = "多工位"
    description = "打开 coding 多工位面板(module 面板运行时;多会话同屏工位,阶段一为骨架)。"
    default_risk = RiskLevel.L0_READONLY

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        return ActionResult(success=True, data={}, panel="coding:studio")
```

`make_tools`(:1500-1503)返回列表尾部加 `StudioSkill()`。

`plugins/coding/manifest.toml` 尾部加:

```toml
# R4 多工位(module 面板,新插件运行时):panel/dist 由 scripts/panel-build 构建;
# open 让插件页出现子入口「多工位(新)」,直调 coding.studio 打开
[[panel]]
type = "module"
name = "studio"
label = "多工位(新)"
src = "panel/dist/index.html"
open = "studio"
```

`plugins/coding/api.toml` 尾部加:

```toml
# 多工位(R4):打开 coding:studio module 面板(插件页子入口 open="studio" 直调)
[[method]]
name = "coding.studio"
handler = "coding.studio"
direct = true
panel = "coding:studio"
```

- [ ] **Step 3: 后端测试通过 + 全量回归**

Run: `cd sidecar && .venv/bin/pytest tests/test_coding_studio.py tests/test_module_panels.py -v && .venv/bin/pytest tests/ -x -q`
Expected: 新增 2 个文件全绿;全量无回归(若既有测试因 manifest/api.toml 新增条目失败,检查是否断言了面板/方法总数,按其断言意图更新)

- [ ] **Step 4: 共享构建链**

新建 `scripts/panel-build/package.json`:

```json
{
  "name": "yibao-panel-build",
  "private": true,
  "type": "module",
  "scripts": {
    "build": "node build.mjs"
  },
  "dependencies": {
    "@vitejs/plugin-vue": "^5.2.0",
    "vite": "^6.0.0",
    "vue": "^3.5.0"
  }
}
```

新建 `scripts/panel-build/build.mjs`:

```js
// 插件面板共享构建:node build.mjs <plugin_id>
// 把 plugins/<pid>/panel(index.html 入口的多文件工程)构建到 plugins/<pid>/panel/dist。
// vue 外置:产物里保留 `from "vue"` 裸导入,运行时由宿主 importmap 指到共享 runtime
// (yibao-plugin://<pid>/__yibao__/vue.esm-browser.js,见 plugin_proto.rs)——插件 bundle 不打 Vue。
import { build } from "vite";
import vue from "@vitejs/plugin-vue";
import { fileURLToPath } from "node:url";
import path from "node:path";

const pid = process.argv[2];
if (!pid || !/^[a-z0-9_-]+$/.test(pid)) {
  console.error("用法: node build.mjs <plugin_id>");
  process.exit(1);
}
const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "../../plugins", pid, "panel");

await build({
  root,
  base: "./", // 相对资源路径:yibao-plugin://<pid>/panel/dist/index.html 下直取
  plugins: [vue()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: { external: ["vue"] },
  },
});
console.log(`[panel-build] ${pid} → plugins/${pid}/panel/dist`);
```

装依赖:`cd scripts/panel-build && pnpm install`

- [ ] **Step 5: studio hello 面板工程**

新建 `plugins/coding/panel/index.html`:

```html
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>多工位</title>
  </head>
  <body>
    <div id="app"></div>
    <script type="module" src="/src/main.js"></script>
  </body>
</html>
```

新建 `plugins/coding/panel/src/main.js`:

```js
import { createApp } from "vue";
import App from "./App.vue";
import "./style.css";

createApp(App).mount("#app");
```

新建 `plugins/coding/panel/src/style.css`:

```css
body { margin: 0; font-family: -apple-system, "PingFang SC", sans-serif; background: #f5f5f7; color: #1d1d1f; }
.hello { padding: 24px; max-width: 640px; margin: 0 auto; }
.hello h1 { font-size: 18px; }
.hello button { padding: 6px 14px; border-radius: 8px; border: 1px solid #d2d2d7; background: #fff; cursor: pointer; }
.hello pre { background: #fff; border: 1px solid #d2d2d7; border-radius: 8px; padding: 12px; font-size: 12px; overflow: auto; }
.err { color: #c41e3a; }
```

新建 `plugins/coding/panel/src/App.vue`(骨架:验证桥注入、init 数据、invoke 往返三件事):

```vue
<script setup>
// coding:studio 骨架(R4 阶段一):验证 module 面板全链路——
// 桥注入(window.yibao / YIBAO_BRIDGE_VERSION)、init 数据、invoke 往返(coding.list)。
// 阶段二起此处替换为真正的多工位 UI。
import { ref } from "vue";

const bridgeVer = window.YIBAO_BRIDGE_VERSION ?? "(未注入)";
const init = ref(null);
const result = ref("");
const error = ref("");

window.yibao.onInit((d) => { init.value = d; });

async function ping() {
  error.value = "";
  result.value = "";
  try {
    const r = await window.yibao.invoke("coding.list", {});
    result.value = JSON.stringify(r, null, 2).slice(0, 800);
  } catch (e) {
    error.value = String(e);
  }
}
</script>

<template>
  <main class="hello">
    <h1>coding:studio(骨架)</h1>
    <p>桥版本:{{ bridgeVer }} ｜ init 数据:{{ init ? "已收到" : "等待中…" }}</p>
    <button @click="ping">调 coding.list 验证往返</button>
    <pre v-if="result">{{ result }}</pre>
    <p v-if="error" class="err">{{ error }}</p>
  </main>
</template>
```

根 `.gitignore` 加一行:`plugins/*/panel/dist/`(构建产物不入库;打包管线在阶段五把 panel-build 接进 app 构建)

构建:`node scripts/panel-build/build.mjs coding`
Expected 输出:`[panel-build] coding → plugins/coding/panel/dist`,且 `plugins/coding/panel/dist/` 下有 `index.html` 与 `assets/*.js`、`assets/*.css`;`dist/assets/*.js` 里 `from"vue"` 裸导入保留(grep 验证:`grep -o 'from *"vue"' plugins/coding/panel/dist/assets/*.js | head -1` 有输出)

- [ ] **Step 6: Commit**

```bash
git add scripts/panel-build plugins/coding/panel/index.html plugins/coding/panel/src plugins/coding/manifest.toml plugins/coding/api.toml plugins/coding/skills/coding.py sidecar/tests/test_coding_studio.py .gitignore
git commit -m "feat: coding:studio 骨架面板 + 插件面板共享构建链(R4 阶段一)"
```

---

### Task 6: 端到端手工验收(全链路 + 热加载 + 旧通道回归)

**Files:** 无新增(验收脚本 + 收尾)

**Interfaces:**
- Consumes: Task 1-5 全部

- [ ] **Step 1: 起 dev**

```bash
cd app && pnpm tauri dev
```

- [ ] **Step 2: 全链路验收(spec 验收 A)**

- 大窗 → 插件页 → 「编码」卡片 → 子入口「多工位(新)」→ 面板窗打开 coding:studio
- 页面渲染「coding:studio(骨架)」,桥版本显示 `1`,init 数据显示「已收到」→ 桥经协议层注入成功
- 点「调 coding.list 验证往返」→ 显示 sessions JSON → invoke 往返经 api.toml 白名单成功

- [ ] **Step 3: 热加载验收(spec 验收 F)**

- 改 `plugins/coding/panel/src/App.vue` 的 h1 文案(如加「v2」),重跑 `node scripts/panel-build/build.mjs coding`
- **不重启 sidecar**,关掉面板窗再经子入口重开 → 显示新文案(payload v=mtime 变 → iframe :key 变 → 重载)

- [ ] **Step 4: 安全验收**

- 面板窗 devtools 切到 studio iframe 上下文,执行 `fetch("https://example.com").catch(e => String(e))` → 被 CSP `connect-src 'none'` 拦截
- 执行 `fetch("yibao-plugin://notes/panel/list.schema.json")` → 同样被 `connect-src 'none'` 拦截(跨插件也禁)

- [ ] **Step 5: 旧通道回归(spec 验收 A 后半)**

- coding:chat 旧面板:发起一个短会话,对话流正常(srcdoc 路径)
- 会话墙(coding:wall)卡片正常;zimeiti 编辑器、toolbox 面板各打开一次正常

- [ ] **Step 6: 全量测试收尾 + commit(如有修补)**

Run: `cd sidecar && .venv/bin/pytest tests/ -q` ; `cd app && pnpm test` ; `cd app/src-tauri && cargo test`
Expected: 三处全绿。验收发现问题就修,修复随本任务 commit:

```bash
git commit -m "test: R4 阶段一端到端验收修补"
```
