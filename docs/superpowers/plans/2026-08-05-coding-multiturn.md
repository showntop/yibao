# Coding 插件多轮会话 修复计划

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 把 coding 插件从"每条消息 = 全新 CC 会话（零历史）"改成"一个 coding 会话 = 多轮 CC 对话（resume 接续）"，让被驱动的 Claude Code 跨轮记住上下文。

**Architecture:** `claude-agent-sdk` 支持 `ClaudeAgentOptions(resume=<session_id>)` 接续、`ResultMessage.session_id` 返回会话 id。runner.run 加 `resume_session_id` 入参 + 返回 cc_session_id；sessions 表加 `cc_session_id` 列；新增 `coding.send`（resume 接续）；chat.html 发送按"有无活动会话"路由 start(新)/send(接续)。

**Tech Stack:** Python 3.12（sidecar，pytest，claude-agent-sdk），HTML/JS webview，TDD（FakeSDK 注入）。

## Global Constraints

- **行为保持**：现有 `coding.start`（新会话）语义不变；新增 `coding.send`（接续）。不破坏 788+ 现有测试。
- **resume 用 `ClaudeAgentOptions(resume=<cc_session_id>)`**；cc_session_id 从 `ResultMessage.session_id` 捕获（normalize 已把 ResultMessage→done，需在 done 前/时抓 session_id）。
- **挂了不碍事**：resume 失败/SDK 异常 → 记 session 失败 + 面板报错，不抛主链路。
- **TDD**：runner resume 用 FakeSDK（造 ResultMessage 带 session_id）测；不跑真 SDK。webview 以 build + 人工为门。
- **sidecar 测试**：`cd /Users/denny/Work/yibao/sidecar && .venv/bin/python -m pytest tests/test_coding_plugin.py -v`；全量基线 797。
- **commit**：每任务一 commit，中文 scope（`feat(coding): ...`），仅 stage 本任务文件，不动 `.gitignore`；提交到 `main`。

## File Structure

**修改：**
- `plugins/coding/skills/_runner.py` — `run()` 加 `resume_session_id`，返回 cc_session_id。
- `plugins/coding/skills/coding.py` — sessions 加 cc_session_id 存储；新增 `coding.send`（resume）；start 存 cc_session_id。
- `plugins/coding/manifest.toml` — sessions 表加 `cc_session_id` 列。
- `plugins/coding/api.toml` — 加 `coding.send` direct method。
- `plugins/coding/panel/chat.html` — 发送路由（新/接续）+ 多轮保持。
- `sidecar/tests/test_coding_plugin.py` — runner resume 测 + send 测。

---

### Task 1: runner.run 支持 resume + 返回 cc_session_id

**Files:** `plugins/coding/skills/_runner.py`、`sidecar/tests/test_coding_plugin.py`
**Interfaces:**
- Consumes: claude-agent-sdk `ClaudeAgentOptions(resume=...)`、`ResultMessage.session_id`。
- Produces: `async def ClaudeCodeRunner.run(prompt, cwd, *, on_event, cancel_event, resume_session_id=None) -> str | None`（返回 cc_session_id 或 None）。

- [ ] **Step 1: 写失败测试**（追加到 test_coding_plugin.py）
```python
def test_runner_returns_cc_session_id():
    # FakeSDK 的 receive_response 末尾 yield 一个带 .session_id 的 ResultMessage-like
    captured = []
    class _Result:
        subtype = "success"
        session_id = "cc-sess-123"
        def __class_getitem__(cls, *a): return cls
    # 复用既有 _FakeClient，但末条换成 Result；_FakeMsg 加一个 result 通道
    # （按既有 _FakeClient/_FakeMsg 形态适配：让最后一条 msg 是 duck-typed ResultMessage：
    #  type 名含 "Result" 或带 .subtype+.is_error）
    class _ResMsg:
        type = "result"
        subtype = "success"
        is_error = False
        session_id = "cc-sess-123"
    msgs = [_FakeMsg("assistant", text="hi"), _ResMsg()]
    runner = ClaudeCodeRunner(client_factory=lambda cwd, tools: _FakeClient(msgs))
    sid = _run(runner.run("p", "/tmp", on_event=lambda e: captured.append(e),
                          cancel_event=asyncio.Event()))
    assert sid == "cc-sess-123"
    assert any(e["kind"] == "done" for e in captured)

def test_runner_resume_passes_session_id(monkeypatch):
    # 验证 resume_session_id 被透传到 ClaudeAgentOptions.resume
    seen = {}
    def factory(cwd, tools, resume=None):
        seen["resume"] = resume
        class C:
            async def __aenter__(self): return self
            async def __aexit__(self,*a): return False
            async def query(self, p): pass
            async def receive_response(self):
                class R: subtype="success"; is_error=False; session_id="cc-sess-999"
                yield R()
        return C()
    # _default_factory 签名要接 resume；测试用注入 factory 模拟
    runner = ClaudeCodeRunner(client_factory=factory)
    _run(runner.run("p", "/tmp", on_event=lambda e: None,
                    cancel_event=asyncio.Event(), resume_session_id="cc-old-1"))
    assert seen["resume"] == "cc-old-1"
```
（`_FakeClient`/`_FakeMsg` 沿用既有；若 factory 签名需加 resume 参数，同步改默认 factory 调用处。）

