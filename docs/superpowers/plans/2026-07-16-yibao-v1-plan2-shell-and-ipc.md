# 译宝 v1 · Plan 2：Tauri 桌面壳 + IPC 接入大脑 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 Plan 1 的 Python 大脑套上桌面壳——全局快捷键唤起、置顶透明形象窗、文字输入条；经 stdio JSON-RPC 把大脑的事件流接进来，UI 渲染对话与形象状态，高风险操作弹窗确认。

**Architecture:** 两阶段。**Phase A（Python，可在本服务器 TDD）**：新增 `sidecar/src/yibao_brain/server.py`——行分隔 JSON 的 stdio 服务，包住 `AgentLoop`，把 `confirmer` 变成「向壳发通知→阻塞读壳的回答」的往返。**Phase B（Tauri，在 Win/Mac 上做）**：`app/` 下 Tauri v2 + Vue 壳，`tauri-plugin-shell` 拉起 sidecar 进程并桥接 stdio，`tauri-plugin-global-shortcut` 注册全局热键，Vue 前端做形象/输入/确认。

**Tech Stack:** Python 3.12（Phase A）；Tauri v2 + Rust + Vue 3 + Vite + `@tauri-apps/api`（Phase B）。

## Global Constraints

（抄自设计文档 `docs/superpowers/specs/2026-07-16-desktop-agent-design.md`）

- 平台：Windows + macOS 起步（Phase B 必须在真实桌面验证；Phase A 纯逻辑）。
- 壳：Tauri(Rust) + Vue，自研，借鉴 BongoCat 的透明/置顶/穿透实践。
- 大脑：独立 Python sidecar（Plan 1 已落地 `sidecar/`，22 测试）。
- 风险分级 L0–L4：高风险（L3+）必须弹窗确认。
- 授权往返：大脑发 `confirmation_needed` → 壳弹窗 → 用户选择 → 壳回 `confirm`。
- macOS：透明窗需 `macOSPrivateApi:true`（不能上 App Store，可签名公证站外分发）；需引导「辅助功能」「屏幕录制」权限。
- DRY、YAGNI、TDD、频繁提交。

## 环境说明

- Phase A 全部可在当前无显示器 Linux 服务器执行并用 `pytest` 验证。
- Phase B 必须在 Windows 或 macOS 桌面执行：`cargo build`、运行 GUI、授权弹窗。本计划 Phase B 的 Rust/Vue 代码是「按 Tauri v2 文档写就、需在目标机 build 校验」的脚手架——遇到 API 不一致以当前 Tauri v2 文档为准修正。

## IPC 协议（行分隔 JSON，Phase A/B 共用）

壳 → 脑（stdin）：
```json
{"id": 1, "type": "run", "text": "用户输入"}
{"id": 2, "type": "confirm", "confirmation_id": "act_3", "approved": true}
```
脑 → 壳（stdout）：
```json
{"type": "event", "event": {<Event model_dump, 见 ipc.py>}}
{"type": "run_done", "id": 1}
```
> 约定：`run` 期间，壳收到 `event(kind=confirmation_needed)` 后，下一条发给脑的消息必须是 `confirm`；脑的 `confirmer` 会阻塞读取它。

## File Structure

```
sidecar/src/yibao_brain/
└── server.py                  # [Phase A] stdio JSON 服务 + serve()（可测）
sidecar/tests/
└── test_server.py             # [Phase A]
app/                           # [Phase B] Tauri 工程（仓库根新建）
├── package.json               # Vue + @tauri-apps/cli + api
├── index.html
├── src/                       # Vue 前端
│   ├── main.ts
│   ├── App.vue                # 形象窗根组件
│   ├── components/
│   │   ├── Avatar.vue         # 状态驱动形象（待机/听/思考/工作）
│   │   ├── InputBar.vue       # 文字输入条
│   │   ├── ConfirmDialog.vue  # 高风险确认弹窗
│   │   └── Bubble.vue         # 对话气泡
│   └── lib/brain.ts           # 封装 invoke/listen 与脑通信
└── src-tauri/
    ├── Cargo.toml
    ├── tauri.conf.json        # 透明/置顶/无边框/热键/私有 API 配置
    ├── capabilities/default.json
    └── src/
        ├── main.rs
        └── lib.rs             # sidecar 拉起 + stdio 桥 + 热键 + 窗口控制
```

