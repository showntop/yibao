# Coding 线产品化（第一轮）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** coding 面板从 demo 到可用：灭三个致命伤（中断假死/流式重入/次次确认），补透明（tool_result/thinking/token），重做 diff，加会话历史抽屉，落地文件夹选择器。

**Architecture:** 后端（`_runner.py` normalize 扩事件 / `coding.py` SendSkill 防重入 + risk 降级 + HistorySkill / `_cc_reader.py` 读 CC transcript）+ 面板（chat.html：stopped 终态/发送禁用/渲染新 kind/状态条/diff 默认展开/历史抽屉）+ Tauri（dialog 插件 pick_folder + WebviewPanel native: 旁路）。

**Tech Stack:** Python（claude-agent-sdk runner/pytest）、原生 JS（chat.html iframe）、Rust+Tauri v2（dialog 插件）。

**关联 spec：** `docs/superpowers/specs/2026-08-09-coding-productize-design.md`；**复用计划：** `docs/superpowers/plans/2026-08-06-coding-folder-picker-division.md`（Task 7 照其执行）。

## Global Constraints

- **SDK 字段 duck-typing 防御**：usage/thinking/tool_result 拿不到就降级不渲染，绝不阻断流。
- **历史读回静默降级**：transcript 解析失败 → `{messages:[]}`，不许把「恢复」变「报错」。
- **sidecar pytest 全绿（基线 864）；vue-tsc/vite build exit 0；cargo check exit 0**。
- **commit**：每任务一 commit，中文 scope（`feat(coding)`/`fix(coding)`），仅 stage 本任务文件，不动 `.gitignore`，提交在 `feat/coding-productize` 分支。
- 面板 iframe 内无 Tauri IPC：原生能力必须走 WebviewPanel `native:` 白名单旁路（Task 7 建）。

## File Structure

**改：**
- `plugins/coding/skills/_runner.py`（stopped 终态 + normalize 扩 thinking/tool_result/usage）
- `plugins/coding/skills/coding.py`（SendSkill 防重入 + L2→L1 + HistorySkill）
- `plugins/coding/skills/_cc_reader.py`（新建，读 ~/.claude/projects transcript）
- `plugins/coding/api.toml`（history quiet 条目）
- `plugins/coding/panel/chat.html`（stopped/禁用/渲染/diff/历史/📁）
- `sidecar/tests/test_coding_plugin.py`、`sidecar/tests/test_coding_handoff.py`
- `app/src-tauri/Cargo.toml`、`app/package.json`、`app/src-tauri/src/lib.rs`、`app/src-tauri/capabilities/*.json`、`app/src/components/WebviewPanel.vue`
- `sidecar/src/yibao_brain/loop.py`（SYSTEM_PROMPT steer）+ `sidecar/tests/test_loop.py`（或既有断言文件）

---

### Task 1: 中断假死修复（runner stopped 终态 + 面板复位）

**Files:**
- Modify: `plugins/coding/skills/_runner.py`（run 取消分支 :139-141）
- Modify: `plugins/coding/panel/chat.html`（handleInitData switch，:690-721）
- Test: `sidecar/tests/test_coding_plugin.py`（追加）

**Interfaces:**
- Consumes: 现有取消语义（cancel_event.is_set() 早退）、`_stream` 的 stopped 落库保留逻辑（coding.py:164-168）
- Produces: runner 取消时 `on_event({"kind":"stopped","text":"已中断"})`；面板 `stopped` kind → `onSessionEnded()`

- [ ] **Step 1: 写失败测试**

`sidecar/tests/test_coding_plugin.py` 追加（读文件头部复用既有 FakeSDK/注入模式）：

```python
def test_runner_cancel_emits_stopped_terminal_event():
    """取消路径必须发 stopped 终态事件（此前静默早退 → 面板永远卡在「运行中」）。"""
    events = []
    runner = ClaudeCodeRunner(client_factory=_cancel_immediately_factory())
    _run_async(runner.run("p", "/tmp", on_event=events.append, cancel_event=_SetEvent()))
    assert any(ev.get("kind") == "stopped" for ev in events), events
    assert not any(ev.get("kind") == "done" for ev in events), events
```

（`_cancel_immediately_factory`：返回首条消息即触发取消的 fake client；`_SetEvent`：`is_set()` 恒 True 的替身——照文件里既有 cancel 测试的替身写法定名复用。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd sidecar && uv run pytest tests/test_coding_plugin.py -k stopped -x -q`
Expected: FAIL（无 stopped 事件）

- [ ] **Step 3: 实现**

`plugins/coding/skills/_runner.py` `run` 的取消分支改为：

```python
                async for msg in c.receive_response():
                    if cancel_event.is_set():
                        # 取消必须给终态：此前静默 return → 面板永远停「运行中」、按钮锁死
                        on_event({"kind": "stopped", "text": "已中断"})
                        return cc_sid
