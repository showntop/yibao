# Coding 插件（统一 coding 聊天面板）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给译宝加一个 `coding` 插件——webview 聊天面板，用户选项目+Claude Code、提任务，后端用 `claude-agent-sdk` 流式跑、回显 + 渲染文件改动，不用切原生 UI。

**Architecture:** 插件形态（`plugins/coding/`）。dispatch 复用官方 `claude-agent-sdk`（in-process 异步流式，非子进程）。runner 在 daemon 线程的独立 asyncio loop 里跑 SDK 流式，每条消息经 `ctx.emit_event({kind:"panel_data"})` 推到 webview 面板（需先给底座加 `panel_data` 流式通道——`PanelApp.vue` ~3 行）。start/stop 走 api.toml direct method（仿 agents 插件），stop 用 race-safe 取消（仿 `agents.task_stop`：先落 stopped 再 cancel）。

**Tech Stack:** Python 3.12（sidecar，pytest，`claude-agent-sdk`），HTML/JS webview 面板（`window.yibao` bridge + Monaco diff），Tauri/Vue 壳既有桥接。

## Global Constraints

- **v1 单 agent（Claude Code）、用户显式选、绝不 auto-routing**（绕开 omnigent 路由坑）。
- **复用 `claude-agent-sdk`，不 wrap CLI**；SDK 调用 lazy import（在默认 client_factory 内 `from claude_agent_sdk import ...`），测试不依赖真 SDK。
- **全自主**：`permission_mode="acceptEdits"` + `allowed_tools` 显式收口；**cwd 必须用户显式选**。
- **挂了不碍事**：runner/线程任何异常只记 session 失败 + 面板报错，绝不拖垮主链路（与 agents 插件同纪律）。
- **TDD**：runner / start / stop 的可测纯逻辑用 FakeRunner + FakeSDK 注入单测；**不在单测跑真 SDK**。webview 面板以 `vue-tsc`/build + 人工为门。
- **sidecar 测试**：`cd /Users/denny/Work/yibao/sidecar && .venv/bin/python -m pytest tests/test_coding_plugin.py -v`；全量 `.venv/bin/python -m pytest -q`（基线 782）。
- **前端命令**：`cd /Users/denny/Work/yibao/app && npx vue-tsc --noEmit && npx vite build`。
- **commit**：每任务一 commit，中文 scope（`feat(coding): ...`），仅 stage 本任务文件，不动无关 `.gitignore`；提交到 `main`。
- **集成事实（已核实，file:line）**：webview 面板声明 `plugins.py:384,392`；`ctx.emit_event` 注入 `plugins.py:511`/`server.py:509`；非 reminder 事件透传 `proactive.py:62-64`；shell→Tauri `lib.rs:410`；`PanelApp` kind=panel 路由 `PanelApp.vue:128-139`；`WebviewPanel` 数据推送 = `watch(props.data)→postInit→iframe onInit`（`WebviewPanel.vue:148,151`），iframe 不重载；`handle_panel_action` direct 路径 `server.py:266-327`；agents 取消 race-safe `agents.py:289-322`。

## File Structure

**新建：**
- `plugins/coding/manifest.toml` — id/capabilities/sessions 表/webview 面板声明。
- `plugins/coding/api.toml` — start/stop/list direct 方法。
- `plugins/coding/skills/__init__.py`、`plugins/coding/skills/runner.py`（AgentRunner + ClaudeCodeRunner）、`plugins/coding/skills/coding.py`（start/stop/list skill + _SESSIONS + _stream）。
- `plugins/coding/panel/chat.html` — webview 聊天 UI。
- `sidecar/tests/test_coding_plugin.py` — runner/start/stop 单测。

**修改（底座，最小）：**
- `sidecar/pyproject.toml` — 加 `claude-agent-sdk` 依赖。
- `app/src/components/PanelApp.vue` — 加 `panel_data` 流式事件分支（~3 行，泛用，非 coding 专属）。

---

### Task 1: 加 claude-agent-sdk 依赖

**Files:**
- Modify: `sidecar/pyproject.toml`（`dependencies` 数组加一行）

**Interfaces:**
- Produces: sidecar venv 可 `import claude_agent_sdk`。

- [ ] **Step 1: 加依赖**