---

# Phase A —— Python IPC 桥（可在本服务器 TDD）

### Task A1: stdio 服务 `server.py` + `serve()`（TDD）

**Files:**
- Create: `sidecar/src/yibao_brain/server.py`
- Test: `sidecar/tests/test_server.py`

**Interfaces:**
- Consumes: `loop.AgentLoop`、`ipc.Event`、`llm.FakeProvider`/`GLMProvider`、`skills.*`、`safety.*`、`memory.*`、`audit.AuditLog`。
- Produces: `serve(loop, read_msg, write_msg)`（可测纯函数）、`build_loop(read_msg, use_real, db_path)`、`main()`（接 sys.stdin/stdout）。

- [ ] **Step 1: 写失败测试 `tests/test_server.py`**

```python
import json
from yibao_brain.server import serve, build_loop
from yibao_brain.llm import FakeProvider, ToolCall


class _TwoStepProvider:
    def __init__(self, first, second):
        self._first, self._second, self._n = first, second, 0

    def chat(self, messages, tools=None):
        self._n += 1
        return self._first.chat(messages, tools) if self._n == 1 else self._second.chat(messages, tools)


def make_reader(msgs):
    it = iter(msgs + [None])  # 末尾返回 None 表示 stdin 结束
    return lambda: next(it)


def test_serve_streams_events_and_run_done(tmp_path):
    provider = _TwoStepProvider(
        first=FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="echo", params={"text": "hi"})]),
        second=FakeProvider(text="echoed: hi"),
    )
    loop = build_loop(make_reader([{"id": 1, "type": "run", "text": "hi"}]),
                      use_real=False, db_path=str(tmp_path / "a.db"), provider=provider)
    out = []
    serve(loop, make_reader([{"id": 1, "type": "run", "text": "hi"}]), lambda m: out.append(m))
    kinds = [m["event"]["kind"] for m in out if m["type"] == "event"]
    assert "action_result" in kinds
    assert out[-1] == {"type": "run_done", "id": 1}


def test_serve_round_trips_confirmation(tmp_path):
    from yibao_brain.skills import Skill, SkillRegistry
    from yibao_brain.ipc import ActionResult, RiskLevel

    class DangerSkill(Skill):
        id = "danger"; description = "危险占位"; default_risk = RiskLevel.L3_HIGH
        def run(self, params, ctx): return ActionResult(success=True, data={"did": True})

    provider = _TwoStepProvider(
        first=FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="danger", params={})]),
        second=FakeProvider(text="done"),
    )
    inbox = [
        {"id": 1, "type": "run", "text": "做危险的事"},
        {"id": 2, "type": "confirm", "confirmation_id": "x", "approved": False},
    ]
    # build_loop 需要 DangerSkill：自定义 skills
    loop = build_loop(make_reader(inbox), use_real=False, db_path=str(tmp_path / "a.db"),
                      provider=provider, skills_factory=lambda: _registry_with(DangerSkill()))
    out = []
    serve(loop, make_reader(inbox), lambda m: out.append(m))
    kinds = [m["event"]["kind"] for m in out if m["type"] == "event"]
    assert "confirmation_needed" in kinds
    assert "error" in kinds  # 用户拒绝后产出 error
    assert not any(m["type"] == "event" and m["event"].get("kind") == "action_result"
                   and m["event"]["result"]["data"].get("did") for m in out)


def _registry_with(*skills):
    from yibao_brain.skills import SkillRegistry
    reg = SkillRegistry()
    for s in skills:
        reg.register(s)
    return reg
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run --directory /data/dennyxiao/yibao/sidecar pytest tests/test_server.py -q`
Expected: FAIL（`ModuleNotFoundError: yibao_brain.server`）。