```

（返回值改 `cc_sid`：取消也尽力把已捕获的 cc_session_id 交回——`_stream` 落库注释明确要求 stopped 也记录，原 `return None` 会丢。）docstring 的「取消则早退（不发 done）」更新为「取消则发 stopped 终态后早退」。

`plugins/coding/panel/chat.html` `handleInitData` 的 switch 在 `case "done"` 前插入：

```js
      case "stopped":
        if (currentBubble) {
          currentBubble.classList.add("done");
          currentBubble = null;
        }
        appendMarker(ev.text || "已中断", true);
        onSessionEnded();
        break;
```

- [ ] **Step 4: 跑测试 + 全量回归**

Run: `cd sidecar && uv run pytest tests/test_coding_plugin.py -q && uv run pytest -q`
Expected: 新 1 PASS；全量 864+ 全绿

- [ ] **Step 5: Commit**

```bash
git add plugins/coding/skills/_runner.py plugins/coding/panel/chat.html sidecar/tests/test_coding_plugin.py
git commit -m "fix(coding): 中断假死——runner 取消发 stopped 终态事件，面板即时复位（顺带取消也保住 cc_session_id）"
```

---

### Task 2: 流式防重入（SendSkill 检查 + 面板禁用至终态）

**Files:**
- Modify: `plugins/coding/skills/coding.py`（SendSkill.run :295-321）
- Modify: `plugins/coding/panel/chat.html`（send() :739-784、onSessionEnded :724-734）
- Test: `sidecar/tests/test_coding_plugin.py`（追加）

**Interfaces:**
- Consumes: Task 1 的终态事件（done/stopped/error 都调 onSessionEnded）
- Produces: SendSkill running 拒绝文案「会话正在运行中，请先中断或等待完成」

- [ ] **Step 1: 写失败测试**

`sidecar/tests/test_coding_plugin.py` 追加：

```python
def test_send_rejects_when_session_running():
    """会话 running 时 send 必须拒绝（防同 sid 双 runner 竞态）；终态会话放行。"""
    ctx = _make_ctx_with_session(status="running")  # 仿文件既有 ctx 构造 helper 命名
    r = SendSkill().run({"id": "sid-1", "prompt": "再来一条"}, ctx)
    assert not r.success and "正在运行" in (r.error or "")
    ctx2 = _make_ctx_with_session(status="done")
    r2 = SendSkill().run({"id": "sid-1", "prompt": "再来一条"}, ctx2)
    assert r2.success, r2.error
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd sidecar && uv run pytest tests/test_coding_plugin.py -k running -x -q`
Expected: FAIL（running 会话也被放行）

- [ ] **Step 3: 实现**

`plugins/coding/skills/coding.py` SendSkill.run 在 `row = rows[0]` 之后插入：

```python
        if row.get("status") == "running":
            return ActionResult(success=False, error="会话正在运行中，请先中断或等待完成")
```

`plugins/coding/panel/chat.html` `send()` 的 `.finally`（:778-783）改为「终态才解锁」：

```js
    }).finally(function () {
      sending = false;
      // 发送键解锁挪到 onSessionEnded（done/stopped/error）——
      // invoke 返回只代表「已受理」，流式期间禁止再发（防同会话双 runner）
      if (!streaming) $("send").disabled = false;
      $("new-chat").disabled = !currentSession || streaming;
    });
```

（`onSessionEnded` 里已有 `$("send").disabled = false`（:729），终态后自然解锁，无需再改。）

- [ ] **Step 4: 跑测试 + 全量回归**

Run: `cd sidecar && uv run pytest tests/test_coding_plugin.py -q && uv run pytest -q`
Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add plugins/coding/skills/coding.py plugins/coding/panel/chat.html sidecar/tests/test_coding_plugin.py
git commit -m "fix(coding): 流式防重入——SendSkill 拒 running 会话，发送键锁到终态"
```

---

### Task 3: start/send 确认降噪（L2→L1）

**Files:**
- Modify: `plugins/coding/skills/coding.py`（StartSkill.default_risk :223、SendSkill.default_risk :275）
- Test: `sidecar/tests/test_coding_plugin.py`（断言更新）

**Interfaces:**
- Consumes: 无
- Produces: 无（行为变化：面板直调/对话调用 start/send 不再弹确认；文件改动仍由 SDK acceptEdits 管）