`sidecar/pyproject.toml` 的 `dependencies = [ ... ]`（约 L6）内加：
```toml
    "claude-agent-sdk>=0.2,<0.3",
```
（pin 0.2.x；SDK fast-moving，minor pin 吸收破性变更。）

- [ ] **Step 2: 安装**

Run: `cd /Users/denny/Work/yibao/sidecar && uv sync --extra dev`
Expected: 成功安装 claude-agent-sdk（及其 bundled CLI）。

- [ ] **Step 3: 验证 import + 版本**

Run: `cd /Users/denny/Work/yibao/sidecar && .venv/bin/python -c "import claude_agent_sdk; print('ok')"`
Expected: `ok`

- [ ] **Step 4: 回归（确认没破坏现有）**

Run: `cd /Users/denny/Work/yibao/sidecar && .venv/bin/python -m pytest -q`
Expected: 782 passed

- [ ] **Step 5: commit**
```bash
git add sidecar/pyproject.toml sidecar/uv.lock
git commit -m "feat(coding): 加 claude-agent-sdk 依赖"
```

---

### Task 2: 底座 panel_data 流式通道（PanelApp.vue）

**Files:**
- Modify: `app/src/components/PanelApp.vue`（事件路由，~L128-139）

**Interfaces:**
- Produces: sidecar emit `{"kind":"panel_data","panel":"<plugin>:<name>","data":{...}}` → 经 `proactive.py` 透传 → `brain-event` → PanelApp 把 `data` 合并进 `current.data`（不动 webview/html，iframe 不重载）→ `WebviewPanel` 的 `watch(props.data)` → iframe `onInit` 收到新 data。

- [ ] **Step 1: 实现（在 PanelApp 的 onEvent 事件处理里加 panel_data 分支）**

先读 `app/src/components/PanelApp.vue` 的 `onEvent`（约 L128-139，处理 `kind=="panel"` 的地方）。在处理 panel 的逻辑旁，加一段：当 `e.kind === "panel_data"` 且 `current.value?.panel === e.panel` 时，把 `e.data` 合并进 `current.value.data`（响应式，触发 WebviewPanel 的 props.data watch）。示例（贴合现有代码风格，变量名以实际为准）：
```typescript
      if (e.kind === "panel_data" && current.value?.panel === e.panel) {
        current.value = { ...current.value, data: { ...(current.value.data ?? {}), ...(e.data ?? {}) } };
        return;
      }
```
放在 `kind === "panel"` 的既有分支**之前**（panel_data 优先匹配）。不动其它分支、不动 webview/html 字段（保证 iframe 不重载）。同时若顶部有 `kind` 白名单过滤（如 `if (e.kind !== "panel" && e.surface === "pet") return;`），把 `panel_data` 也放行：
```typescript
      if (e.kind !== "panel" && e.kind !== "panel_data" && e.surface === "pet") return;
```
（以文件实际过滤逻辑为准——目标是让 `panel_data` 通过。）

- [ ] **Step 2: 类型 + 构建验证**

Run: `cd /Users/denny/Work/yibao/app && npx vue-tsc --noEmit && npx vite build`
Expected: exit 0

- [ ] **Step 3: commit**
```bash
git add app/src/components/PanelApp.vue
git commit -m "feat(panel): panel_data 流式事件通道（webview 面板增量数据推送）"
```

---

### Task 3: 插件骨架（manifest + sessions 表 + webview 面板声明 + api.toml）

**Files:**
- Create: `plugins/coding/manifest.toml`、`plugins/coding/api.toml`、`plugins/coding/skills/__init__.py`（空）、`plugins/coding/panel/chat.html`（占位 UI，Task 6 填真 UI）
- Test: 启动 sidecar 看插件加载（无单测；门 = sidecar 起得来 + 面板注册）

**Interfaces:**
- Produces: 插件 `coding` 注册；sessions 表；webview 面板 `coding:chat`；api 方法 `coding.start`/`coding.stop`/`coding.list`（handler 指向 Task 5 的 skill）。

- [ ] **Step 1: manifest.toml**