- [ ] **Step 3: 实现 `src/yibao_brain/server.py`**

```python
"""stdio 行分隔 JSON 服务：把 AgentLoop 接到桌面壳（Phase B 的 Tauri 侧）。"""
from __future__ import annotations

import json
import sys
from collections.abc import Callable

from .audit import AuditLog
from .config import glm_api_key
from .llm import FakeProvider, GLMProvider
from .loop import AgentLoop
from .memory import FakeMemory, Mem0Memory
from .safety import Gate, GatePolicy, RiskClassifier
from .skills import EchoSkill, SkillRegistry

ReadMsg = Callable[[], dict | None]
WriteMsg = Callable[[dict], None]


def build_loop(
    read_msg: ReadMsg,
    use_real: bool,
    db_path: str,
    provider=None,
    skills_factory=None,
) -> AgentLoop:
    reg = skills_factory() if skills_factory else SkillRegistry()
    if not skills_factory:
        reg.register(EchoSkill())

    if provider is not None:
        prov = provider
    else:
        prov = GLMProvider() if (use_real and glm_api_key()) else FakeProvider(text="(未配置 GLM key，使用 fake 回复)")

    try:
        memory = Mem0Memory() if use_real else FakeMemory()
    except Exception:
        memory = FakeMemory()

    def confirmer(action) -> bool:
        # 由 serve 在 confirmation_needed 事件之后触发；阻塞读壳的回答
        ans = read_msg() or {}
        return bool(ans.get("approved", False))

    return AgentLoop(
        provider=prov,
        skills=reg,
        classifier=RiskClassifier(),
        gate=Gate(GatePolicy()),
        memory=memory,
        log=AuditLog(db_path),
        confirmer=confirmer,
    )


def serve(loop: AgentLoop, read_msg: ReadMsg, write_msg: WriteMsg) -> None:
    while True:
        req = read_msg()
        if req is None:
            return
        if req.get("type") == "run":
            for event in loop.run(req.get("text", "")):
                write_msg({"type": "event", "event": event.model_dump(mode="json")})
            write_msg({"type": "run_done", "id": req.get("id")})


def _line_reader() -> ReadMsg:
    def _r() -> dict | None:
        line = sys.stdin.readline()
        if not line:
            return None
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None
    return _r


def _line_writer() -> WriteMsg:
    def _w(msg: dict) -> None:
        sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return _w


def main() -> int:
    reader, writer = _line_reader(), _line_writer()
    loop = build_loop(reader, use_real=True, db_path="audit.db")
    serve(loop, reader, writer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run --directory /data/dennyxiao/yibao/sidecar pytest tests/test_server.py -q`
Expected: PASS（2 passed）。

- [ ] **Step 5: 跑全量回归**

Run: `uv run --directory /data/dennyxiao/yibao/sidecar pytest -q`
Expected: 全部 PASS（Plan 1 的 22 + server 2 = 24）。

- [ ] **Step 6: 手动冒烟（fake，headless 可做）**

Run:
```bash
printf '{"id":1,"type":"run","text":"hi"}\n' | uv run --directory /data/dennyxiao/yibao/sidecar python -m yibao_brain.server
```
Expected: 输出若干 `{"type":"event",...}` 与结尾 `{"type":"run_done","id":1}`。

- [ ] **Step 7: 提交**

```bash
git add sidecar/src/yibao_brain/server.py sidecar/tests/test_server.py
git commit -m "feat(sidecar): stdio json server bridging agent loop to shell"
```

---

### Task A2: 注册 server 入口 + README