- [ ] **Step 2: 跑测试验证失败** — `pytest tests/test_coding_plugin.py -v -k "returns_cc_session_id or resume_passes"` → FAIL（run 不返 session_id / 不接 resume）。
- [ ] **Step 3: 实现**
  - `run(self, prompt, cwd, *, on_event, cancel_event, resume_session_id=None)`。
  - 默认 factory 接 `resume`：`ClaudeAgentOptions(cwd=cwd, permission_mode="acceptEdits", allowed_tools=tools, resume=resume)`（resume=None 时 SDK 视为不 resume）。
  - 在流式循环里，当 normalize 某条 msg 产出 `done`（ResultMessage-like），**先读该 msg 的 `.session_id`** 存入本地 `cc_sid`，再 on_event(done)。run 末尾 `return cc_sid`。
  - 注：normalize 现返回 list；run 循环里需能拿到"产生 done 的那条原 msg"以读 session_id——调整：run 直接对每条原 msg 先尝试 `getattr(msg,"session_id",None)` 缓存，再 normalize(msg) 逐事件 on_event。
- [ ] **Step 4: 跑测试验证通过** — 全 test_coding_plugin.py 通过。
- [ ] **Step 5: 全量回归** — 797 passed。
- [ ] **Step 6: commit** — `feat(coding): runner 支持 resume + 返回 cc_session_id`

---

### Task 2: sessions 表加 cc_session_id 列 + start 存它

**Files:** `plugins/coding/manifest.toml`（sessions 表）、`plugins/coding/skills/coding.py`（start_session + _stream 存 cc_session_id）

- [ ] **Step 1: manifest 加列** — sessions `columns` 加 `{name = "cc_session_id", type = "text", default = ""}`。插件表 schema 由 manifest 声明（loader 建表/迁移；空默认兼容存量行）。
- [ ] **Step 2: _stream 存 cc_session_id** — `_stream` 拿到 run() 返回的 cc_sid → `db.update("sessions", sid, {"cc_session_id": cc_sid or ""})`（在最终状态落库处一并写）。
- [ ] **Step 3: 测试** — 追加：start_session 插入行后 cc_session_id 默认空；_stream mock 跑完（runner 返回 cc_sid）后行 cc_session_id 被更新。（用既有 fake db + monkeypatch runner/spawn。）
- [ ] **Step 4: 全量回归** — 797 passed（+新测）。
- [ ] **Step 5: commit** — `feat(coding): sessions 表存 cc_session_id`

---

### Task 3: coding.send（resume 接续）skill + api

**Files:** `plugins/coding/skills/coding.py`（SendSkill）、`plugins/coding/api.toml`、`sidecar/tests/test_coding_plugin.py`

- [ ] **Step 1: SendSkill** — `id="coding.send"`，params `{id（我们的 session id）, prompt}`；读行 → 取 `cc_session_id` → `_spawn_stream(db, sid, cwd, prompt, runner, emit, resume_session_id=cc_session_id)`（_spawn_stream/_stream 透传 resume_session_id 给 runner.run）。校验：行存在、cc_session_id 非空（否则提示"会话尚未建立，请先发送首条"）。
- [ ] **Step 2: _spawn_stream/_stream 加 resume_session_id 参数** — 透传到 `runner.run(..., resume_session_id=...)`。
- [ ] **Step 3: api.toml** — 加 `[[method]] name="coding.send" handler="coding.send" direct=true panel="coding:chat"`。
- [ ] **Step 4: make_tools** — 返回 `[StartSkill(), SendSkill(), StopSkill(), ListSkill()]`。
- [ ] **Step 5: 测试** — SendSkill：cc_session_id 为空 → 报错；非空 → 以 resume 调 spawn（monkeypatch _spawn_stream 断言收到 resume_session_id）。
- [ ] **Step 6: 全量回归** — 通过。
- [ ] **Step 7: commit** — `feat(coding): coding.send resume 接续多轮`

---

### Task 4: chat.html 发送路由（新/接续）+ 多轮保持

**Files:** `plugins/coding/panel/chat.html`

- [ ] **Step 1: 发送逻辑** — `send()`：若 `currentSession` 为空 → `coding.start({cwd, prompt})`（新会话，回填 currentSession）；否则 → `coding.send({id: currentSession, prompt})`（接续）。两路径都：appendUser → invoke → currentBubble=null → 流式 onInit 累加。
- [ ] **Step 2: 会话保持** — done/error 不清空 currentSession（会话可继续）；加「新对话」按钮 → 清 currentSession + 清 #log（开新 CC 会话）。
- [ ] **Step 3: 中断** — stop 仍调 coding.stop（取消当前 turn；currentSession 保留，可继续 send）。
- [ ] **Step 4: build** — `cd app && npx vite build` exit 0。
- [ ] **Step 5: commit** — `feat(coding): 面板多轮——首条 start / 后续 send + 新对话`

---

### Task 5: 验收（真多轮）

**Files:** 无（验证）
- [ ] **Step 1: 自动化** — sidecar 全量 + vite build 全绿。
- [ ] **Step 2: 真多轮（人工，留用户）** — `npm run tauri dev` → 编码面板：发"写个贪吃蛇"→ CC 问技术 → 回"HTML"→ CC **带上下文继续**（不再当孤字）→ 多轮往返直到完成。
- [ ] **Step 3: spec/记录回写** — 把多轮修复记入 coding spec 末尾。

---

## 自审（vs 诊断的根因）
- 根因"每条=新 session 零历史" → runner resume（T1）+ cc_session_id 存储（T2）+ send 接续（T3）+ 面板路由（T4）✅
- 行为保持：start 不变、现有测试不破 ✅
- 类型一致：`resume_session_id` 贯穿 runner/_spawn_stream/_stream/SendSkill；`cc_session_id` 列名一致 ✅