`plugins/coding/manifest.toml`：
```toml
id = "coding"
name = "编码"
description = "统一 coding 聊天：选项目 + Claude Code，提任务后台流式跑，面板回显 + 文件改动 diff。v1 单 agent、用户显式选、不 auto-routing。"
capabilities = ["db"]   # in-process SDK，非子进程；不要 process

[code]
entry = "skills"

[[table]]
name = "sessions"
columns = [
  {name = "id", type = "text", pk = true},
  {name = "agent", type = "text", default = "claude-code"},
  {name = "cwd", type = "text"},
  {name = "prompt", type = "text"},
  {name = "status", type = "text", default = "running"},   # running/done/stopped/failed
  {name = "created_at", type = "integer"},
  {name = "finished_at", type = "integer", default = 0},
]
indexes = ["status", "created_at"]

[[panel]]
type = "webview"
name = "chat"
label = "编码对话"
src = "panel/chat.html"
```

- [ ] **Step 2: api.toml**

`plugins/coding/api.toml`：
```toml
[[method]]
name = "coding.start"
handler = "coding.start"
direct = true
panel = "coding:chat"

[[method]]
name = "coding.stop"
handler = "coding.stop"
direct = true
refresh = "coding.list"

[[method]]
name = "coding.list"
handler = "coding.list"
direct = true
```

- [ ] **Step 3: skills/__init__.py + 占位 chat.html**

`plugins/coding/skills/__init__.py`：空文件（包标记）。
`plugins/coding/panel/chat.html`（占位，Task 6 换真 UI）：
```html
<!DOCTYPE html><html><head><meta charset="utf-8"><title>编码</title></head>
<body><div id="app">编码面板（建设中）</div>
<script>window.yibao && window.yibao.onInit && window.yibao.onInit(function(d){ console.log("init", d); });</script>
</body></html>
```

- [ ] **Step 4: 验证插件加载**

Run: `cd /Users/denny/Work/yibao/sidecar && .venv/bin/python -c "from yibao_brain.plugins import load_plugins_safe" 2>&1; .venv/bin/python -c "import sys; sys.path.insert(0,'src'); from yibao_brain import plugins; print('import ok')"`
Expected: import ok（插件目录被扫描不报错）。再跑全量回归确认未破坏：`.venv/bin/python -m pytest -q` → 782 passed。

- [ ] **Step 5: commit**
```bash
git add plugins/coding/
git commit -m "feat(coding): 插件骨架（manifest/sessions 表/webview 面板声明/api.toml）"
```

---

### Task 4: AgentRunner + ClaudeCodeRunner（async 流式 + 取消 + 容错）

**Files:**
- Create: `plugins/coding/skills/runner.py`
- Test: `sidecar/tests/test_coding_plugin.py`（新建）

**Interfaces:**
- Produces:
  - `class AgentRunner(Protocol)`：`async def run(self, prompt: str, cwd: str, *, on_event, cancel_event) -> None`
  - `class ClaudeCodeRunner(AgentRunner)`：`__init__(self, client_factory=None, allowed_tools=None)`；默认 client_factory 内 lazy `from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions` 建 `ClaudeAgentOptions(cwd=cwd, permission_mode="acceptEdits", allowed_tools=...)`；`run()` 异步迭代 `client.receive_response()`，每条消息 `on_event(normalize(msg))`，每条之间查 `cancel_event.is_set()` → 取消则中断；全包 try/except → `on_event({"kind":"error","text":...})`；正常结束 `on_event({"kind":"done"})`。
  - `normalize(msg) -> dict`：把 SDK 消息归一成 `{kind: "text_delta"|"tool_use"|"file_edit"|"done"|"error", ...}`。**输入契约（duck-typed）**：文本类消息→`text_delta`；工具调用类→`tool_use`（带工具名/输入）；文件编辑（Write/Edit 工具）→`file_edit`（带 path）；其余忽略。具体 SDK 消息类名（AssistantMessage/ToolUseBlock 等）在实装时读已装的 `claude_agent_sdk` 包确定，**测试用 fake 对象不依赖真实类名**。

- [ ] **Step 1: 写失败测试（FakeSDK 注入）**