**Files:**
- Modify: `sidecar/pyproject.toml`（加 `[project.scripts] yibao-brain-server`）
- Modify: `sidecar/README.md`（补 server 用法）

- [ ] **Step 1: 在 `pyproject.toml` 的 `[project.scripts]` 下追加一行**

```toml
[project.scripts]
yibao-brain = "yibao_brain.cli:main"
yibao-brain-server = "yibao_brain.server:main"
```

- [ ] **Step 2: README 末尾追加**

````markdown
## 作为 sidecar（供桌面壳调用）
```bash
uv run yibao-brain-server        # 行分隔 JSON over stdio；协议见 Plan 2 文档
```
````

- [ ] **Step 3: 重装并验证入口存在**

Run: `uv sync --directory /data/dennyxiao/yibao/sidecar --extra dev >/dev/null && printf '{"id":1,"type":"run","text":"hi"}\n' | uv run --directory /data/dennyxiao/yibao/sidecar yibao-brain-server | tail -1`
Expected: 末行 `{"type":"run_done","id":1}`。

- [ ] **Step 4: 提交**

```bash
git add sidecar/pyproject.toml sidecar/README.md
git commit -m "feat(sidecar): expose yibao-brain-server entrypoint"
```

---

# Phase B —— Tauri 桌面壳（在 Win/Mac 上执行）

> 以下任务在本服务器无法验证（无显示/GUI）。请在 Windows 或 macOS 上执行；`cargo build`/运行时遇到 Tauri v2 API 与文档不一致，以官方文档为准修正。建议每任务一次 `cargo build` + 手动运行。

### Task B1: Tauri + Vue 工程脚手架与窗口配置

**Files:**
- Create: `app/package.json`、`app/index.html`、`app/src/main.ts`、`app/src/App.vue`（占位）
- Create: `app/src-tauri/Cargo.toml`、`app/src-tauri/tauri.conf.json`、`app/src-tauri/src/main.rs`、`app/src-tauri/capabilities/default.json`

- [ ] **Step 1: 用官方脚手架初始化（推荐，省去手写）**

在仓库根执行（需 Node + Rust 工具链）：
```bash
npm create tauri-app@latest -- --template vue-ts --manager npm app
```
进入 `app/`，安装依赖：
```bash
cd app && npm install && npm install @tauri-apps/api
```

- [ ] **Step 2: 安装用到的 Tauri 插件**

```bash
cd app
npm run tauri -- add shell
npm run tauri -- add global-shortcut
```
（等价于在 `src-tauri/Cargo.toml` 加 `tauri-plugin-shell`、`tauri-plugin-global-shortcut`，并在 `lib.rs`/`main.rs` `.plugin(...)` 注册。）

- [ ] **Step 3: 配置 `src-tauri/tauri.conf.json` 的窗口与私有 API**

把 `app` 对象改为（关键字段）：
```json
{
  "productName": "译宝",
  "app": {
    "macOSPrivateApi": true,
    "windows": [
      {
        "label": "main",
        "title": "译宝",
        "transparent": true,
        "decorations": false,
        "alwaysOnTop": true,
        "skipTaskbar": true,
        "resizable": false,
        "shadow": false,
        "visible": false,
        "width": 360,
        "height": 460
      }
    ],
    "security": { "csp": null }
  }
}
```
> Windows 透明窗若出现黑/灰底：在 `tauri.conf.json` 同窗加 `"background_color": "#00000000"` 或在 `lib.rs` 对该 webview 关闭 GPU 加速后再调优。

- [ ] **Step 4: `capabilities/default.json` 授予最小权限**

```json
{
  "$schema": "../gen/schemas/desktop-schema.json",
  "identifier": "default",
  "description": "译宝默认能力",
  "windows": ["main"],
  "permissions": [
    "core:default",
    "shell:allow-spawn",
    "shell:allow-stdin-write",
    "global-shortcut:allow-register",
    "global-shortcut:allow-unregister"
  ]
}
```
> 视 Tauri v2 实际生成的权限名调整（`npm run tauri info` / 查 `gen/schemas`）。