- [ ] **Step 1: 改断言（红）**

`sidecar/tests/test_coding_plugin.py` 中凡断言 `default_risk == RiskLevel.L2_MEDIUM`（或语义等价）处改为 `L1_LOW`；无此断言则加一个：

```python
def test_start_send_are_l1_no_confirm():
    """会话启动/续聊 = L1（直调不弹确认）：文件改动已由 SDK permission_mode=acceptEdits 管理，
    高频对话循环不该每次弹风险确认。"""
    assert StartSkill.default_risk == RiskLevel.L1_LOW
    assert SendSkill.default_risk == RiskLevel.L1_LOW
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd sidecar && uv run pytest tests/test_coding_plugin.py -k l1 -x -q`
Expected: FAIL（当前是 L2）

- [ ] **Step 3: 实现**

两处 `default_risk = RiskLevel.L2_MEDIUM   # 改文件` 改为：

```python
    default_risk = RiskLevel.L1_LOW   # 会话启动/续聊本身不执行高危动作；文件改动由 SDK acceptEdits 管理
```

- [ ] **Step 4: 全量回归**

Run: `cd sidecar && uv run pytest -q`
Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add plugins/coding/skills/coding.py sidecar/tests/test_coding_plugin.py
git commit -m "feat(coding): start/send 风险降 L1——对话循环不再次次弹确认（文件改动仍由 SDK acceptEdits 管）"
```

---

### Task 4: 透明渲染（thinking/tool_result/usage + 状态条）

**Files:**
- Modify: `plugins/coding/skills/_runner.py`（normalize :24-47 + _normalize_block :50-63 + done 事件）
- Modify: `plugins/coding/panel/chat.html`（handleInitData switch、appendToolUse 区域、setStatus/状态行 :299,774、done 处理 :704-711）
- Test: `sidecar/tests/test_coding_plugin.py`（追加）

**Interfaces:**
- Consumes: SDK 消息形态（ThinkingBlock.thinking、ToolResultBlock.content/is_error、ResultMessage.duration_ms/total_cost_usd/usage——**全部 duck-typing，拿不到降级**）
- Produces: 事件 `{"kind":"thinking","text"}`、`{"kind":"tool_result","text","is_error"}`、`{"kind":"done","usage":{"input_tokens"?,"output_tokens"?,"cost_usd"?,"duration_ms"?}}`

- [ ] **Step 1: 写失败测试**

`sidecar/tests/test_coding_plugin.py` 追加：

```python
def test_normalize_thinking_block():
    block = SimpleNamespace(type="thinking", thinking="先看一下结构再改" * 1)
    msg = SimpleNamespace(content=[block])
    evs = normalize(msg)
    assert evs == [{"kind": "thinking", "text": "先看一下结构再改"}]


def test_normalize_tool_result_from_user_message():
    block = SimpleNamespace(content="file contents here", is_error=False)
    msg = SimpleNamespace(content=[block])  # UserMessage-like（类名含 User 由 fake 类名控制）
    msg.__class__.__name__ = "UserMessage"
    evs = normalize(msg)
    assert evs == [{"kind": "tool_result", "text": "file contents here", "is_error": False}]


def test_normalize_done_carries_usage():
    msg = SimpleNamespace(subtype="success", is_error=False,
                          duration_ms=12345, total_cost_usd=0.012,
                          usage={"input_tokens": 3000, "output_tokens": 200})
    evs = normalize(msg)
    assert evs[0]["kind"] == "done"
    u = evs[0]["usage"]
    assert u["duration_ms"] == 12345 and u["cost_usd"] == 0.012
    assert u["input_tokens"] == 3000 and u["output_tokens"] == 200
```

（`SimpleNamespace` 文件顶部若无则补 import；第三个用例的 subtype/is_error 命中既有 ResultMessage 判定分支。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd sidecar && uv run pytest tests/test_coding_plugin.py -k "thinking or tool_result or usage" -x -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`plugins/coding/skills/_runner.py`：

① normalize 顶部「User 排除」改为提取 tool_result：

```python
    mtype = type(msg).__name__
    # UserMessage：提取 tool_result（工具输出可见是透明底线）；SystemMessage 仍忽略
    if "User" in mtype:
        return _tool_result_events(msg)
    if "System" in mtype:
        return []