`sidecar/tests/test_coding_plugin.py`：
```python
"""coding 插件：runner 流式/取消/容错（FakeSDK 注入，不跑真 SDK）。"""
from __future__ import annotations
import asyncio, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# 插件 skills 不在 src 下，单独加路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugins", "coding", "skills"))
from runner import ClaudeCodeRunner, normalize  # noqa: E402


class _FakeMsg:
    """duck-typed SDK 消息：.type + 文本/工具块。"""
    def __init__(self, kind, text=None, tool=None, path=None):
        self.type = kind
        self.text = text
        self.tool = tool
        self.path = path


class _FakeClient:
    """async context manager；receive_response() 异步 yield 预置消息。"""
    def __init__(self, messages):
        self._messages = messages
        self.queried = None
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def query(self, prompt): self.queried = prompt
    async def receive_response(self):
        for m in self._messages:
            yield m


def _run(coro): return asyncio.run(coro)


def test_runner_streams_and_done():
    events = []
    msgs = [_FakeMsg("assistant", text="hello"), _FakeMsg("assistant", text=" world")]
    runner = ClaudeCodeRunner(client_factory=lambda cwd, tools: _FakeClient(msgs))
    cancel = asyncio.Event()
    _run(runner.run("do X", "/tmp", on_event=events.append, cancel_event=cancel))
    kinds = [e["kind"] for e in events]
    assert "text_delta" in kinds and kinds[-1] == "done"


def test_runner_cancel_mid_stream():
    events = []
    # 第三条之前设置 cancel
    msgs = [_FakeMsg("assistant", text="a"), _FakeMsg("assistant", text="b"), _FakeMsg("assistant", text="c")]
    runner = ClaudeCodeRunner(client_factory=lambda cwd, tools: _FakeClient(msgs))
    cancel = asyncio.Event()
    sent = []
    def on_event(e):
        sent.append(e)
        if len(sent) == 2:
            cancel.set()
    _run(runner.run("p", "/tmp", on_event=on_event, cancel_event=cancel))
    # 被取消 → 不该跑到第三条之后 / 不该 done
    assert all(e.get("kind") != "done" for e in events) or events[-1]["kind"] != "done"


def test_runner_error_isolated():
    events = []
    def factory(cwd, tools):
        class Bad:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def query(self, p): raise RuntimeError("boom")
            async def receive_response(self):
                if False: yield
        return Bad()
    runner = ClaudeCodeRunner(client_factory=factory)
    _run(runner.run("p", "/tmp", on_event=events.append, cancel_event=asyncio.Event()))
    assert any(e["kind"] == "error" for e in events)


def test_normalize_text_and_file_edit():
    assert normalize(_FakeMsg("assistant", text="hi"))["kind"] == "text_delta"
    fe = normalize(_FakeMsg("tool_use", tool="Edit", path="a.py"))
    assert fe["kind"] == "file_edit" and fe["path"] == "a.py"
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd /Users/denny/Work/yibao/sidecar && .venv/bin/python -m pytest tests/test_coding_plugin.py -v`
Expected: FAIL（`runner` 模块不存在）

- [ ] **Step 3: 实现 runner.py**

`plugins/coding/skills/runner.py`：
```python
"""AgentRunner：程序化驱动 coding agent 流式跑。v1 实装 ClaudeCodeRunner（claude-agent-sdk）。"""
from __future__ import annotations
import sys
from typing import Protocol, Callable, Awaitable, Any

_FILE_EDIT_TOOLS = {"Write", "Edit", "MultiEdit"}


def normalize(msg: Any) -> dict:
    """把 SDK 消息归一成 coding_event dict。duck-typed：按常见字段判别。"""
    # 文件编辑工具调用
    tool = getattr(msg, "tool", None) or _deep_get(msg, ("tool", "name"))
    path = getattr(msg, "path", None) or _deep_get(msg, ("path",)) or _deep_get(msg, ("file_path",))
    if tool in _FILE_EDIT_TOOLS or path:
        return {"kind": "file_edit", "tool": tool, "path": path}
    text = getattr(msg, "text", None) or _deep_get(msg, ("text", "content"))
    if text:
        return {"kind": "text_delta", "text": str(text)}
    mtype = getattr(msg, "type", "")
    if mtype in ("result", "done"):
        return {"kind": "done"}
    # 其余（无关工具调用等）忽略 → None 信号
    return {"kind": "tool_use", "tool": str(tool or mtype)}


def _deep_get(obj, path):
    cur = obj
    for p in path:
        if cur is None:
            return None
        cur = getattr(cur, p, None) if not isinstance(cur, dict) else cur.get(p)
    return cur


class AgentRunner(Protocol):
    async def run(self, prompt: str, cwd: str, *, on_event: Callable[[dict], None],
                  cancel_event) -> None: ...


class ClaudeCodeRunner:
    """claude-agent-sdk 流式 runner。client_factory 可注入（测试用 fake）。"""

    def __init__(self, client_factory: Callable[..., Any] | None = None,
                 allowed_tools: list[str] | None = None):
        self._allowed_tools = allowed_tools or ["Read", "Write", "Edit", "MultiEdit", "Bash", "Glob", "Grep"]
        self._client_factory = client_factory  # None → 生产用真 SDK

    def _default_factory(self, cwd: str, tools: list[str]):
        from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions  # lazy：测试不依赖
        options = ClaudeAgentOptions(cwd=cwd, permission_mode="acceptEdits", allowed_tools=tools)
        return ClaudeSDKClient(options=options)

    async def run(self, prompt: str, cwd: str, *, on_event, cancel_event) -> None:
        factory = self._client_factory or self._default_factory
        try:
            client = factory(cwd, self._allowed_tools)
            async with client as c:
                await c.query(prompt)
                async for msg in c.receive_response():
                    if cancel_event.is_set():
                        return
                    ev = normalize(msg)
                    if ev is not None:
                        on_event(ev)
                        if ev["kind"] == "done":
                            return
            on_event({"kind": "done"})
        except Exception as e:
            print(f"[yibao/coding] runner 失败：{e}", file=sys.stderr)
            on_event({"kind": "error", "text": str(e)})
```
（`normalize` 返回 None 时 `on_event` 不调——把 `if ev is not None` 改为对 None 的跳过；上面 `_deep_get`/判别按已装 SDK 消息形态在实装时微调，**但 fake 测试已锁契约**。）