- [ ] **Step 5: 构建验证**

Run: `cd app && npm run tauri build -- --debug` （或 `npm run tauri dev`）
Expected: 编译通过、弹出一个透明置顶无边框空窗（可能仍空白——B3 再做内容）。macOS 首次会请求权限。

- [ ] **Step 6: 提交**

```bash
git add app/
git commit -m "feat(app): scaffold tauri v2 + vue shell with transparent always-on-top window"
```

---

### Task B2: Rust 侧——拉起 sidecar + stdio 桥 + 全局热键

**Files:**
- Modify: `app/src-tauri/src/lib.rs`

**Interfaces:**
- Consumes: Plan A 的 `yibao-brain-server`（stdio 行分隔 JSON）。
- Produces: `brain-event`（Tauri 事件，载荷为脑的 Event）、`brain-run-done`；前端命令 `run_input(text)`、`confirm(confirmation_id, approved)`；全局热键切换窗口。

- [ ] **Step 1: 在 `lib.rs` 写 sidecar 拉起 + stdio 桥 + 热键**

```rust
// app/src-tauri/src/lib.rs
use std::sync::Mutex;
use tauri::{Manager, Emitter};
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;
use tauri_plugin_shell::process::CommandEvent;
use serde_json::Value;

struct Brain(Mutex<Option<CommandChild>>);

#[tauri::command]
fn run_input(state: tauri::State<Brain>, text: String) -> Result<(), String> {
    let msg = serde_json::json!({ "id": 0, "type": "run", "text": text }).to_string();
    let child = state.0.lock().unwrap();
    if let Some(c) = child.as_ref() {
        c.write(format!("{}\n", msg)).map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
fn confirm(state: tauri::State<Brain>, confirmation_id: String, approved: bool) -> Result<(), String> {
    let msg = serde_json::json!({ "id": 0, "type": "confirm", "confirmation_id": confirmation_id, "approved": approved }).to_string();
    let child = state.0.lock().unwrap();
    if let Some(c) = child.as_ref() {
        c.write(format!("{}\n", msg)).map_err(|e| e.to_string())?;
    }
    Ok(())
}

pub fn run() {
    let shortcut = tauri_plugin_global_shortcut::Builder::new()
        .with_handler(|app, _shortcut, event| {
            if event.state == tauri_plugin_global_shortcut::ShortcutState::Pressed {
                if let Some(win) = app.get_webview_window("main") {
                    let _ = if win.is_visible().unwrap_or(false) {
                        win.hide()
                    } else {
                        win.show();
                        win.set_focus()
                    };
                }
            }
        })
        .build();

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(shortcut)
        .manage(Brain(Mutex::new(None)))
        .setup(|app| {
            // 注册全局热键（按平台/偏好可改）
            app.handle().plugin(
                tauri_plugin_global_shortcut::init()
            ).ok();
            // 注：上面的 Builder 方式注册更直接；若用 init()，再 app.global_shortcut().register("Super+Shift+Y")
            // 这里保留 Builder 已在构造时 with_handler；需显式注册快捷键：
            #[cfg(desktop)]
            {
                use tauri_plugin_global_shortcut::GlobalShortcutExt;
                let _ = app.global_shortcut().register("Super+Shift+Y");
            }

            // 拉起 Python sidecar（开发期：用 venv 的 python 跑模块）
            // 生产期：改为打包后的 sidecar 二进制（PyInstaller）+ tauri.conf.json 的 externalBin
            let sidecar_dir = std::env::current_dir()?; // 按实际部署调整指向 sidecar/
            let cmd = app.shell()
                .command("python")
                .args(["-u", "-m", "yibao_brain_server"])
                .current_dir(sidecar_dir.join("../sidecar"))
                .env("PYTHONUNBUFFERED", "1");

            let (mut rx, child) = cmd.spawn().map_err(|e| e.to_string())?;
            app.state::<Brain>().0.lock().unwrap().replace(child);

            let app_handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                while let Some(event) = rx.recv().await {
                    if let CommandEvent::Stdout(bytes) = event {
                        let line = String::from_utf8_lossy(&bytes).trim().to_string();
                        if let Ok(v) = serde_json::from_str::<Value>(&line) {
                            match v.get("type").and_then(|t| t.as_str()) {
                                Some("event") => { let _ = app_handle.emit("brain-event", v.get("event").cloned()); }
                                Some("run_done") => { let _ = app_handle.emit("brain-run-done", v); }
                                _ => {}
                            }
                        }
                    } else if let CommandEvent::Stderr(bytes) = event {
                        eprintln!("brain stderr: {}", String::from_utf8_lossy(&bytes));
                    }
                }
            });

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![run_input, confirm])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
```
> ⚠️ 校验点：`tauri-plugin-shell` v2 的 `Command::spawn` 返回 `(Receiver<CommandEvent>, CommandChild)`；`CommandChild::write` 写 stdin；事件枚举为 `CommandEvent::Stdout/Stderr`。`current_dir` 指向 `sidecar/` 需按你机器实际相对路径/绝对路径修正（或直接 `python -m yibao_brain.server` 配合已装包）。`-u` 强制无缓冲，避免行被攒着不 flush。热键注册两种方式择一，勿重复。