```

② 新函数（_normalize_block 之后）：

```python
def _tool_result_events(msg: Any) -> list[dict]:
    """UserMessage 的 ToolResultBlock → tool_result 事件（截 800 字；content 为 str 或块列表，鸭子类型）。"""
    content = getattr(msg, "content", None)
    if not isinstance(content, list):
        return []
    out: list[dict] = []
    for block in content:
        is_err = bool(getattr(block, "is_error", False))
        bc = getattr(block, "content", None)
        if bc is None:
            continue
        if isinstance(bc, list):  # 块列表：拼 text 段
            text = "".join(str(getattr(b, "text", "")) for b in bc)
        else:
            text = str(bc)
        out.append({"kind": "tool_result", "text": text[:800], "is_error": is_err})
    return out
```

③ `_normalize_block` 加 thinking 分支（在 name/input 判断之前）：

```python
    btype = type(block).__name__
    if "Thinking" in btype:
        thinking = getattr(block, "thinking", None) or getattr(block, "text", "")
        return {"kind": "thinking", "text": str(thinking)[:500]}
```

④ done 事件携带 usage（normalize 的 ResultMessage 分支 :41-43）：

```python
    # 2) ResultMessage-like：终态（usage 鸭子类型，拿不到就空 dict 降级）
    if "Result" in mtype or (hasattr(msg, "subtype") and hasattr(msg, "is_error")):
        usage: dict = {}
        for src, dst in (("duration_ms", "duration_ms"), ("total_cost_usd", "cost_usd")):
            v = getattr(msg, src, None)
            if v is not None:
                usage[dst] = v
        raw = getattr(msg, "usage", None)
        if isinstance(raw, dict):
            for k in ("input_tokens", "output_tokens"):
                if raw.get(k) is not None:
                    usage[k] = raw[k]
        return [{"kind": "done", "usage": usage}]
```

⑤ `plugins/coding/panel/chat.html` handleInitData switch 加两个 case + done 用法：

```js
      case "thinking":
        if (!currentBubble) currentBubble = appendAssistantBubble();
        appendThinking(currentBubble, ev.text || "");
        break;
      case "tool_result":
        appendToolResult(ev);
        break;
```

`done` case 改（:704-711）：

```js
      case "done":
        if (currentBubble) {
          currentBubble.classList.add("done");
        } else {
          appendMarker("完成", false);
        }
        currentBubble = null;
        setStatusDone(ev.usage);
        onSessionEnded();
        break;
```

新函数（放在 appendToolUse 附近）：

```js
  // 思考：淡色斜体小块（💭 改「思考」标签，不用 emoji）
  function appendThinking(bubble, text) {
    if (!text) return;
    var d = document.createElement("div");
    d.className = "think-block";
    d.innerHTML = "<span class=\"think-tag\">思考</span>" + esc(text);
    bubble.appendChild(d);
    scrollLog();
  }

  // 工具输出：挂到最近一张工具卡下（无卡则独立暗块），截断显示
  function appendToolResult(ev) {
    var d = document.createElement("details");
    d.className = "tool-result" + (ev.is_error ? " err" : "");
    d.innerHTML = "<summary>结果" + (ev.is_error ? "（错误）" : "") + "</summary><pre>" + esc(ev.text || "") + "</pre>";
    if (lastToolCard) lastToolCard.appendChild(d);
    else $("log").appendChild(d);
    scrollLog();
  }

  // 完成状态行：耗时 + token（有 usage 才显示）
  function setStatusDone(u) {
    var parts = ["✓ 完成"];
    if (u) {
      if (u.duration_ms != null) parts.push(Math.round(u.duration_ms / 1000) + "s");
      var tok = ((u.input_tokens || 0) + (u.output_tokens || 0));
      if (tok) parts.push(tok >= 1000 ? (tok / 1000).toFixed(1) + "k tok" : tok + " tok");
      if (u.cost_usd != null) parts.push("$" + u.cost_usd.toFixed(3));
    }
    setStatus(parts.join(" · "));
  }
```

`appendToolUse` 里记录 `lastToolCard = card`（新模块级变量，与 currentBubble 同域）；流式运行状态条（send() 的 setStatus 处与 done 对应）：把 :774 的 setStatus 改为启动秒表：

```js
      startTicker("会话 " + sid + (isStart ? " 启动" : " 接续"));
```

新函数：

```js
  var ticker = null, tickStart = 0;
  function startTicker(prefix) {
    stopTicker();
    tickStart = Date.now();
    setStatus(prefix + " · 0s", false, true);  // 第三个参 = spinner 开
    ticker = setInterval(function () {
      setStatus(prefix + " · " + Math.floor((Date.now() - tickStart) / 1000) + "s", false, true);
    }, 1000);
  }
  function stopTicker() { if (ticker) { clearInterval(ticker); ticker = null; } }