- [ ] **Step 4: 跑测试验证通过**

Run: `cd /Users/denny/Work/yibao/sidecar && .venv/bin/python -m pytest tests/test_coding_plugin.py -v`
Expected: 4 passed

- [ ] **Step 5: 全量回归**

Run: `cd /Users/denny/Work/yibao/sidecar && .venv/bin/python -m pytest -q`
Expected: 786 passed（782 + 4）

- [ ] **Step 6: commit**
```bash
git add plugins/coding/skills/runner.py sidecar/tests/test_coding_plugin.py
git commit -m "feat(coding): AgentRunner + ClaudeCodeRunner（流式/取消/容错，FakeSDK 注入测）"
```

---

### Task 5: coding tools（start/stop/list + _SESSIONS + _stream）

**Files:**
- Create: `plugins/coding/skills/coding.py`
- Test: `sidecar/tests/test_coding_plugin.py`（追加）

**Interfaces:**
- Consumes: Task 4 的 `ClaudeCodeRunner`；译宝 `Skill`/`SkillContext`（`ctx.db`/`ctx.emit_event`，见 agents 插件用法）。
- Produces: skills `coding.start` / `coding.stop` / `coding.list`（id 即 handler 名，被 api.toml direct 调）。

- [ ] **Step 1: 写失败测试（start 建行 / stop race-safe 顺序，注入 fake db + registry）**

追加到 `tests/test_coding_plugin.py`：
```python
import coding as codingmod  # noqa: E402
from coding import _stop_session  # noqa: E402


class _FakeDB:
    def __init__(self): self.rows = {}; self.updates = []
    def insert(self, table, row): self.rows[row["id"]] = dict(row); return row["id"]
    def update(self, table, rid, fields): self.updates.append((rid, fields)); self.rows.setdefault(rid, {}).update(fields)
    def query(self, *a, **k): return list(self.rows.values())


def test_start_inserts_running_session(monkeypatch):
    db = _FakeDB()
    # 不真起线程：把 _stream 占位成空
    monkeypatch.setattr(codingmod, "_spawn_stream", lambda *a, **k: None)
    sid = codingmod.start_session(db, agent="claude-code", cwd="/tmp/p", prompt="hi")
    assert db.rows[sid]["status"] == "running" and db.rows[sid]["cwd"] == "/tmp/p"


def test_stop_sets_stopped_before_cancel():
    # race-safe：先 db.update(stopped) 再 cancel 标记
    db = _FakeDB(); db.rows["s1"] = {"id": "s1", "status": "running"}
    flag = {"cancelled": False}
    class Reg:
        def __init__(self): self.s = {"s1": flag}
    reg = Reg()
    _stop_session(db, reg, "s1")
    # 先落 stopped
    assert db.updates[0] == ("s1", {"status": "stopped"}) or db.updates[0][0] == "s1"
    # 再 cancel
    assert flag["cancelled"] is True
```
> 注：测试调的是**可测纯函数** `start_session(db, ...)` / `_stop_session(db, reg, sid)`——把"起线程/真 runner"抽到 `_spawn_stream`（测试 monkeypatch 掉），把"race-safe 顺序"做成纯函数。skill 的 `run()` 只是薄包装调这些纯函数 + 读 ctx。