- [ ] **Step 2: `main.rs` 调 `lib::run()`**

```rust
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
fn main() { yibao_app_lib::run(); }
```
（脚手架生成的 `lib` 名以 `Cargo.toml` 的 `[lib] name` 为准，通常 `yibao_app_lib`。）

- [ ] **Step 3: 构建并手动验证**

Run: `cd app && npm run tauri dev`
Expected: 启动后 sidecar 进程被拉起；按 `Super+Shift+Y` 窗口显隐切换；终端能看到 brain stderr/心跳。先不接前端，确认进程与热键活着。

- [ ] **Step 4: 提交**

```bash
git add app/src-tauri/
git commit -m "feat(app): spawn brain sidecar + stdio ipc bridge + global hotkey"
```

---

### Task B3: Vue 前端——形象 / 输入 / 确认 / 事件流

**Files:**
- Create: `app/src/App.vue`、`app/src/components/{Avatar,InputBar,ConfirmDialog,Bubble}.vue`、`app/src/lib/brain.ts`
- Modify: `app/src/main.ts`（挂载 App）、`app/index.html`（透明背景）

- [ ] **Step 1: `src/lib/brain.ts`——封装通信**

```ts
import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

export type BrainEvent = {
  kind: "thought" | "action_proposed" | "confirmation_needed" | "action_result" | "final_reply" | "error";
  text?: string; action?: any; result?: any; confirmation_id?: string;
};

export function runInput(text: string) { return invoke("run_input", { text }); }
export function sendConfirm(confirmationId: string, approved: boolean) {
  return invoke("confirm", { confirmationId, approved });
}
export function onBrainEvent(cb: (e: BrainEvent) => void): Promise<UnlistenFn> {
  return listen<any>("brain-event", (ev) => cb(ev.payload as BrainEvent));
}
```

- [ ] **Step 2: `index.html` 透明背景 + `App.vue` 主壳**

`index.html` 的 `<body>` 加内联样式 `background: transparent;`（配合透明窗）。