```

`onSessionEnded` 首行加 `stopTicker();`；`setStatus` 加 spinner 参数（读现有实现，`status` 行前插一个 CSS spinner span，第三个参控制显隐；spinner 样式：10px 边框圆 + 旋转 keyframes）。

- [ ] **Step 4: 跑测试 + 全量回归 + 构建**

Run: `cd sidecar && uv run pytest tests/test_coding_plugin.py -q && uv run pytest -q && cd ../app && npx vite build`
Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add plugins/coding/skills/_runner.py plugins/coding/panel/chat.html sidecar/tests/test_coding_plugin.py
git commit -m "feat(coding): 透明渲染——thinking/工具输出/耗时token 可见 + 运行状态条 spinner 秒表"
```

---

### Task 5: diff 重做（删 Monaco 死代码 + 默认展开 + 统计）

**Files:**
- Modify: `plugins/coding/panel/chat.html`（Monaco loader :630-672、file_edit 卡片 :415-453、diff 函数 :455-488,:541-574、头部注释 :9-14）

**Interfaces:**
- Consumes: 既有 LCS 行级 diff 函数（chat.html:541-574）
- Produces: file_edit 卡片默认展开 + 头部 `+x/-y` 统计 + 着色 diff；MultiEdit 分段渲染

- [ ] **Step 1: 删 Monaco 死代码**

删除：头部注释段（:9-14，改写为「diff 为内建行级 LCS 着色，默认展开」）、`monacoPromise`/`loadMonaco` 全部代码（:630-672）、`renderMonacoDiff` 调用点与 10s 超时降级逻辑（:647-649）。保留纯文本 diff 路径为唯一路径。

- [ ] **Step 2: file_edit 卡片默认展开 + 统计**

`appendFileEdit`（:415-453 区域）：

- 卡片不再用 `<details>` 折叠——直接渲染展开态（头行 + diff 体）；
- 头行加统计：对 Edit（有 old+new）先跑 LCS 得行数 `+a/-d`，渲染为 `✎ path <span class="add">+a</span> <span class="del">-d</span> <span class="tool-tag">Edit</span>`；
- diff 体着色 class：`diff-add`（绿底）/`diff-del`（红底删除线）/`diff-ctx`（灰）；Write = 全文 `diff-add`；
- MultiEdit：`JSON.parse(ev.new)` 得 edits 数组（parse 失败退回原文本），逐段渲染 `old_string→new_string` 的 LCS diff（每段带头行 `第 N 处`）。

头行元素结构建议：

```js
  function editHeader(path, tool, stats) {
    var h = document.createElement("div");
    h.className = "edit-head";
    h.innerHTML = "✎ " + esc(path || "(未知文件)") +
      (stats ? " <span class=\"add\">+" + stats.a + "</span> <span class=\"del\">-" + stats.d + "</span>" : "") +
      " <span class=\"tool-tag\">" + esc(tool || "") + "</span>";
    return h;
  }
```

样式（文件 style 区加）：`.diff-add{background:rgba(22,163,74,.12)} .diff-del{background:rgba(220,38,38,.12);text-decoration:line-through} .diff-ctx{color:var(--muted,#94a3b8)} .add{color:#16a34a} .del{color:#dc2626} .tool-tag{opacity:.55;font-size:11px} .edit-head{font-family:var(--mono,ui-monospace);font-size:12px;margin:6px 0 2px}`（配色变量按 chat.html 现有 slate/sky 变量对齐，没有就用字面量。）

- [ ] **Step 3: 构建**

Run: `cd app && npx vite build`
Expected: exit 0

- [ ] **Step 4: Commit**

```bash
git add plugins/coding/panel/chat.html
git commit -m "feat(coding): diff 重做——删 Monaco 死代码，改动卡片默认展开 + +x/-y 统计 + 着色 + MultiEdit 分段"
```

---

### Task 6: 会话历史抽屉（_cc_reader + coding.history + 抽屉 UI）

**Files:**
- Create: `plugins/coding/skills/_cc_reader.py`
- Modify: `plugins/coding/skills/coding.py`（HistorySkill，HandoffBriefSkill 附近 :426-489）
- Modify: `plugins/coding/api.toml`（history quiet 条目）
- Modify: `plugins/coding/panel/chat.html`（header 加「历史」按钮 + 抽屉浮层 + 恢复逻辑）
- Test: `sidecar/tests/test_coding_handoff.py`（追加）

**Interfaces:**
- Consumes: sessions 表（cc_session_id/cwd/prompt/status/created_at）、`coding.list`（既有，返回 rows）、`_codex_reader.py` 的读者模式（:20-84）
- Produces:
  - `_cc_reader.read_transcript(cc_session_id, limit=40) -> list[{"role": str, "text": str}]`
  - api `coding.history {id} -> {session_id, cwd, cc_session_id, messages}`（direct+quiet）
  - 面板：历史抽屉 + 点击恢复（设 currentSession + 渲染历史 + 分隔线）