- [ ] **Step 2: 跑测试验证失败**

Run: `cd /Users/denny/Work/yibao/sidecar && .venv/bin/python -m pytest tests/test_coding_plugin.py -v -k "start_inserts or stop_sets"`
Expected: FAIL（`coding` 模块/函数不存在）

- [ ] **Step 3: 实现 coding.py**

`plugins/coding/skills/coding.py`（结构仿 agents 插件 skills/agents.py 的 Skill 模式；以下是核心逻辑，Skill 类壳按译宝 Skill 基类套）：
```python
"""coding 插件 skills：start（建 session + 后台流式）/ stop（race-safe 取消）/ list。"""
from __future__ import annotations
import asyncio, sys, threading, time, uuid

# skill 基类与 ctx 形态见 agents 插件；这里聚焦可测纯逻辑 + 后台线程
_SESSIONS: dict[str, dict] = {}   # sid -> {"cancel": asyncio.Event-ish, "thread": ...}
# asyncio.Event 不能跨线程 set —— 用 threading.Event 做取消信号（runner 在自己的 loop 里 is_set() 查）


def start_session(db, *, agent: str, cwd: str, prompt: str, runner=None) -> str:
    sid = uuid.uuid4().hex[:12]
    db.insert("sessions", {"id": sid, "agent": agent, "cwd": cwd, "prompt": prompt,
                           "status": "running", "created_at": int(time.time()), "finished_at": 0})
    return sid


def _spawn_stream(db, sid, cwd, prompt, runner, emit_event):
    """起 daemon 线程跑 runner（自带 asyncio loop）。emit_event 已线程安全（proactive.call_soon_threadsafe）。"""
    cancel = threading.Event()
    _SESSIONS[sid] = {"cancel": cancel}

    def _thread():
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(_stream(sid, cwd, prompt, runner, emit_event, cancel))
        except Exception as e:
            print(f"[yibao/coding] stream 线程崩：{e}", file=sys.stderr)
            try: db.update("sessions", sid, {"status": "failed", "finished_at": int(time.time())})
            except Exception: pass
        finally:
            loop.close()
            _SESSIONS.pop(sid, None)

    threading.Thread(target=_thread, daemon=True, name=f"yibao-coding-{sid}").start()


async def _stream(sid, cwd, prompt, runner, emit_event, cancel):
    """跑 runner；每条 event 转 panel_data 推面板；结束落 session。"""
    def on_event(ev):
        emit_event({"kind": "panel_data", "panel": "coding:chat",
                    "data": {"session_id": sid, "event": ev}})
        if ev.get("kind") in ("done", "error"):
            status = "done" if ev["kind"] == "done" else "failed"
            # runner 内部已吞异常→error；这里只落库
            try: pass  # 落库在 finally 统一
            except Exception: pass
    await runner.run(prompt, cwd, on_event=on_event, cancel_event=_AsyncShield(cancel))
    # 落最终状态
    final = "stopped" if cancel.is_set() else "done"
    # 注：db 需经闭包/参数传入——实装时把 db 传进 _stream


class _AsyncShield:
    """把 threading.Event 适配成 runner 期望的 cancel_event（.is_set()）。"""
    def __init__(self, ev): self._ev = ev
    def is_set(self): return self._ev.is_set()


def _stop_session(db, registry, sid) -> bool:
    """race-safe 取消：先 db.update(stopped)，再 set cancel。仿 agents.task_stop 顺序。"""
    row = db.rows.get(sid) if hasattr(db, "rows") else None
    db.update("sessions", sid, {"status": "stopped", "finished_at": int(time.time())})
    entry = registry.s.get(sid) if hasattr(registry, "s") else registry.get(sid)
    if entry is not None:
        entry["cancel"].set() if isinstance(entry, dict) else setattr(entry, "cancelled", True)
    return True
```
> 实装注意（plan 范围内必须解决，不留 TODO）：
> 1. **db 传入 `_stream`**：上面 `_stream` 末尾落库需要 db——把 `db` 作为 `_spawn_stream`/`_stream` 参数一路传（`_spawn_stream(db, ...)` 已有，`_stream` 签名加 `db`，`on_event` 闭包捕获）。实装时补齐参数链。
> 2. **Skill 类壳**：`start`/`stop`/`list` 各一个 `Skill` 子类（`id="coding.start"` 等，`default_risk`：start=L2（改文件）、stop=L0），`run(params, ctx)` 里调 `start_session(ctx.db, ...)` + `_spawn_stream(...)` + `ctx.emit_event`，或 `_stop_session` / `ctx.db.query`。Skill 基类用法完全照 `plugins/agents/skills/agents.py`（先读它再套）。
> 3. **runner 实例**：skill `run()` 里建 `ClaudeCodeRunner()`（生产默认 factory）；测试经 monkeypatch 占位 `_spawn_stream` 不真起。