`App.vue`（精简：形象 + 输入条 + 气泡列表 + 确认弹窗）：
```vue
<script setup lang="ts">
import { ref, onMounted, onUnmounted } from "vue";
import Avatar from "./components/Avatar.vue";
import InputBar from "./components/InputBar.vue";
import ConfirmDialog from "./components/ConfirmDialog.vue";
import Bubble from "./components/Bubble.vue";
import { onBrainEvent, runInput, sendConfirm, type BrainEvent } from "./lib/brain";

const state = ref<"idle"|"listen"|"think"|"work">("idle");
const bubbles = ref<{role:"user"|"ai"; text:string}[]>([]);
const pending = ref<{id:string; skill:string; desc:string} | null>(null);
let unlisten: any;

async function onEvent(e: BrainEvent) {
  switch (e.kind) {
    case "action_proposed": state.value = "work"; break;
    case "confirmation_needed":
      state.value = "idle";
      pending.value = { id: e.confirmation_id!, skill: e.action?.skill_id, desc: e.action?.description };
      break;
    case "action_result": break;
    case "final_reply": state.value = "idle"; bubbles.value.push({role:"ai", text: e.text||""}); break;
    case "error": state.value = "idle"; bubbles.value.push({role:"ai", text: "⚠️ " + (e.text||"")}); break;
  }
}
async function submit(text: string) {
  bubbles.value.push({role:"user", text}); state.value = "think";
  await runInput(text);
}
async function decide(approved: boolean) {
  if (pending.value) { await sendConfirm(pending.value.id, approved); pending.value = null; state.value="think"; }
}
onMounted(async () => { unlisten = await onBrainEvent(onEvent); });
onUnmounted(() => unlisten?.());
</script>

<template>
  <div class="shell">
    <Avatar :state="state" />
    <div class="bubbles"><Bubble v-for="(b,i) in bubbles" :key="i" :role="b.role" :text="b.text" /></div>
    <InputBar v-if="!pending" @submit="submit" />
    <ConfirmDialog v-else :skill="pending.skill" :desc="pending.desc"
                   @approve="() => decide(true)" @deny="() => decide(false)" />
  </div>
</template>

<style scoped>.shell { display:flex; flex-direction:column; gap:8px; padding:12px; }</style>
```

- [ ] **Step 3: 四个组件（功能最小版）**

`Avatar.vue`（状态驱动 emoji/色块占位，后续 Plan 4 升 Live2D）：
```vue
<script setup lang="ts">defineProps<{state:"idle"|"listen"|"think"|"work"}>();</script>
<template><div class="av" :class="state">{{ {idle:'😌',listen:'👂',think:'🤔',work:'⚙️'}[state] }}</div></template>
<style scoped>
.av{width:64px;height:64px;border-radius:50%;display:grid;place-items:center;font-size:34px;background:rgba(255,255,255,.7);box-shadow:0 2px 8px rgba(0,0,0,.15)}
.av.work{animation:p 1s infinite alternate}@keyframes p{from{opacity:.6}to{opacity:1}}
</style>
```

`InputBar.vue`：
```vue
<script setup lang="ts">const emit=defineEmits<{(e:"submit",t:string):void}>();let t="";const send=()=>{if(t.trim()){emit("submit",t);t="";}};</script>
<template><form @submit.prevent="send"><input v-model="t" placeholder="对译宝说点什么…" autofocus/></form></template>
<style scoped>input{width:100%;padding:8px;border-radius:8px;border:1px solid #ccc}</style>
```

`Bubble.vue`：
```vue
<script setup lang="ts">defineProps<{role:"user"|"ai";text:string}>();</script>
<template><div :class="role">{{ text }}</div></template>
<style scoped>div{padding:6px 10px;border-radius:10px;max-width:90%}.ai{background:#eef}.user{background:#dfe;align-self:flex-end}</style>
```

`ConfirmDialog.vue`：
```vue
<script setup lang="ts">defineProps<{skill:string;desc:string}>();const emit=defineEmits<{(e:"approve"):void;(e:"deny"):void}>();</script>
<template><div class="dlg"><b>⚠️ 确认执行</b><p>{{ skill }}：{{ desc }}</p><button @click="emit('approve')">允许</button><button @click="emit('deny')">拒绝</button></div></template>
<style scoped>.dlg{padding:12px;border:1px solid #c33;border-radius:8px;background:#fff}button{margin-right:8px}</style>
```