- [ ] **Step 1: 写失败测试**

`sidecar/tests/test_coding_handoff.py` 追加：

```python
def test_cc_reader_reads_last_messages(tmp_path, monkeypatch):
    """read_transcript：按 session id 定位 jsonl，提取 user/assistant 文本，倒序截 limit。"""
    import json as _json
    proj = tmp_path / ".claude" / "projects" / "some-proj"
    proj.mkdir(parents=True)
    lines = [
        {"type": "user", "message": {"content": "第一句话"}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "回答一"}]}},
        {"type": "system", "message": {"content": "忽略我"}},
        {"type": "user", "message": {"content": [{"type": "text", "text": "第二句话"}]}},
    ]
    (proj / "cc-abc-123.jsonl").write_text("\n".join(_json.dumps(x) for x in lines), encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    from _cc_reader_for_test import load  # 见下：按文件加载 _cc_reader（skills 自包含惯例）
    msgs = load().read_transcript("cc-abc-123", limit=40)
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert msgs[0]["text"] == "第一句话" and msgs[2]["text"] == "第二句话"


def test_cc_reader_missing_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from _cc_reader_for_test import load
    assert load().read_transcript("ghost", limit=40) == []


def test_history_skill_returns_messages(tmp_path, monkeypatch):
    """coding.history：会话存在 → 带 messages；不存在 → 失败。"""
    # 仿文件既有 ctx 构造：sessions 行 {id, cc_session_id, cwd}
    ...
```