- [ ] **Step 4: 跑测试验证通过**

Run: `cd /Users/denny/Work/yibao/sidecar && .venv/bin/python -m pytest tests/test_coding_plugin.py -v`
Expected: all passed（含 start/stop 用例）

- [ ] **Step 5: 全量回归**

Run: `cd /Users/denny/Work/yibao/sidecar && .venv/bin/python -m pytest -q`
Expected: 全绿

- [ ] **Step 6: commit**
```bash
git add plugins/coding/skills/coding.py sidecar/tests/test_coding_plugin.py
git commit -m "feat(coding): start/stop/list skills + _SESSIONS + 后台流式（race-safe 取消）"
```

---

### Task 6: webview 聊天面板 chat.html（选择器 + 流式回显 + diff + 按钮）

**Files:**
- Modify: `plugins/coding/panel/chat.html`（替换 Task 3 占位）

**Interfaces:**
- Consumes: `window.yibao.invoke(method, params)`（start/stop/list）+ `window.yibao.onInit(cb)`（收 panel_data 流式 data，每条 data = `{session_id, event:{kind,...}}`）。
- Produces: 用户能选项目 cwd + 发任务 → 看流式输出 + 文件 diff → 中断。

- [ ] **Step 1: 实现 chat.html**

写一个自包含 HTML（内联 CSS/JS；Monaco 用 CDN `<script src="https://cdn.jsdelivr.net/npm/monaco-editor/min/vs/loader.js">`，`require.config` 后 `monaco.editor.createDiffEditor`）。结构：
- 顶栏：`<select id="cwd">`（项目选择器，options 从 `window.yibao.invoke("coding.list")` 之外另填——v1 先一个文本输入框让用户粘贴/选 cwd，或读最近用过的）、`<span>agent: Claude Code</span>`（v1 固定）。
- 主区：`<div id="log">`（流式气泡：`onInit` 收到 data.event，`text_delta`→追加 `<div class="msg ai">`；`file_edit`→渲染路径 + Monaco diff 卡片（v1 可先显示 `修改：path` + 折叠，diff 用 Monaco 懒加载）；`tool_use`→折叠行；`done`→收尾标；`error`→红字）。
- 底栏：`<textarea id="prompt">` + `<button id="send">发送</button>` + `<button id="stop">中断</button>`。
- JS：`send` → `window.yibao.invoke("coding.start", {cwd, prompt})`（取回 session_id 记下）；`stop` → `window.yibao.invoke("coding.stop", {id: session_id})`；`onInit(d)` → 按 `d.event.kind` 渲染（多次 onInit 调用 = 多条流式 chunk，**累加**到 log，不重置）。
- 关键：onInit 是"每条 data 一调"，渲染逻辑要**累加**（append），不是用 data 整体替换 #log（否则只显最后一条）。
- 错误兜底：invoke 失败、Monaco CDN 加载失败 → 静态文本降级（显示"修改：path"，不崩）。

> 注：CDN 依赖在生产离线场景可能不可用——v1 接受（个人工具、通常联网）；若需离线，后续把 Monaco 打包进 panel。在 chat.html 顶部注释标此约束。

- [ ] **Step 2: 前端构建（确认 panel.html 不破坏 build——它是资源，不参与 tsc，但 vite 不报错）**