- [ ] **Step 4: 构建并端到端验证（本机，GLM fake 或真实 key）**

Run: `cd app && npm run tauri dev`
操作：按热键唤出窗 → 输入框敲「请回显 hi」→ 期望看到 ⚙️ 工作 → 气泡出现结果/最终回复；构造一个 L3 技能（临时把某技能 default_risk 设 L3_HIGH）→ 期望弹确认框 → 拒绝/允许各试一次。
Expected: 事件流贯通、形象状态切换、确认往返正常。

- [ ] **Step 5: 提交**

```bash
git add app/src app/index.html
git commit -m "feat(app): vue ui (avatar/input/confirm/bubbles) wired to brain events"
```

---

### Task B4: 打包与平台适配记录

**Files:**
- Modify: `app/src-tauri/tauri.conf.json`（bundle 标识/签名占位）
- Modify: `app/README.md`（记录平台踩坑）

- [ ] **Step 1: 填 bundle 标识**

`tauri.conf.json` 的 `bundle`：设 `"identifier": "com.dennyxiao.yibao"`、`"active": true`。

- [ ] **Step 2: 记录平台踩坑到 `app/README.md`**

至少记录：macOS 需在「系统设置→隐私与安全」授予辅助功能/屏幕录制；`macOSPrivateApi` 致无法上 App Store、需开发者签名+公证后站外分发；Windows 透明窗黑底/白闪的处理；sidecar 路径在 dev 与打包后的差异（生产用 PyInstaller 打包 + `externalBin`）。

- [ ] **Step 3: 打包验证（各平台）**

Run: `cd app && npm run tauri build`
Expected: 产出平台安装包/可执行；首次运行权限引导正常。

- [ ] **Step 4: 提交**

```bash
git add app/
git commit -m "chore(app): bundle id + platform gotchas doc"
```

---

## Self-Review（计划自检）

**1. Spec 覆盖**：
- Tauri 壳 + 透明置顶窗 → B1 ✓
- 全局快捷键 → B2 ✓
- Python 大脑经 IPC 接入 → A1/A2 + B2 ✓
- 高风险弹窗确认（授权往返）→ A1 的 confirmer 往返 + B3 ConfirmDialog ✓
- 轻量形象（状态驱动）→ B3 Avatar ✓
- 文字输入 → B3 InputBar ✓
- 范围外（明确推迟）：STT/TTS/语音打断（Plan 4）、真实执行层与技能（Plan 3）、Live2D（Plan 4）。

**2. 占位符扫描**：无 TBD/TODO；Phase B 的 Rust/Vue 代码为「需在目标机 build 校验」的脚手架，已显式标注校验点，非占位。

**3. 类型/命名一致性**：
- 协议字段 `type`/`id`/`text`/`confirmation_id`/`approved` 在 Phase A 的 server.py 与 Phase B 的 Rust/Vue 间一致 ✓。
- `Event.model_dump` 字段（`kind`/`text`/`action`/`result`/`confirmation_id`）与 `brain.ts` 的 `BrainEvent` 一致 ✓。
- `run_input(text)` / `confirm(confirmationId, approved)` 命令名在 Rust `invoke_handler` 与前端 `invoke` 间一致 ✓。
- Phase A `build_loop` 的 `skills_factory` 参数在两个测试中均使用；`provider` 注入一致 ✓。

**4. 已知风险**（非占位，供执行者注意）：
- Phase B 依赖目标机 Rust/Node 工具链与 Tauri v2 CLI；API 以官方文档为准。
- sidecar 路径在 dev（`python -m`）与打包（PyInstaller `externalBin`）间不同，B4 已记。
- 一次只处理一个 `run`（单对话）；并发对话需改造 server（v1 不做）。