（`_cc_reader_for_test.load` helper：importlib 按路径加载 `plugins/coding/skills/_cc_reader.py` 并取 `read_transcript`——照 test_coding_handoff.py 里 `_codex_reader` 的既有加载 helper 写法定名复用；HistorySkill 测试的 ctx 构造仿该文件既有模式。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd sidecar && uv run pytest tests/test_coding_handoff.py -k "cc_reader or history" -x -q`
Expected: FAIL

- [ ] **Step 3: 实现**

① `plugins/coding/skills/_cc_reader.py`：

```python
"""读 Claude Code 本地 transcript（~/.claude/projects/**/*.jsonl）：会话历史恢复用。

私有格式、鸭子类型防御：任何一步失败都返回 []（恢复不了就当新会话，绝不报错）。
每行一个 JSON：{"type":"user"|"assistant"|...,"message":{"content": str|list}}。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_MAX_LINE_KB = 512  # 单行防御上限（超长行截断解析）


def read_transcript(cc_session_id: str, limit: int = 40) -> list[dict]:
    """按 session id 找 transcript，提取最近 limit 条 user/assistant 文本消息（时间正序）。"""
    if not cc_session_id:
        return []
    try:
        base = Path(os.path.expanduser("~/.claude/projects"))
        hits = sorted(base.glob(f"**/{cc_session_id}.jsonl"))
        if not hits:
            return []
        rows: list[dict] = []
        with open(hits[-1], encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line[: _MAX_LINE_KB * 1024])
                except json.JSONDecodeError:
                    continue
                role = row.get("type")
                if role not in ("user", "assistant"):
                    continue
                text = _text_of(row.get("message"))
                if text:
                    rows.append({"role": role, "text": text})
        return rows[-limit:] if limit else rows
    except Exception:
        return []


def _text_of(message: Any) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            else:
                t = getattr(block, "text", None)  # SDK 对象序列化形态
                if t:
                    parts.append(str(t))
        return "\n".join(p for p in parts if p).strip()
    return ""
```

② `plugins/coding/skills/coding.py` 加 HistorySkill（HandoffBriefSkill 之后）+ `make_tools` 注册：

```python
class HistorySkill(Skill):
    id = "coding.history"
    label = "读取会话历史"
    description = "读取某个 coding 会话的信息与最近消息（恢复旧会话用）：读 Claude Code 本地 transcript，失败静默为空。"
    default_risk = RiskLevel.L0_READ

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {"id": {"type": "string", "description": "会话 id"}},
                    "required": ["id"],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        sid = str(params.get("id") or "").strip()
        rows = ctx.db.query("sessions", where={"id": sid})
        if not rows:
            return ActionResult(success=False, error=f"会话不存在：{sid}")
        row = rows[0]
        cc = row.get("cc_session_id") or ""
        reader = _sibling("_cc_reader")
        messages = reader.read_transcript(cc, limit=40) if cc else []
        return ActionResult(success=True, data={
            "session_id": sid, "cwd": row.get("cwd") or "", "cc_session_id": cc,
            "prompt": row.get("prompt") or "", "messages": messages,
        })
```

（`_sibling("_cc_reader")` 沿用 coding.py:31-46 的兄弟模块加载惯例；`RiskLevel.L0_READ` 名按 sidecar 枚举实际值对齐——若没有 L0_READ 用既有最低级。）

③ `plugins/coding/api.toml` 追加：

```toml
# 历史抽屉：读会话最近消息（只读；quiet 不发 panel 事件——抽屉已在面板内）
[[method]]
name = "history"
handler = "coding.history"
direct = true
quiet = true
```

④ `plugins/coding/panel/chat.html`：

header（:290-291 「↩ 从 Codex 接续」「新对话」旁）加 `<button id="history" class="btn ghost">历史</button>`。

抽屉（新函数，仿 handoff picker 的 fixed 浮层 :849-894 结构）：

```js
  // ---- 历史抽屉：列会话（coding.list）→ 点击恢复（coding.history 读回 + 接着聊）----
  function openHistory() {
    if (!hasBridge) return;
    var ov = $("history-overlay");
    ov.hidden = false;
    var body = $("history-body");
    body.innerHTML = "<div class=\"mempty\">加载中…</div>";
    window.yibao.invoke("coding.list", {}).then(function (r) {
      var rows = (r && r.rows) || [];
      if (!rows.length) { body.innerHTML = "<div class=\"mempty\">还没有会话</div>"; return; }
      body.innerHTML = "";
      rows.forEach(function (row) {
        var b = document.createElement("button");
        b.className = "hrow";
        var when = row.created_at ? new Date(row.created_at * 1000) : null;
        var time = when ? (when.getMonth() + 1) + "/" + when.getDate() + " " + String(when.getHours()).padStart(2, "0") + ":" + String(when.getMinutes()).padStart(2, "0") : "";
        var cwdShort = (row.cwd || "").split("/").filter(Boolean).pop() || row.cwd || "";
        var first = String(row.prompt || "").split("\n")[0].slice(0, 42);
        b.innerHTML = "<span class=\"ht\">" + esc(time) + "</span>" +
          "<span class=\"hc\">" + esc(cwdShort) + "</span>" +
          "<span class=\"hp\">" + esc(first) + "</span>" +
          "<span class=\"hs " + esc(row.status || "") + "\">" + esc(row.status || "") + "</span>";
        b.addEventListener("click", function () { resumeSession(row.id); });
        body.appendChild(b);
      });
    }).catch(function (e) {
      body.innerHTML = "<div class=\"mempty\">加载失败：" + esc(emsg(e)) + "</div>";
    });
  }

  function resumeSession(sid) {
    window.yibao.invoke("coding.history", { id: sid }).then(function (r) {
      if (currentSession) discardedSessions[currentSession] = true;
      currentSession = sid;
      currentBubble = null;
      streaming = false;
      $("log").innerHTML = "";
      (r.messages || []).forEach(function (m) {
        if (m.role === "user") appendUser(m.text);
        else { var b = appendAssistantBubble(); appendTextToBubble(b, m.text); b.classList.add("done"); }
      });
      if ((r.messages || []).length) appendMarker("—— 以上为历史，继续聊 ↓ ——", false);
      if (r.cwd) $("cwd").value = r.cwd;
      $("history-overlay").hidden = true;
      $("new-chat").disabled = false;
      $("send").disabled = false;
      setStatus("已恢复会话 " + sid + "，发消息将在同一上下文继续");
      $("prompt").focus();
    }).catch(function (e) {
      setStatus("恢复失败：" + emsg(e), true);
      $("history-overlay").hidden = true;
    });
  }
```

浮层 DOM（handoff picker 结构同款，style 区补 `.hrow`/`.ht`/`.hc`/`.hp`/`.hs` 样式）：

```html
  <div id="history-overlay" class="overlay" hidden>
    <div class="overlay-card">
      <header>历史会话 · 点击恢复<span class="sp"></span><button id="history-close" class="btn ghost">关闭</button></header>
      <div id="history-body" class="overlay-body"></div>
    </div>
  </div>
```

绑定：`$("history").addEventListener("click", openHistory);` 与 `$("history-close")` 关闭。

- [ ] **Step 4: 跑测试 + 全量回归 + 构建**

Run: `cd sidecar && uv run pytest tests/test_coding_handoff.py -q && uv run pytest -q && cd ../app && npx vite build`
Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add plugins/coding/skills/_cc_reader.py plugins/coding/skills/coding.py plugins/coding/api.toml plugins/coding/panel/chat.html sidecar/tests/test_coding_handoff.py
git commit -m "feat(coding): 会话历史抽屉——列表/读回 transcript/接着聊（_cc_reader + coding.history quiet）"
```

---

### Task 7: 文件夹选择器 + 分工 steer（执行 8/6 现成计划）

**Files:** 照 `docs/superpowers/plans/2026-08-06-coding-folder-picker-division.md` 的 File Structure（FP1-FP3 + DIV1）。

**Interfaces:**
- Consumes: 现成计划全文（含完整代码）；chat.html cwd 药丸（:287）；loop.py SYSTEM_PROMPT（:23-48）
- Produces: Tauri `pick_folder` 命令 + capabilities `dialog:allow-open`；WebviewPanel `native:pick_folder` 旁路；chat.html 📁 按钮；SYSTEM_PROMPT coding 分工段 + 断言测试

- [ ] **Step 1: 读 `docs/superpowers/plans/2026-08-06-coding-folder-picker-division.md` 全文，照 Task FP1 执行**（npm/cargo 装 dialog 插件 → lib.rs 注册 + pick_folder 命令 → capabilities 加 `dialog:allow-open` → `cargo check` exit 0）。注意该计划写于三态重构前，lib.rs 行号已漂移——按内容锚点（generate_handler、plugins builder 链、capabilities 文件名按 `ls` 实际）对齐。
- [ ] **Step 2: commit** `feat(coding): Tauri dialog 插件 + pick_folder 命令`
- [ ] **Step 3: 照 Task FP2 执行**（WebviewPanel.vue 的 onMessage 命名空间校验之前加 `native:` 白名单旁路，`invoke` 自 `@tauri-apps/api/core`）→ `npx vue-tsc --noEmit && npx vite build` exit 0。
- [ ] **Step 4: commit** `feat(panel): WebviewPanel native: 命令旁路（白名单 pick_folder）`
- [ ] **Step 5: 照 Task FP3 执行**（chat.html cwd 药丸加 📁 按钮 `#cwd-pick` + `pickCwd()` → `window.yibao.invoke("native:pick_folder")` 回填；保留手输）→ `npx vite build` exit 0。
- [ ] **Step 6: commit** `feat(coding): cwd 药丸加文件夹选择器（native:pick_folder）`
- [ ] **Step 7: 照 Task DIV1 执行**（loop.py SYSTEM_PROMPT 末尾加 coding 分工段 + 断言测试「编码面板」「dispatch_task」关键词在位）→ `cd sidecar && uv run pytest -q` 全绿。
- [ ] **Step 8: commit** `feat(coding): 系统提示 steer——交互式 coding 引导面板、dispatch 仅后台`

---

### Task 8: 验收

- [ ] **Step 1: 自动化全绿**

```bash
cd sidecar && uv run pytest -q
cd ../app && npx vue-tsc --noEmit && npx vite build && cargo check --manifest-path src-tauri/Cargo.toml
```

- [ ] **Step 2: 真机（人工）验收清单**

1. 起会话（📁 选目录）→ 流式中看到 thinking 淡块、工具卡下「结果」、diff 默认展开 + 统计、状态条秒表
2. 流式中点发送 → 禁用；中断 → stopped 复位、可再发
3. 完成 → 状态行「✓ 完成 · Ns · Nk tok」；接续一条 → 不弹确认直接跑
4. 多轮两条 → 历史抽屉 → 恢复旧会话 → 看到历史 + 接着聊（上下文真接续：问它「刚才让你做什么了」）
5. 对译宝说「帮我写个 X」→ 引导去编码面板（steer 生效）
6. quiet 档/confirm 档抽查无异常

- [ ] **Step 3: 收尾 commit（如有验收修小补）**

```bash
git add -p
git commit -m "fix(coding): 产品化真机验收小修"
```

---

## 自审

- spec 覆盖：①中断（T1）②防重入（T2）③降噪（T3）④透明（T4）⑤diff（T5）⑥历史（T6）⑦选择器+steer（T7）✅
- 类型一致：`stopped`/`thinking`/`tool_result`/`done.usage` kind 跨 runner→coding.py→chat.html 一致 ✅；`coding.history {id}->{messages}`（T6 后端=api.toml=前端 invoke）✅；`native:pick_folder`（T7 WebviewPanel 旁路=chat.html invoke）✅；`read_transcript(cc_session_id, limit)`（_cc_reader=HistorySkill=测试）✅
- 无占位：后端全量代码、面板新函数全量代码、旧代码替换点带行号锚 ✅