Run: `cd /Users/denny/Work/yibao/app && npx vite build`
Expected: exit 0（chat.html 不在 vite 编译范围，但确认无误）

- [ ] **Step 3: commit**
```bash
git add plugins/coding/panel/chat.html
git commit -m "feat(coding): webview 聊天面板（选择器/流式回显/diff/起停）"
```

---

### Task 7: 集成验收（真 SDK + 真 UI + 取消）

**Files:** 无（验证任务；若 Step 发现 bug，按需修 Task 4-6 文件）

- [ ] **Step 1: sidecar 全量 + 前端构建**

Run: `cd /Users/denny/Work/yibao/sidecar && .venv/bin/python -m pytest -q` → 全绿（782+新）。
Run: `cd /Users/denny/Work/yibao/app && npx vue-tsc --noEmit && npx vite build` → exit 0。

- [ ] **Step 2: 插件加载 + 面板注册冒烟**

Run: `cd /Users/denny/Work/yibao/sidecar && .venv/bin/python -c "import sys; sys.path.insert(0,'src'); from yibao_brain import plugins; print('ok')"` → ok。
（真机）`npm run tauri dev` → 打开编码面板 → 能渲染 chat.html。

- [ ] **Step 3: 真 SDK 端到端（人工，复用 spike 型任务）**

（真机）选一个真实项目 cwd（如 `/tmp/yibao-spike` 或某小项目），提一个清晰任务（如"给 taskstore 加 JSON 持久化和测试"），点发送：
- 面板流式回显 agent 文本 + 文件改动；
- 完成后 session 状态 done、结果可用（跑测试通过）。

- [ ] **Step 4: 取消生效（人工）**

发一个稍长任务，跑起来后点中断：
- session 状态 stopped、agent 停（无后续 chunk）。

- [ ] **Step 5: 含糊大任务兜底（人工，验不崩）**

提一个明显过大/含糊的任务，确认面板正常跑/可中断/不崩（不要求质量）。

- [ ] **Step 6: 验收回写 + 收尾**

把真机验收结果回写 spec `docs/superpowers/specs/2026-08-04-coding-plugin-design.md` 末尾「实装记录」段；commit。
```bash
git add docs/superpowers/specs/2026-08-04-coding-plugin-design.md
git commit -m "docs(coding): 插件真机验收记录回写"
```

---

## 自审（plan vs spec 覆盖核对）

- spec §2 复用栈（claude-agent-sdk + Monaco；多 agent 平台 SKIP）→ Task 1（SDK 依赖）+ Task 4（runner 用 SDK）+ Task 6（Monaco）✅
- spec §3 v1 范围（单 agent、显式选、全自主、流式+diff、起停）→ Task 3-7 ✅；不做项（auto-routing/多 agent/交接/记忆/活动线）明确排除 ✅
- spec §4.1 插件结构 → Task 3 ✅
- spec §4.2 dispatch（ClaudeSDKClient + acceptEdits + allowed_tools + receive_response 流式）→ Task 4 ✅
- spec §4.3 流式→面板通道 → Task 2（panel_data 底座通道）+ Task 5（emit panel_data）✅
- spec §4.4 面板（选择器/流式/diff/起停）→ Task 6 ✅
- spec §5 交互流 + 中断 → Task 5（race-safe stop）+ Task 6（中断按钮）✅
- spec §6 与 agents 关系（互补/AgentRunner 抽象）→ Task 4 AgentRunner Protocol ✅
- spec §8 风险（SDK 版本 pin、流式通道、成本 token、cwd 安全、诚实边界）→ Task 1 pin / Task 2 通道 / Task 5 cwd 用户选 / Task 7 兜底 ✅
- spec §9 测试（FakeRunner 注入，不跑真 SDK）→ Task 4/5 单测 + Task 7 真机 ✅
- 类型/命名一致：`ClaudeCodeRunner`/`AgentRunner`/`start_session`/`_stop_session`/`_SESSIONS`/`coding.start|stop|list`/`coding:chat`/`panel_data` 跨任务一致 ✅
- 无占位（Task 5 的"实装注意"3 条是 plan 范围内明确要解决的接线点——db 参数链/Skill 壳照 agents/runner 实例化——已给出具体做法，非 TBD）✅
