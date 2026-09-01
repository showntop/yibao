"""coding 插件：runner 流式/取消/容错（FakeSDK 注入，不跑真 SDK）。"""
from __future__ import annotations
import asyncio, os, sys, time
from types import SimpleNamespace
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# 插件 tools 不在 src 下，单独加路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugins", "coding", "tools"))
from _runner import ClaudeCodeRunner, normalize  # noqa: E402


# ---------- 真 SDK 形态的 lightweight fakes ----------
# 镜像 claude_agent_sdk 0.2.x：AssistantMessage.content 是 ContentBlock 列表，
# 块为 TextBlock(.text) 或 ToolUseBlock(.name+.input)；ResultMessage(.subtype+.is_error)。
# 用简单类而非真 dataclass，避免耦合 SDK 构造器的必填字段；normalize 全程 duck-typed。
class _FakeText:
    def __init__(self, text): self.text = text
class _FakeTool:
    def __init__(self, name, inp): self.name = name; self.input = inp
class _FakeAssistant:
    def __init__(self, blocks): self.content = list(blocks)
class _FakeResultMessage:
    def __init__(self, subtype="success"): self.subtype = subtype; self.is_error = False
class _FakeSystemMessage:
    def __init__(self): self.subtype = "init"; self.data = {}
class _FakeUserMessage:
    def __init__(self, text): self.content = text  # 字符串形态


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


# ---------- runner：流式 / 取消 / 容错 ----------
def test_runner_streams_and_done():
    events = []
    msgs = [_FakeAssistant([_FakeText("hello")]), _FakeResultMessage("success")]
    runner = ClaudeCodeRunner(client_factory=lambda cwd, tools, resume=None, **k: _FakeClient(msgs))
    cancel = asyncio.Event()
    _run(runner.run("do X", "/tmp", on_event=events.append, cancel_event=cancel))
    kinds = [e["kind"] for e in events]
    assert kinds == ["text_delta", "done"]


def test_runner_cancel_mid_stream():
    sent = []
    msgs = [
        _FakeAssistant([_FakeText("a")]),
        _FakeAssistant([_FakeText("b")]),
        _FakeAssistant([_FakeText("c")]),
        _FakeResultMessage("success"),
    ]
    runner = ClaudeCodeRunner(client_factory=lambda cwd, tools, resume=None, **k: _FakeClient(msgs))
    cancel = asyncio.Event()
    def on_event(e):
        sent.append(e)
        if len(sent) == 2:
            cancel.set()
    _run(runner.run("p", "/tmp", on_event=on_event, cancel_event=cancel))
    kinds = [e["kind"] for e in sent]
    assert "done" not in kinds                                  # cancel 抑制终态 done
    assert kinds == ["text_delta", "text_delta", "stopped"]     # 前两条入列 + 取消终态
    assert len(sent) == 3                              # 第 3 条取消后丢弃


def test_runner_error_isolated():
    events = []
    def factory(cwd, tools, resume=None, **k):
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


class _SetEvent:
    """is_set() 恒 True 的 cancel_event 替身（asyncio.Event 极简仿）。"""
    def is_set(self): return True
    def set(self): pass


def _cancel_immediately_factory():
    """fake client factory：首条消息前取消即触发（配合 _SetEvent 恒 set）。"""
    msgs = [_FakeAssistant([_FakeText("a")]), _FakeResultMessage("success")]
    return lambda cwd, tools, resume=None, **k: _FakeClient(msgs)


def test_runner_cancel_emits_stopped_terminal_event():
    """取消路径必须发 stopped 终态事件（此前静默早退 → 面板永远卡在「运行中」）。"""
    events = []
    runner = ClaudeCodeRunner(client_factory=_cancel_immediately_factory())
    _run(runner.run("p", "/tmp", on_event=events.append, cancel_event=_SetEvent()))
    assert any(ev.get("kind") == "stopped" for ev in events), events
    assert not any(ev.get("kind") == "done" for ev in events), events


# ---------- runner：resume + cc_session_id 捕获 ----------
class _FakeResultWithSession:
    """终态 ResultMessage-like：带 .session_id（duck-typed normalize→done）。"""
    def __init__(self, subtype="success", session_id="cc-sess-x"):
        self.subtype = subtype; self.is_error = False; self.session_id = session_id


def test_runner_returns_cc_session_id():
    captured = []
    msgs = [_FakeAssistant([_FakeText("hi")]), _FakeResultWithSession(session_id="cc-sess-123")]
    runner = ClaudeCodeRunner(client_factory=lambda cwd, tools, resume=None, **k: _FakeClient(msgs))
    sid = _run(runner.run("p", "/tmp", on_event=captured.append, cancel_event=asyncio.Event()))
    assert sid == "cc-sess-123"
    assert any(e["kind"] == "done" for e in captured)


def test_runner_resume_passes_session_id():
    seen = {}
    def factory(cwd, tools, resume=None, **k):
        seen["resume"] = resume
        class C:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def query(self, p): pass
            async def receive_response(self):
                class R:
                    subtype = "success"; is_error = False; session_id = "cc-sess-999"
                yield R()
        return C()
    runner = ClaudeCodeRunner(client_factory=factory)
    sid = _run(runner.run("p", "/tmp", on_event=lambda e: None,
                          cancel_event=asyncio.Event(), resume_session_id="cc-old-1"))
    assert seen["resume"] == "cc-old-1"
    assert sid == "cc-sess-999"


# ---------- normalize：真 SDK 块形态 ----------
def test_normalize_text_block():
    assert normalize(_FakeAssistant([_FakeText("hi")])) == [
        {"kind": "text_delta", "text": "hi"}
    ]


def test_normalize_multi_block_one_message():
    msg = _FakeAssistant([
        _FakeText("thinking..."),
        _FakeTool("Read", {"file_path": "x.py"}),
        _FakeText("done."),
    ])
    out = normalize(msg)
    assert [e["kind"] for e in out] == ["text_delta", "tool_use", "text_delta"]
    assert out[1]["tool"] == "Read" and out[1]["input"] == {"file_path": "x.py"}


def test_normalize_write_file_edit():
    out = normalize(_FakeAssistant([_FakeTool("Write", {"file_path": "a.py", "content": "x=1"})]))
    assert out == [{
        "kind": "file_edit", "tool": "Write", "path": "a.py",
        "old": None, "new": "x=1",
    }]


def test_normalize_edit_file_edit():
    out = normalize(_FakeAssistant([_FakeTool("Edit", {
        "file_path": "a.py", "old_string": "a", "new_string": "b"})]))
    fe = out[0]
    assert fe["kind"] == "file_edit" and fe["tool"] == "Edit" and fe["path"] == "a.py"
    assert fe["old"] == "a" and fe["new"] == "b"


def test_normalize_multiedit_file_edit():
    out = normalize(_FakeAssistant([_FakeTool("MultiEdit", {
        "file_path": "a.py", "edits": [{"old": "x", "new": "y"}]})]))
    fe = out[0]
    assert fe["kind"] == "file_edit" and fe["tool"] == "MultiEdit"
    assert fe["old"] is None and '"old": "x"' in fe["new"]


def test_normalize_bash_tool_use():
    out = normalize(_FakeAssistant([_FakeTool("Bash", {"command": "ls -la"})]))
    assert out == [{"kind": "tool_use", "tool": "Bash", "input": {"command": "ls -la"}}]


def test_normalize_result_done():
    assert normalize(_FakeResultMessage("success")) == [{"kind": "done", "usage": {}}]


def test_normalize_ignores_system_message():
    assert normalize(_FakeSystemMessage()) == []


def test_normalize_str_user_message_emits_user_msg():
    """字符串 content 的 UserMessage（replay-user-messages 回流）→ user_msg 事件；无 uuid 降级空串。"""
    assert normalize(_FakeUserMessage("hi")) == [{"kind": "user_msg", "uuid": "", "text": "hi"}]


def test_normalize_none():
    assert normalize(None) == []


def test_normalize_real_sdk_dataclasses():
    """对真 claude_agent_sdk 数据类验一遍（锁定 type-name 检测对真类生效）。"""
    try:
        from claude_agent_sdk import AssistantMessage, TextBlock, ToolUseBlock, ResultMessage
    except Exception:  # pragma: no cover - SDK 应在依赖里；缺失则跳过
        import pytest
        pytest.skip("claude_agent_sdk 不可导入")
    msg = AssistantMessage(
        content=[TextBlock(text="hi"), ToolUseBlock(id="t1", name="Bash", input={"command": "pwd"})],
        model="claude",
    )
    out = normalize(msg)
    assert [e["kind"] for e in out] == ["text_delta", "tool_use"]
    assert out[1]["tool"] == "Bash" and out[1]["input"] == {"command": "pwd"}
    done = normalize(ResultMessage(
        subtype="success", duration_ms=0, duration_api_ms=0,
        is_error=False, num_turns=1, session_id="s"))
    assert done == [{"kind": "done", "usage": {"duration_ms": 0}}]


# ---------- Task 5: coding skills（start/stop 纯函数 + race-safe 取消顺序）----------
import coding as codingmod  # noqa: E402
from coding import _stop_session  # noqa: E402


class _FakeDB:
    """极小鸭式 db：记录 insert/update；sessions 存 rows（按 id），其余表存 _tables。
    query 支持 where 等值过滤 + order（"col [DESC]"）+ limit（仿 PluginDb 形态）。"""
    def __init__(self): self.rows = {}; self.updates = []; self._tables = {}
    def insert(self, table, row):
        row = dict(row)
        if table == "sessions":
            self.rows[row["id"]] = row; return row["id"]
        row.setdefault("id", f"{table}-{len(self._tables.get(table, [])) + 1}")
        self._tables.setdefault(table, []).append(row)
        return row["id"]
    def update(self, table, rid, fields): self.updates.append((rid, fields)); self.rows.setdefault(rid, {}).update(fields)
    def query(self, table, where=None, order=None, limit=None, **_):
        out = list(self.rows.values()) if table == "sessions" else list(self._tables.get(table, []))
        if where:
            out = [r for r in out if all(r.get(k) == v for k, v in where.items())]
        if order:
            parts = order.split()
            out = sorted(out, key=lambda r: r.get(parts[0]) or 0,
                         reverse=len(parts) > 1 and parts[1].upper() == "DESC")
        return out[:limit] if limit else out


def test_start_inserts_running_session(monkeypatch):
    db = _FakeDB()
    # 不真起线程：把 _spawn_stream 占位成空
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


# ---------- Task 2: _stream 存 cc_session_id ----------
import threading as _threading  # noqa: E402
from coding import _stream  # noqa: E402


class _FakeRunner:
    """鸭式 runner：run() 返回预设 cc_sid；记录传入参数。"""
    def __init__(self, cc_sid): self._cc_sid = cc_sid; self.called_with = None
    async def run(self, prompt, cwd, *, on_event, cancel_event, resume_session_id=None,
                  permission_mode="acceptEdits", can_use_tool=None, session_entry=None):
        self.called_with = {"prompt": prompt, "cwd": cwd, "resume": resume_session_id,
                            "permission_mode": permission_mode, "can_use_tool": can_use_tool,
                            "session_entry": session_entry}
        if self._cc_sid is not None:
            on_event({"kind": "done"})
        return self._cc_sid


def test_stream_stores_cc_session_id_on_done():
    """runner 返回 cc_sid 时，_stream 最终 db.update 含 cc_session_id=cc_sid。"""
    db = _FakeDB(); db.rows["s1"] = {"id": "s1", "status": "running"}
    runner = _FakeRunner(cc_sid="cc-sess-abc")
    cancel = _threading.Event()
    _run(_stream(db, "s1", "/tmp/p", "hi", runner, emit_event=None, cancel=cancel))
    # 末次 update 写入 final 状态 + cc_session_id
    last = db.updates[-1][1]
    assert last["status"] == "done"
    assert last["cc_session_id"] == "cc-sess-abc"


def test_stream_preserves_cc_session_id_when_runner_returns_none():
    """runner 返回 None（取消/失败）时，不更新 cc_session_id 列——老行既有 thread_id/session_id
    不被抹成 ""（codex 静默失败后后续 send 仍能 resume；CC 拿不到 session_id 也原样不写）。"""
    db = _FakeDB(); db.rows["s2"] = {"id": "s2", "status": "running", "cc_session_id": "cc-old-9"}
    runner = _FakeRunner(cc_sid=None)
    cancel = _threading.Event()
    _run(_stream(db, "s2", "/tmp/p", "hi", runner, emit_event=None, cancel=cancel))
    last = db.updates[-1][1]
    assert "cc_session_id" not in last                        # 终态 update 不触该列
    assert db.rows["s2"]["cc_session_id"] == "cc-old-9"       # 老值保留


def test_stream_preserves_stopped_and_still_records_cc_session_id():
    """race-safe：用户先停（status=stopped）→ _stream 保留 stopped，但仍记 cc_session_id。"""
    db = _FakeDB(); db.rows["s3"] = {"id": "s3", "status": "stopped"}
    runner = _FakeRunner(cc_sid="cc-sess-stop")
    cancel = _threading.Event(); cancel.set()   # 模拟 stop 已 set
    _run(_stream(db, "s3", "/tmp/p", "hi", runner, emit_event=None, cancel=cancel))
    last = db.updates[-1][1]
    assert last["status"] == "stopped"                 # 不被覆盖
    assert last["cc_session_id"] == "cc-sess-stop"     # 仍记录


# ---------- Task 3: coding.send（resume 接续）----------
from coding import SendTool, StartTool  # noqa: E402


class _Ctx:
    """最小 ctx 鸭式：db + emit_event（SendTool.run 只用这俩）。"""
    def __init__(self, db): self.db = db; self.emit_event = lambda *a, **k: None


def test_send_skill_resumes_with_cc_session_id(monkeypatch):
    """cc_session_id 非空 → _spawn_stream 收到 resume_session_id=cc。"""
    db = _FakeDB()
    db.rows["s1"] = {"id": "s1", "cwd": "/tmp/p", "cc_session_id": "cc-old-1"}
    captured = {}
    monkeypatch.setattr(
        codingmod, "_spawn_stream",
        lambda *a, **k: captured.update({"args": a, "kwargs": k}))
    res = SendTool().run({"id": "s1", "prompt": "再来一轮"}, _Ctx(db))
    assert res.success is True
    assert res.data["session_id"] == "s1"
    # resume 透传到 _spawn_stream
    assert captured["kwargs"].get("resume_session_id") == "cc-old-1"
    # spawn 前已重置 running（finished_at=0）
    reset = next((u for u in db.updates if u[0] == "s1" and u[1].get("status") == "running"), None)
    assert reset is not None and reset[1].get("finished_at") == 0


def test_send_skill_missing_row_errors(monkeypatch):
    """会话不存在 → success=False，不调 _spawn_stream。"""
    db = _FakeDB()
    called = {"n": 0}
    monkeypatch.setattr(codingmod, "_spawn_stream",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    res = SendTool().run({"id": "ghost", "prompt": "x"}, _Ctx(db))
    assert res.success is False and "不存在" in res.error
    assert called["n"] == 0


def test_send_skill_empty_cc_session_id_rebuilds_context(monkeypatch):
    """cc_session_id 为空（首条未建立上下文，如 codex 交接首轮失败）→ 不再拒绝：
    自动以交接摘要续跑（_spawn_stream resume_session_id=None 重开新会话），success 受理，
    prompt 带【交接上下文】前缀；llm 缺失时摘要退化为原文节选（不炸）。"""
    db = _FakeDB()
    db.rows["s2"] = {"id": "s2", "cwd": "/tmp/p", "cc_session_id": "", "agent": "codex"}
    captured = {}
    monkeypatch.setattr(codingmod, "_spawn_stream",
                        lambda *a, **k: captured.update({"args": a, "kwargs": k}))
    res = SendTool().run({"id": "s2", "prompt": "继续"}, _Ctx(db))
    assert res.success is True
    assert res.data["session_id"] == "s2"
    assert captured["kwargs"].get("resume_session_id") is None   # 降级走新会话而非 resume
    assert "【交接上下文】" in captured["args"][3] and "【用户继续】" in captured["args"][3]
    assert "继续" in captured["args"][3]
    # 落 running 待重跑（finished_at 归零，同正常 resume 路径）
    reset = next((u for u in db.updates if u[0] == "s2" and u[1].get("status") == "running"), None)
    assert reset is not None and reset[1].get("finished_at") == 0


def test_send_skill_empty_cc_rebuild_uses_llm_brief(monkeypatch):
    """cc_session_id 为空且 llm 可用 → 摘要经 _build_brief（src/dst=会话引擎名）进续跑 prompt。"""
    db = _FakeDB()
    db.rows["s2"] = {"id": "s2", "cwd": "/tmp/p", "cc_session_id": "", "agent": "codex"}
    db.insert("messages", {"session_id": "s2", "role": "user", "text": "老任务",
                           "ts": 1, "seq": 1, "uuid": ""})
    captured = {}
    monkeypatch.setattr(codingmod, "_spawn_stream",
                        lambda *a, **k: captured.update({"args": a, "kwargs": k}))
    monkeypatch.setattr(codingmod._sess, "_build_brief",
                        lambda llm, turns, git, src, dst: f"摘要:{src}->{dst}")
    monkeypatch.setattr(codingmod._codex, "git_summary", lambda cwd: "")

    class _CtxLLM(_Ctx):
        llm = object()

    res = SendTool().run({"id": "s2", "prompt": "继续"}, _CtxLLM(db))
    assert res.success is True
    assert "摘要:Codex->Codex" in captured["args"][3]
    assert captured["kwargs"].get("resume_session_id") is None


def test_send_skill_missing_prompt_errors(monkeypatch):
    """缺少 prompt → 早退报错，不查库、不调 _spawn_stream。"""
    db = _FakeDB()
    called = {"n": 0}
    monkeypatch.setattr(codingmod, "_spawn_stream",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    res = SendTool().run({"id": "s1"}, _Ctx(db))   # 无 prompt
    assert res.success is False and "prompt" in res.error
    assert called["n"] == 0


def test_send_skill_openai_schema_shape():
    schema = SendTool().openai_schema()
    assert schema["function"]["name"] == "coding.send"
    props = schema["function"]["parameters"]["properties"]
    assert set(props.keys()) == {"id", "prompt"}
    assert schema["function"]["parameters"]["required"] == ["id", "prompt"]


def test_make_tools_includes_send():
    """make_tools 返回 Start/Send/Stop/List/Attach + HandoffList/HandoffBrief/History/Mode/Rewind/Decide/PermPending/Files
    + LastSessions/AttachCc + Drivers/AttachCodex + SessionBrief + Studio 十九件。"""
    tools = codingmod.make_tools(type("C", (), {"db": None, "emit_event": None})())
    ids = [t.id for t in tools]
    assert "coding.send" in ids
    assert ids == ["coding.start", "coding.send", "coding.stop", "coding.list", "coding.attach",
                   "coding.handoff_list", "coding.handoff_brief", "coding.history",
                   "coding.mode", "coding.rewind", "coding.decide", "coding.perm_pending", "coding.files",
                   "coding.last_sessions", "coding.attach_cc",
                   "coding.drivers", "coding.attach_codex", "coding.session_brief",
                   "coding.studio"]


def test_start_skill_does_not_pass_resume(monkeypatch):
    """回归保护：StartTool 调 _spawn_stream 不传 resume_session_id（fresh）。"""
    db = _FakeDB()
    captured = {}
    monkeypatch.setattr(codingmod, "_spawn_stream",
                        lambda *a, **k: captured.update({"kwargs": k}))
    monkeypatch.setattr(codingmod, "ClaudeCodeRunner", lambda: object())
    StartTool().run({"cwd": "/tmp", "prompt": "p"}, _Ctx(db))
    assert captured["kwargs"].get("resume_session_id") is None


# ---------- 运行中 steer（spec §A）：send 对 running 会话排队而非拒绝 ----------


def _make_ctx_with_session(status, with_messages=None):
    """仿 _Ctx/_FakeDB 既有约定：造一个带指定 status 会话（sid-1）的 ctx。
    with_messages：可选 [(role, text), ...] 预置 messages 表 transcript（history 测试用）。"""
    db = _FakeDB()
    db.rows["sid-1"] = {"id": "sid-1", "cwd": "/tmp/p",
                        "cc_session_id": "cc-old-1", "status": status}
    for i, (role, text) in enumerate(with_messages or []):
        db.insert("messages", {"session_id": "sid-1", "role": role, "text": text,
                               "ts": 0, "seq": i + 1, "uuid": ""})
    return _Ctx(db)


def test_send_running_session_queues_steer_instead_of_rejecting(monkeypatch):
    """spec §A：running + live entry → 不再硬拒，prompt 入 steer 队列（返回 queued/position），
    不另起 _spawn_stream；入队发 marker（面板流 + messages 表双写）。
    无活体 entry 的陈旧 running（对账前的缝）与终态会话照常走 resume 放行。"""
    spawned = {"n": 0}
    monkeypatch.setattr(codingmod, "_spawn_stream",
                        lambda *a, **k: spawned.__setitem__("n", spawned["n"] + 1))
    ctx = _make_ctx_with_session(status="running")
    entry = {"cancel": _threading.Event()}
    monkeypatch.setattr(codingmod._sess, "_SESSIONS", {"sid-1": entry})
    events = []
    ctx.emit_event = events.append
    r = SendTool().run({"id": "sid-1", "prompt": "先补一句"}, ctx)
    assert r.success and r.data["queued"] is True and r.data["position"] == 1
    r2 = SendTool().run({"id": "sid-1", "prompt": "再补一句"}, ctx)
    assert r2.data["position"] == 2
    assert entry["steer"] == ["先补一句", "再补一句"]       # 按序排队
    assert spawned["n"] == 0                                # 不当轮打断、不另起 runner
    marks = [e for e in events if e.get("kind") == "panel_data"
             and e["payload"]["data"]["event"].get("kind") == "marker"]
    assert len(marks) == 2 and "已排队" in marks[0]["payload"]["data"]["event"]["text"]
    assert marks[0]["payload"]["data"]["session_id"] == "sid-1"
    db_marks = [m for m in ctx.db._tables.get("messages", []) if m["role"] == "marker"]
    assert len(db_marks) == 2 and "已排队" in db_marks[0]["text"]
    # 无活体 entry 的陈旧 running（如底座重启 mid-run、对账前的缝）→ 直送 resume 放行
    monkeypatch.setattr(codingmod._sess, "_SESSIONS", {})
    r3 = SendTool().run({"id": "sid-1", "prompt": "续跑"}, ctx)
    assert r3.success and spawned["n"] == 1
    # 终态会话放行
    ctx2 = _make_ctx_with_session(status="done")
    r4 = SendTool().run({"id": "sid-1", "prompt": "再来一条"}, ctx2)
    assert r4.success, r4.error


# ---------- start/send 风险降噪：L2 → L1 ----------
from yibao_brain.ipc import RiskLevel  # noqa: E402


def test_start_send_are_l1_no_confirm():
    """会话启动/续聊 = L1（直调不弹确认）：文件改动已由 SDK permission_mode=acceptEdits 管理，
    高频对话循环不该每次弹风险确认。"""
    assert StartTool.default_risk == RiskLevel.L1_LOW
    assert SendTool.default_risk == RiskLevel.L1_LOW


# ---------- Task 4: 透明渲染（thinking / tool_result / done.usage）----------
def test_normalize_thinking_block():
    block = SimpleNamespace(type="thinking", thinking="先看一下结构再改" * 1)
    msg = SimpleNamespace(content=[block])
    evs = normalize(msg)
    assert evs == [{"kind": "thinking", "text": "先看一下结构再改"}]


def test_normalize_tool_result_from_user_message():
    # UserMessage-like：类名含 User（SimpleNamespace 为 immutable 内建类型，
    # 不能改 __name__，用同名本地类控制类名）
    class UserMessage:
        def __init__(self, blocks): self.content = list(blocks)
    block = SimpleNamespace(content="file contents here", is_error=False)
    msg = UserMessage([block])
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


# ---------- 终审修复：stop 无 live runner 补发终态 / send 查 live entry ----------
from coding import StopTool  # noqa: E402


class _EmitCtx:
    """带事件记录的 ctx 鸭式：db + emit_event（记录到 .events）。"""
    def __init__(self, db):
        self.db = db
        self.events = []
    def emit_event(self, e):
        self.events.append(e)


def test_stop_stale_running_emits_stopped_terminal(monkeypatch):
    """陈旧 running（live registry 空，如底座重启 mid-run）：stop 仍成功，
    且补发 panel_data/stopped 终态让面板复位（否则发送键永久锁死）。"""
    db = _FakeDB()
    db.rows["s-stale"] = {"id": "s-stale", "status": "running"}
    monkeypatch.setattr(codingmod._sess, "_SESSIONS", {})   # 无 live runner
    ctx = _EmitCtx(db)
    res = StopTool().run({"id": "s-stale"}, ctx)
    assert res.success is True
    assert db.rows["s-stale"]["status"] == "stopped"          # db 已落 stopped
    term = [e for e in ctx.events
            if e.get("kind") == "panel_data"
            and e["payload"]["data"]["event"].get("kind") == "stopped"]
    assert term, ctx.events
    assert term[0]["payload"]["panel"] == "coding:studio"
    assert term[0]["payload"]["data"]["session_id"] == "s-stale"


def test_stop_with_live_runner_no_extra_terminal(monkeypatch):
    """有 live runner：_stop_session 走正常 cancel（runner 取消路径自发 stopped），
    StopTool 不补发——否则面板「已中断」marker 翻倍。"""
    db = _FakeDB()
    db.rows["s-live"] = {"id": "s-live", "status": "running"}
    cancel = _threading.Event()
    monkeypatch.setattr(codingmod._sess, "_SESSIONS", {"s-live": {"cancel": cancel}})
    ctx = _EmitCtx(db)
    res = StopTool().run({"id": "s-live"}, ctx)
    assert res.success is True and cancel.is_set()
    # 不重复补发终态 panel_data（live runner 的终态由流式线程收尾发，stop 不再补）
    assert [e for e in ctx.events if e.get("kind") == "panel_data"] == []


def test_send_rejects_when_runner_finishing(monkeypatch):
    """check-then-act 缝：db=stopped 但 _SESSIONS 仍有 live entry（runner 卡长工具）→ 拒；
    entry 退清后放行。"""
    called = {"n": 0}
    monkeypatch.setattr(codingmod, "_spawn_stream",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    ctx = _make_ctx_with_session(status="stopped")   # sid-1：db 已 terminal
    monkeypatch.setattr(codingmod._sess, "_SESSIONS", {"sid-1": {"cancel": _threading.Event()}})
    r = SendTool().run({"id": "sid-1", "prompt": "再来一条"}, ctx)
    assert not r.success and "收尾" in (r.error or "")
    assert called["n"] == 0
    monkeypatch.setattr(codingmod._sess, "_SESSIONS", {})   # runner 线程退清 → 放行
    r2 = SendTool().run({"id": "sid-1", "prompt": "再来一条"}, ctx)
    assert r2.success, r2.error


# ---------- R2 Task 1: checkpointing + replay + interrupt + mode 参数 + user_msg ----------
def test_factory_passes_checkpointing_and_replay_args():
    """生产 options：checkpointing 开 + replay-user-messages（rewind 原料）；mode 可参数化。"""
    from claude_agent_sdk import ClaudeAgentOptions
    seen = {}
    def factory(cwd, tools, resume=None, permission_mode="acceptEdits", can_use_tool=None):
        seen["mode"] = permission_mode
        opts = ClaudeAgentOptions(
            cwd=cwd, permission_mode=permission_mode, allowed_tools=tools, resume=resume,
            enable_file_checkpointing=True, extra_args={"replay-user-messages": None},
            can_use_tool=can_use_tool,
        )
        seen["opts"] = opts
        return _FakeClient(opts)
    runner = ClaudeCodeRunner(client_factory=factory)
    _run(runner.run("p", "/tmp", on_event=lambda e: None, cancel_event=asyncio.Event(),
                    permission_mode="plan"))
    assert seen["mode"] == "plan"
    assert seen["opts"].enable_file_checkpointing is True
    assert seen["opts"].extra_args == {"replay-user-messages": None}


def test_user_message_yields_user_msg_event_with_uuid():
    """replay 开启后 UserMessage（无 tool_result 块）→ user_msg 事件带 uuid；无 uuid 降级空串。"""
    # SimpleNamespace 不能改 __class__（C 类型），用本地类控制类名进 User 分支
    class UserMessage:
        def __init__(self, content, uuid=None):
            self.content = content
            if uuid is not None:
                self.uuid = uuid
    evs = normalize(UserMessage("帮我改一下登录页", uuid="u-1"))
    assert evs == [{"kind": "user_msg", "uuid": "u-1", "text": "帮我改一下登录页"}]
    assert normalize(UserMessage("没 uuid 的老消息")) == [
        {"kind": "user_msg", "uuid": "", "text": "没 uuid 的老消息"}]


def test_cancel_calls_client_interrupt_before_stopped():
    """取消时先 client.interrupt() 杀后台工具，再发 stopped 终态。"""
    calls = []
    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def query(self, p): pass
        async def receive_response(self):
            yield SimpleNamespace(content=[])
        async def interrupt(self): calls.append("interrupt")
    runner = ClaudeCodeRunner(client_factory=lambda *a, **k: FakeClient())
    events = []
    _run(runner.run("p", "/tmp", on_event=events.append, cancel_event=_SetEvent()))
    assert calls == ["interrupt"]
    assert events and events[-1]["kind"] == "stopped"


# ---------- R2 Task 2: transcript 落库（messages 表 + history 读库优先）----------
from coding import HistoryTool  # noqa: E402


class _EventsRunner:
    """鸭式 runner：依次回放预置事件后返回 cc-sess-x（transcript 落库测试用）。"""
    def __init__(self, events): self._events = events
    async def run(self, prompt, cwd, *, on_event, cancel_event, resume_session_id=None, **_):
        for ev in self._events:
            on_event(ev)
        return "cc-sess-x"


def _run_fake_stream(ctx, events):
    """跑一轮 _stream：_EventsRunner 回放 events（不起线程，直跑 coroutine）。"""
    _run(_stream(ctx.db, "sid-1", "/tmp/p", "p", _EventsRunner(events),
                 emit_event=None, cancel=_threading.Event()))


def test_stream_persists_transcript_to_messages_table():
    """流式：user prompt + assistant 块 + 终态 marker 都落 messages 表（seq 单调）。"""
    ctx = _make_ctx_with_session(status="running")
    _run_fake_stream(ctx, events=[
        {"kind": "user_msg", "uuid": "u-1", "text": "任务一"},
        {"kind": "text_delta", "text": "好的"},
        {"kind": "done", "usage": {}},
    ])
    rows = sorted(ctx.db.query("messages", where={"session_id": "sid-1"}), key=lambda r: r["seq"])
    assert [(r["role"], r["text"]) for r in rows] == [
        ("user", "任务一"), ("assistant", "好的"), ("marker", "完成"),
    ]
    assert rows[0]["uuid"] == "u-1" and rows[1]["uuid"] == ""


def test_history_prefers_db_transcript_over_cc_reader(monkeypatch):
    """库里有 → 直接返回（不读 jsonl）；库里空 → fallback _cc_reader。"""
    ctx = _make_ctx_with_session(status="done", with_messages=[("user", "旧任务")])
    # _sibling 把 _cc_reader 缓存成模块对象（sys.modules 别名），patch 其函数属性
    monkeypatch.setattr(
        codingmod._sibling("_cc_reader"), "read_transcript",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该读 jsonl")))
    r = HistoryTool().run({"id": "sid-1"}, ctx)
    assert r.success and r.data["messages"] == [{"role": "user", "text": "旧任务", "uuid": ""}]


# ---------- T2 评审修复：history 取最近 40 条 / seq 跨轮续号 ----------
def test_history_returns_latest_40_in_ascending_order():
    """45 条消息（seq 1..45）→ history 返回 seq 6..45 正序，而不是头部 seq 1..40。"""
    ctx = _make_ctx_with_session(
        status="done",
        with_messages=[("assistant", f"m{i}") for i in range(1, 46)])
    r = HistoryTool().run({"id": "sid-1"}, ctx)
    assert r.success
    texts = [m["text"] for m in r.data["messages"]]
    assert len(texts) == 40
    assert texts[0] == "m6" and texts[-1] == "m45"     # 尾部 40 条
    assert "m5" not in texts and "m1" not in texts     # 不是头部 40 条
    assert texts == sorted(texts, key=lambda t: int(t[1:]))  # 时间正序


def test_stream_seq_continues_across_rounds():
    """同一 sid 两轮 _stream：第二轮首条消息 seq = 第一轮 max + 1（不交错）。"""
    ctx = _make_ctx_with_session(status="running")
    _run_fake_stream(ctx, events=[
        {"kind": "user_msg", "uuid": "u-1", "text": "第一轮"},
        {"kind": "text_delta", "text": "好"},
        {"kind": "done", "usage": {}},
    ])
    r1_max = max(r["seq"] for r in ctx.db.query("messages", where={"session_id": "sid-1"}))
    ctx.db.rows["sid-1"]["status"] = "running"          # 第一轮落 done 后 send 重置回 running
    _run_fake_stream(ctx, events=[
        {"kind": "user_msg", "uuid": "u-2", "text": "第二轮"},
        {"kind": "done", "usage": {}},
    ])
    rows = sorted(ctx.db.query("messages", where={"session_id": "sid-1"}),
                  key=lambda r: r["seq"])
    seqs = [r["seq"] for r in rows]
    assert seqs == list(range(1, len(rows) + 1))        # 全局单调不重复
    r2_first = next(r for r in rows if r["text"] == "第二轮")
    assert r2_first["seq"] == r1_max + 1                # 续号，不是从 1 重计


# ---------- R2 Task 3: plan mode 切换（db mode + 透传 + 运行中 set_permission_mode）----------
from coding import ModeTool  # noqa: E402


def test_start_and_send_persist_mode(monkeypatch):
    """start 落 mode 列并透传 permission_mode；send 不带 mode 沿用库值，带则覆盖回写。"""
    db = _FakeDB()
    captured = []
    # 不真起线程：_spawn_stream 占位，只记录 kwargs
    monkeypatch.setattr(codingmod, "_spawn_stream", lambda *a, **k: captured.append(k))
    monkeypatch.setattr(codingmod, "ClaudeCodeRunner", lambda: object())
    r = StartTool().run({"cwd": "/tmp", "prompt": "x", "mode": "plan"}, _Ctx(db))
    assert r.success, r.error
    sid = r.data["session_id"]
    assert db.rows[sid]["mode"] == "plan"                     # mode 落列
    assert captured[-1].get("permission_mode") == "plan"      # 透传到流式
    # send 不带 mode → 沿用库里的 plan
    db.rows[sid]["status"] = "done"
    db.rows[sid]["cc_session_id"] = "cc-1"
    r2 = SendTool().run({"id": sid, "prompt": "再来"}, _Ctx(db))
    assert r2.success, r2.error
    assert captured[-1].get("permission_mode") == "plan"
    assert db.rows[sid]["mode"] == "plan"                     # 库值不被重置
    # send 带 mode → 覆盖并回写库
    db.rows[sid]["status"] = "done"
    r3 = SendTool().run({"id": sid, "prompt": "再来", "mode": "acceptEdits"}, _Ctx(db))
    assert r3.success, r3.error
    assert captured[-1].get("permission_mode") == "acceptEdits"
    assert db.rows[sid]["mode"] == "acceptEdits"


def test_send_mode_defaults_accept_edits_when_db_missing(monkeypatch):
    """老会话（无 mode 列值）send 不带 mode → 默认 acceptEdits。"""
    captured = []
    monkeypatch.setattr(codingmod, "_spawn_stream", lambda *a, **k: captured.append(k))
    ctx = _make_ctx_with_session(status="done")   # sid-1 行无 mode 键
    r = SendTool().run({"id": "sid-1", "prompt": "再来"}, ctx)
    assert r.success, r.error
    assert captured[-1].get("permission_mode") == "acceptEdits"
    assert ctx.db.rows["sid-1"]["mode"] == "acceptEdits"


def test_stream_passes_permission_mode_to_runner():
    """_stream 把 permission_mode 透传 runner.run（send/start 的 mode 最终到 SDK options）。"""
    db = _FakeDB(); db.rows["s9"] = {"id": "s9", "status": "running"}
    runner = _FakeRunner(cc_sid=None)
    _run(_stream(db, "s9", "/tmp/p", "hi", runner, emit_event=None,
                 cancel=_threading.Event(), permission_mode="plan"))
    assert runner.called_with["permission_mode"] == "plan"


def test_stream_passes_live_session_entry_to_runner(monkeypatch):
    """_stream 把 _SESSIONS[sid]（live entry）透传 runner.run 的 session_entry（运行中切模式的通道）。"""
    db = _FakeDB(); db.rows["s10"] = {"id": "s10", "status": "running"}
    entry = {"cancel": _threading.Event()}
    monkeypatch.setattr(codingmod._sess, "_SESSIONS", {"s10": entry})
    runner = _FakeRunner(cc_sid=None)
    _run(_stream(db, "s10", "/tmp/p", "hi", runner, emit_event=None,
                 cancel=_threading.Event()))
    assert runner.called_with["session_entry"] is entry


def test_mode_skill_updates_db_and_live_pending(monkeypatch):
    """coding.mode：落库 + live 会话置 mode_pending（runner 下条消息生效）；返回 live 标记。"""
    ctx = _make_ctx_with_session(status="running")
    entry = {"cancel": _threading.Event()}
    monkeypatch.setattr(codingmod._sess, "_SESSIONS", {"sid-1": entry})
    r = ModeTool().run({"id": "sid-1", "mode": "plan"}, ctx)
    assert r.success and r.data["live"] is True            # 有 live entry → mode_pending 已置
    assert entry["mode_pending"] == "plan"
    assert ctx.db.query("sessions", where={"id": "sid-1"})[0]["mode"] == "plan"
    assert r.data["ok"] is True and r.data["mode"] == "plan"
    # 非 live（会话已结束/无 runner）：只落库，不置 pending
    monkeypatch.setattr(codingmod._sess, "_SESSIONS", {})
    r2 = ModeTool().run({"id": "sid-1", "mode": "acceptEdits"}, ctx)
    assert r2.success and r2.data["live"] is False
    assert ctx.db.rows["sid-1"]["mode"] == "acceptEdits"


def test_mode_skill_rejects_bad_mode_and_unknown_session():
    """非法 mode / 不存在会话 → 友好错误，不碰库。"""
    ctx = _make_ctx_with_session(status="running")
    r = ModeTool().run({"id": "sid-1", "mode": "yolo"}, ctx)
    assert not r.success and "不支持" in (r.error or "")
    r2 = ModeTool().run({"id": "ghost", "mode": "plan"}, ctx)
    assert not r2.success and "不存在" in (r2.error or "")
    assert "mode" not in ctx.db.rows["sid-1"]              # 均未落库


def test_runner_applies_pending_mode_between_messages():
    """runner 每条消息前检查 session_entry.mode_pending → client.set_permission_mode（消费即弹出）。"""
    calls = []

    class ModeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def query(self, p): pass
        async def set_permission_mode(self, mode): calls.append(mode)
        async def receive_response(self):
            yield _FakeAssistant([_FakeText("a")])
            yield _FakeAssistant([_FakeText("b")])
            yield _FakeResultMessage("success")

    runner = ClaudeCodeRunner(client_factory=lambda cwd, tools, resume=None, **k: ModeClient())
    entry = {"mode_pending": "plan"}
    events = []
    _run(runner.run("p", "/tmp", on_event=events.append, cancel_event=asyncio.Event(),
                    session_entry=entry))
    assert calls == ["plan"]                               # 只消费一次
    assert "mode_pending" not in entry                     # 消费后弹出
    assert [e["kind"] for e in events] == ["text_delta", "text_delta", "done"]


def test_runner_pending_mode_without_setter_is_skipped():
    """鸭子类型：client 无 set_permission_mode → 静默跳过，流不受影响（pending 仍弹出防重复消费）。"""
    msgs = [_FakeAssistant([_FakeText("x")]), _FakeResultMessage("success")]
    runner = ClaudeCodeRunner(client_factory=lambda cwd, tools, resume=None, **k: _FakeClient(msgs))
    entry = {"mode_pending": "plan"}
    events = []
    _run(runner.run("p", "/tmp", on_event=events.append, cancel_event=asyncio.Event(),
                    session_entry=entry))
    assert [e["kind"] for e in events] == ["text_delta", "done"]
    assert "mode_pending" not in entry


def test_runner_pending_mode_set_failure_is_silent():
    """set_permission_mode 抛错 → 打印并跳过，流不断、不发 error 事件。"""

    class BadModeClient(_FakeClient):
        async def set_permission_mode(self, mode): raise RuntimeError("nope")

    msgs = [_FakeAssistant([_FakeText("x")]), _FakeResultMessage("success")]
    runner = ClaudeCodeRunner(client_factory=lambda cwd, tools, resume=None, **k: BadModeClient(msgs))
    events = []
    _run(runner.run("p", "/tmp", on_event=events.append, cancel_event=asyncio.Event(),
                    session_entry={"mode_pending": "plan"}))
    assert [e["kind"] for e in events] == ["text_delta", "done"]


# ---------- R2 Task 4: rewind 检查点回滚（RewindTool + runner rewind_pending）----------
from coding import RewindTool  # noqa: E402


class _NeverSet:
    """is_set() 恒 False 的 cancel 替身（rewind 只写 rewind_pending，不得触碰 cancel）。"""
    def __init__(self): self.set_calls = 0
    def is_set(self): return False
    def set(self): self.set_calls += 1


def test_rewind_live_session_defers_to_runner(monkeypatch):
    """会话在跑：rewind 只写 rewind_pending，runner 下条消息前执行 rewind_files。"""
    ctx = _make_ctx_with_session(status="running")
    # sessions_registry 挂到 ctx 上（仿 mode 测试的 _SESSIONS monkeypatch 约定）
    ctx.sessions_registry = {"sid-1": {"cancel": _NeverSet()}}
    monkeypatch.setattr(codingmod._sess, "_SESSIONS", ctx.sessions_registry)
    r = RewindTool().run({"id": "sid-1", "user_msg_id": "u-1"}, ctx)
    assert r.success and r.data["live"] is True
    assert ctx.sessions_registry["sid-1"]["rewind_pending"] == "u-1"
    assert ctx.sessions_registry["sid-1"]["cancel"].set_calls == 0   # rewind 不触碰 cancel


class _FreshRewindClient:
    """记录 connect/rewind_files/disconnect 调用序的 fake client（idle rewind 路径用）。"""
    def __init__(self): self.calls = []
    async def connect(self): self.calls.append("connect")
    async def rewind_files(self, uuid): self.calls.append(("rewind_files", uuid))
    async def disconnect(self): self.calls.append("disconnect")


def test_rewind_idle_session_uses_fresh_client(monkeypatch):
    """会话已结束：新开 client（resume=cc_session_id）connect → rewind_files → disconnect。"""
    ctx = _make_ctx_with_session(status="done")   # sid-1：cc_session_id="cc-old-1", cwd="/tmp/p"
    monkeypatch.setattr(codingmod._sess, "_SESSIONS", {})          # 非 live
    events = []
    ctx.emit_event = events.append
    client = _FreshRewindClient()
    seen = {}

    class _FakeRunner:
        def _default_factory(self, cwd, tools, resume=None):
            seen["cwd"] = cwd
            seen["resume"] = resume
            return client

    monkeypatch.setattr(codingmod, "ClaudeCodeRunner", lambda: _FakeRunner())
    r = RewindTool().run({"id": "sid-1", "user_msg_id": "u-1"}, ctx)
    assert r.success and r.data["live"] is False
    assert client.calls == ["connect", ("rewind_files", "u-1"), "disconnect"]
    assert seen["resume"] == "cc-old-1" and seen["cwd"] == "/tmp/p"
    # rewind_ok 事件经 panel_data 推到 coding:studio 面板
    ok = [e for e in events if e.get("kind") == "panel_data"
          and e["payload"]["data"]["event"].get("kind") == "rewind_ok"]
    assert ok and ok[0]["payload"]["data"]["session_id"] == "sid-1"
    assert "已回滚" in ok[0]["payload"]["data"]["event"]["text"]


def test_rewind_fresh_client_failure_emits_error(monkeypatch):
    """新 client rewind 抛错 → error 事件（回滚失败：…）+ success=False，不炸。"""
    ctx = _make_ctx_with_session(status="done")
    monkeypatch.setattr(codingmod._sess, "_SESSIONS", {})
    events = []
    ctx.emit_event = events.append

    class _BadClient:
        async def connect(self): pass
        async def rewind_files(self, uuid): raise RuntimeError("boom")
        async def disconnect(self): pass

    class _FakeRunner:
        def _default_factory(self, cwd, tools, resume=None): return _BadClient()

    monkeypatch.setattr(codingmod, "ClaudeCodeRunner", lambda: _FakeRunner())
    r = RewindTool().run({"id": "sid-1", "user_msg_id": "u-1"}, ctx)
    assert not r.success and "回滚失败" in (r.error or "")
    errs = [e for e in events if e.get("kind") == "panel_data"
            and e["payload"]["data"]["event"].get("kind") == "error"]
    assert errs and "回滚失败" in errs[0]["payload"]["data"]["event"]["text"]


def test_rewind_failure_degrades(monkeypatch):
    """无 cc_session_id 且不在跑 → 失败文案，不炸。"""
    db = _FakeDB()
    db.rows["sid-1"] = {"id": "sid-1", "cwd": "/tmp/p", "cc_session_id": "", "status": "done"}
    monkeypatch.setattr(codingmod._sess, "_SESSIONS", {})
    r = RewindTool().run({"id": "sid-1", "user_msg_id": "u-1"}, _Ctx(db))
    assert not r.success and "无检查点" in (r.error or "")
    # 缺锚点 / 不存在会话 → 友好错误，不碰库不碰 runner
    r2 = RewindTool().run({"id": "sid-1"}, _Ctx(db))       # 无 user_msg_id
    assert not r2.success and "user_msg_id" in (r2.error or "")
    r3 = RewindTool().run({"id": "ghost", "user_msg_id": "u-1"}, _Ctx(db))
    assert not r3.success and "不存在" in (r3.error or "")


def test_rewind_is_l1_no_confirm():
    """⏪ 回滚 = L1（direct+quiet 面板直调不弹确认）：回滚目标由用户显式点击的消息锚定。"""
    assert RewindTool.default_risk == RiskLevel.L1_LOW


def test_runner_applies_pending_rewind_before_next_message():
    """runner 每条消息前检查 session_entry.rewind_pending → client.rewind_files（消费即弹出 + rewind_ok）。"""
    calls = []

    class RewClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def query(self, p): pass
        async def rewind_files(self, uuid): calls.append(uuid)
        async def receive_response(self):
            yield _FakeAssistant([_FakeText("a")])
            yield _FakeResultMessage("success")

    runner = ClaudeCodeRunner(client_factory=lambda cwd, tools, resume=None, **k: RewClient())
    entry = {"rewind_pending": "u-9"}
    events = []
    _run(runner.run("p", "/tmp", on_event=events.append, cancel_event=asyncio.Event(),
                    session_entry=entry))
    assert calls == ["u-9"]                                # 只消费一次
    assert "rewind_pending" not in entry                   # 消费后弹出
    kinds = [e["kind"] for e in events]
    assert kinds == ["rewind_ok", "text_delta", "done"]
    assert "已回滚" in events[0]["text"]


def test_runner_pending_rewind_without_method_is_skipped():
    """鸭子类型：client 无 rewind_files → 静默跳过（pending 仍弹出防重复消费），无 rewind_ok。"""
    msgs = [_FakeAssistant([_FakeText("x")]), _FakeResultMessage("success")]
    runner = ClaudeCodeRunner(client_factory=lambda cwd, tools, resume=None, **k: _FakeClient(msgs))
    entry = {"rewind_pending": "u-1"}
    events = []
    _run(runner.run("p", "/tmp", on_event=events.append, cancel_event=asyncio.Event(),
                    session_entry=entry))
    assert [e["kind"] for e in events] == ["text_delta", "done"]
    assert "rewind_pending" not in entry


def test_runner_pending_rewind_failure_emits_error_event():
    """rewind_files 抛错 → error 事件（回滚失败：…），流不断、跑完仍有 done。"""
    class BadRewClient(_FakeClient):
        async def rewind_files(self, uuid): raise RuntimeError("nope")

    msgs = [_FakeAssistant([_FakeText("x")]), _FakeResultMessage("success")]
    runner = ClaudeCodeRunner(client_factory=lambda cwd, tools, resume=None, **k: BadRewClient(msgs))
    events = []
    _run(runner.run("p", "/tmp", on_event=events.append, cancel_event=asyncio.Event(),
                    session_entry={"rewind_pending": "u-1"}))
    errs = [e for e in events if e["kind"] == "error"]
    assert errs and "回滚失败" in errs[0]["text"]
    assert events[-1]["kind"] == "done"                    # 流继续跑完



# ---------- R2 Task 5: can_use_tool 权限交互（回调桥 + coding.decide）----------
from coding import DecideTool  # noqa: E402

# coding.py 的 `_runner` 是 _sibling 加载的模块单例（DecideTool 查的 _PERM 就在它上面）；
# 本文件顶部 `from _runner import ...` 是另一个模块实例（sys.modules["_runner"]），
# Task 5 测试一律走 codingmod._runner，保证回调桥与 DecideTool 共享同一注册表。
_runner_mod = codingmod._runner


def test_can_use_tool_roundtrip_approve_and_deny():
    """回调发 permission_request 并等待；decide(allow=True) → PermissionResultAllow；decide(False) → Deny。"""
    from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

    async def _roundtrip(allow):
        events = []
        cb = _runner_mod.make_permission_callback("s1", events.append, timeout_s=5.0)

        async def _decide():
            # 等 permission_request 发出（回调先同步发事件再 await 等裁决）
            while not events:
                await asyncio.sleep(0.005)
            rid = events[0]["rid"]
            r = DecideTool().run({"rid": rid, "allow": allow}, _Ctx(_FakeDB()))
            assert r.success, r.error

        decider = asyncio.create_task(_decide())
        res = await cb("Bash", {"command": "ls -la"})
        await decider
        return res, events

    res, events = _run(_roundtrip(True))
    assert isinstance(res, PermissionResultAllow)
    assert [e["kind"] for e in events] == ["permission_request", "permission_done"]
    assert events[0]["tool"] == "Bash" and events[0]["input"] == {"command": "ls -la"}
    assert events[0]["rid"].startswith("perm_s1_")
    assert events[1] == {"kind": "permission_done", "rid": events[0]["rid"], "allow": True}
    assert events[0]["rid"] not in _runner_mod._PERM        # 裁决后注册表清理

    res2, events2 = _run(_roundtrip(False))
    assert isinstance(res2, PermissionResultDeny)
    assert res2.message == "用户拒绝"
    assert events2[-1] == {"kind": "permission_done", "rid": events2[0]["rid"], "allow": False}
    assert events2[0]["rid"] not in _runner_mod._PERM


def test_can_use_tool_timeout_defaults_deny():
    """60s（测试注入短超时）无裁决 → Deny('超时未批准')。"""
    from claude_agent_sdk import PermissionResultDeny

    events = []
    cb = _runner_mod.make_permission_callback("s2", events.append, timeout_s=0.05)
    res = _run(cb("Bash", {"command": "rm -rf /tmp/x"}))
    assert isinstance(res, PermissionResultDeny)
    assert res.message == "超时未批准"
    assert [e["kind"] for e in events] == ["permission_request", "permission_done"]
    assert events[1]["allow"] is False                      # 超时按拒绝收场（面板卡复位）
    assert events[0]["rid"] not in _runner_mod._PERM


def test_decide_skill_resolves_pending():
    ev = _threading.Event()
    _runner_mod._PERM["perm_1"] = {"event": ev, "allow": None}
    try:
        r = DecideTool().run({"rid": "perm_1", "allow": True}, _Ctx(_FakeDB()))
        assert r.success
        assert _runner_mod._PERM["perm_1"]["allow"] is True and _runner_mod._PERM["perm_1"]["event"].is_set()
        # 未知 rid → 友好错误（权限请求不存在或已超时），不炸
        r2 = DecideTool().run({"rid": "perm_ghost", "allow": True}, _Ctx(_FakeDB()))
        assert not r2.success and "不存在" in (r2.error or "")
    finally:
        _runner_mod._PERM.pop("perm_1", None)


def test_release_pending_permissions_scoped_by_sid():
    """放行只命中目标会话：其他会话的挂起等待不动；已裁决（allow 非 None）的不覆盖。"""
    ev_a, ev_b = _threading.Event(), _threading.Event()
    _runner_mod._PERM["perm_sa_1"] = {"event": ev_a, "allow": None}
    _runner_mod._PERM["perm_sb_1"] = {"event": ev_b, "allow": None}
    try:
        assert _runner_mod.release_pending_permissions("sa") == 1
        assert ev_a.is_set() and _runner_mod._PERM["perm_sa_1"]["allow"] is False
        assert not ev_b.is_set() and _runner_mod._PERM["perm_sb_1"]["allow"] is None
        # 幂等：再放行同一会话 0（allow 已非 None）
        assert _runner_mod.release_pending_permissions("sa") == 0
    finally:
        _runner_mod._PERM.pop("perm_sa_1", None)
        _runner_mod._PERM.pop("perm_sb_1", None)


def test_stop_releases_pending_permission_waits():
    """停止会话即放行挂起的权限等待（deny 收场），不再等 60s 超时（终审 Minor#6）。"""
    import time as _time
    from claude_agent_sdk import PermissionResultDeny

    async def _flow():
        events = []
        cb = _runner_mod.make_permission_callback("s9", events.append, timeout_s=30.0)
        waiter = asyncio.create_task(cb("Bash", {"command": "rm -rf /"}))
        while not events:                       # 等 permission_request 发出
            await asyncio.sleep(0.005)
        t0 = _time.monotonic()
        db = _FakeDB(); db.rows["s9"] = {"id": "s9", "status": "running"}
        reg = type("R", (), {"s": {"s9": {"cancelled": False}}})()
        _stop_session(db, reg, "s9")            # 停止 → 应放行权限等待
        res = await waiter
        return res, events, _time.monotonic() - t0

    res, events, dt = _run(_flow())
    assert isinstance(res, PermissionResultDeny)
    assert res.message == "用户拒绝"
    assert dt < 5.0                             # 立即放行，不等 30s 超时
    assert events[-1] == {"kind": "permission_done", "rid": events[0]["rid"], "allow": False}
    assert events[0]["rid"] not in _runner_mod._PERM


def test_stream_passes_can_use_tool_to_runner():
    """_stream 调 runner.run 时挂 can_use_tool 回调（权限审批桥进流式；Task 1 已扩 run 参数）。"""
    db = _FakeDB(); db.rows["s11"] = {"id": "s11", "status": "running"}
    runner = _FakeRunner(cc_sid=None)
    _run(_stream(db, "s11", "/tmp/p", "hi", runner, emit_event=None,
                 cancel=_threading.Event()))
    assert callable(runner.called_with["can_use_tool"])


# ---------- R2 Task 6: @files 上下文（FilesTool 模糊搜索）----------
from coding import FilesTool  # noqa: E402


def _ctx():
    """FilesTool.run 不触 ctx；沿用 _Ctx/_FakeDB 约定造最小鸭式。"""
    return _Ctx(_FakeDB())


def test_files_fuzzy_match_and_excludes(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "login.ts").write_text("x")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("x")
    r = FilesTool().run({"cwd": str(tmp_path), "q": "login"}, _ctx())
    files = r.data["files"]
    assert any(f["rel"] == "src/login.ts" for f in files)
    assert not any("node_modules" in f["rel"] for f in files)


def test_files_caps_results_and_bad_cwd(tmp_path):
    r = FilesTool().run({"cwd": str(tmp_path / "ghost"), "q": ""}, _ctx())
    assert r.success and r.data["files"] == []


# ---------- P2 B1：审批统一进 L2 确认体系（confirmation_needed + action_result 出队）----------
from coding import ListTool  # noqa: E402


def test_can_use_tool_emits_confirmation_and_action_result():
    """confirmation 通道：permission_request 照发（面板只读镜像）+ confirmation_needed
    （L2 攒批载荷，字段与前端 brain.ts 入队条件逐字对齐）+ 裁决后 action_result 出队；
    等 _PERM[rid].event 语义不变。"""
    from claude_agent_sdk import PermissionResultAllow

    async def _flow():
        events, emitted = [], []
        cb = _runner_mod.make_permission_callback("s30", events.append, timeout_s=5.0,
                                                  emit_event=emitted.append)

        async def _decide():
            while not events:               # 等 permission_request 发出
                await asyncio.sleep(0.005)
            r = DecideTool().run({"rid": events[0]["rid"], "allow": True}, _Ctx(_FakeDB()))
            assert r.success, r.error

        decider = asyncio.create_task(_decide())
        res = await cb("Bash", {"command": "npm test"})
        await decider
        return res, events, emitted

    res, events, emitted = _run(_flow())
    assert isinstance(res, PermissionResultAllow)
    assert [e["kind"] for e in events] == ["permission_request", "permission_done"]
    rid = events[0]["rid"]
    # confirmation_needed：actions 攒批格式（对齐 loop 攒批事件）+ 单条 action 兼容字段
    cn = [e for e in emitted if e["kind"] == "confirmation_needed"]
    assert len(cn) == 1
    assert cn[0]["confirmation_id"] == rid and cn[0]["action"]["id"] == rid
    a = cn[0]["actions"][0]
    assert a["id"] == rid and a["tool_id"] == "coding" and a["label"] == "Bash"
    assert a["description"] == "npm test" and a["params"] == {"command": "npm test"}
    assert a["surface"] == "panel:coding" and a["risk"] == 1
    # 出队：action_result 带 action.id（前端 brain.ts 按 action.id 出队）
    ar = [e for e in emitted if e["kind"] == "action_result"]
    assert len(ar) == 1 and ar[0]["action"]["id"] == rid
    assert ar[0]["result"]["success"] is True
    assert rid not in _runner_mod._PERM                      # 裁决后注册表清理


def test_can_use_tool_outcome_deny_and_timeout_action_result():
    """deny/超时两种结局都补 action_result 出队（success=False，error 标注原因）。"""
    from claude_agent_sdk import PermissionResultDeny

    async def _deny_flow():
        events, emitted = [], []
        cb = _runner_mod.make_permission_callback("s31", events.append, timeout_s=5.0,
                                                  emit_event=emitted.append)

        async def _decide():
            while not events:
                await asyncio.sleep(0.005)
            DecideTool().run({"rid": events[0]["rid"], "allow": False}, _Ctx(_FakeDB()))

        d = asyncio.create_task(_decide())
        res = await cb("Bash", {"command": "rm -rf /tmp/x"})
        await d
        return res, emitted

    res, emitted = _run(_deny_flow())
    assert isinstance(res, PermissionResultDeny) and res.message == "用户拒绝"
    ar = [e for e in emitted if e["kind"] == "action_result"]
    assert len(ar) == 1 and ar[0]["result"]["success"] is False
    assert "已拒绝" in ar[0]["result"]["error"]

    # 超时：无裁决 → deny("超时未批准") + action_result 标注超时
    emitted2 = []
    cb2 = _runner_mod.make_permission_callback("s32", lambda e: None, timeout_s=0.05,
                                               emit_event=emitted2.append)
    res2 = _run(cb2("Bash", {"command": "sleep 99"}))
    assert isinstance(res2, PermissionResultDeny) and res2.message == "超时未批准"
    ar2 = [e for e in emitted2 if e["kind"] == "action_result"]
    assert len(ar2) == 1 and ar2[0]["result"]["success"] is False
    assert "超时" in ar2[0]["result"]["error"]


def test_stop_release_emits_action_result_dequeue():
    """stop 放行挂起审批（deny 收场）：等待方同样补 action_result 出队（release 只 set，
    出队由 _cb 结局逻辑统一发，与 permission_done 同路径）。"""
    from claude_agent_sdk import PermissionResultDeny

    async def _flow():
        events, emitted = [], []
        cb = _runner_mod.make_permission_callback("s33", events.append, timeout_s=30.0,
                                                  emit_event=emitted.append)
        waiter = asyncio.create_task(cb("Bash", {"command": "rm -rf /"}))
        while not events:                       # 等 permission_request 发出
            await asyncio.sleep(0.005)
        db = _FakeDB(); db.rows["s33"] = {"id": "s33", "status": "running"}
        reg = type("R", (), {"s": {"s33": {"cancelled": False}}})()
        _stop_session(db, reg, "s33")           # 停止 → 放行权限等待
        res = await waiter
        return res, emitted

    res, emitted = _run(_flow())
    assert isinstance(res, PermissionResultDeny)
    ar = [e for e in emitted if e["kind"] == "action_result"]
    assert len(ar) == 1 and ar[0]["result"]["success"] is False


def test_perm_confirmation_desc_truncated_80_and_file_path():
    """desc 摘要截 80 字；文件工具取 file_path 作摘要/公开参数。"""
    emitted = []
    cb = _runner_mod.make_permission_callback("s34", lambda e: None, timeout_s=0.01,
                                              emit_event=emitted.append)
    _run(cb("Bash", {"command": "x" * 200}))
    a = emitted[0]["actions"][0]
    assert len(a["description"]) == 80

    emitted2 = []
    cb2 = _runner_mod.make_permission_callback("s34", lambda e: None, timeout_s=0.01,
                                               emit_event=emitted2.append)
    _run(cb2("Edit", {"file_path": "/tmp/a/b.ts", "old_string": "1", "new_string": "2"}))
    a2 = emitted2[0]["actions"][0]
    assert a2["description"] == "/tmp/a/b.ts" and a2["params"] == {"file_path": "/tmp/a/b.ts"}


def test_can_use_tool_confirm_batched_channel_resolves():
    """双通道之二：不走 coding.decide，直接写 _PERM（模拟 server confirm_batched 的
    perm_ 路由）同样兑现；先到先得。"""
    from claude_agent_sdk import PermissionResultAllow

    async def _flow():
        events = []
        cb = _runner_mod.make_permission_callback("s35", events.append, timeout_s=5.0)

        async def _confirm():
            while not events:
                await asyncio.sleep(0.005)
            rid = events[0]["rid"]
            entry = _runner_mod._PERM[rid]
            entry["allow"] = True                 # server._fulfill_coding_perm 同款直写
            entry["event"].set()

        c = asyncio.create_task(_confirm())
        res = await cb("Bash", {"command": "ls"})
        await c
        return res

    assert isinstance(_run(_flow()), PermissionResultAllow)


# ---------- P2 B1：coding.list live 字段 / coding.start background ----------

def test_list_skill_live_states():
    """live：waiting（_PERM 挂起，优先）> running（_SESSIONS entry）> idle；已裁决不算 waiting。"""
    db = _FakeDB()
    db.rows["a"] = {"id": "a", "status": "done", "created_at": 1}
    db.rows["b"] = {"id": "b", "status": "running", "created_at": 2}
    db.rows["c"] = {"id": "c", "status": "running", "created_at": 3}
    _runner_mod._PERM["perm_c_7"] = {"event": _threading.Event(), "allow": None}
    codingmod._sess._SESSIONS["b"] = {"cancel": _threading.Event()}
    codingmod._sess._SESSIONS["c"] = {"cancel": _threading.Event()}
    try:
        res = ListTool().run({}, _Ctx(db))
        live = {s["id"]: s["live"] for s in res.data["sessions"]}
        assert live == {"a": "idle", "b": "running", "c": "waiting"}
        # 已裁决（allow 非 None）不再算 waiting → 回落 running
        _runner_mod._PERM["perm_c_7"]["allow"] = True
        res2 = ListTool().run({}, _Ctx(db))
        live2 = {s["id"]: s["live"] for s in res2.data["sessions"]}
        assert live2["c"] == "running"
    finally:
        _runner_mod._PERM.pop("perm_c_7", None)
        codingmod._sess._SESSIONS.pop("b", None)
        codingmod._sess._SESSIONS.pop("c", None)


def test_start_skill_background_param(monkeypatch):
    """background=true → data.panel=None（不开面板，静默执行）；缺省 → coding:studio 照开。"""
    db = _FakeDB()
    monkeypatch.setattr(codingmod, "_spawn_stream", lambda *a, **k: None)
    r = StartTool().run({"cwd": "/tmp", "prompt": "后台改 X", "background": True}, _Ctx(db))
    assert r.success and r.data["panel"] is None
    assert "后台" in r.data["human"]
    r2 = StartTool().run({"cwd": "/tmp", "prompt": "普通任务"}, _Ctx(db))
    assert r2.success and r2.data["panel"] == "coding:studio"
    props = StartTool().openai_schema()["function"]["parameters"]["properties"]
    assert props["background"]["type"] == "boolean"


# ---------- P2 B1：会话终态汇报（reminder/event + task meta）----------

def test_usage_suffix():
    assert codingmod._usage_suffix(None) == ""
    assert codingmod._usage_suffix({}) == ""
    assert codingmod._usage_suffix({"duration_ms": 12300}) == "（耗时 12s）"
    assert codingmod._usage_suffix({"cost_usd": 0.0312, "input_tokens": 10,
                                    "output_tokens": 5}) == "（$0.0312 · 15 tok）"


def test_stream_reports_done_reminder_with_cost():
    """done → kind=reminder（宠物气泡+Feed 任务卡），text 带成本，task meta 完整。"""
    db = _FakeDB(); db.rows["s40"] = {"id": "s40", "status": "running"}
    emitted = []

    class _DoneRunner:
        async def run(self, prompt, cwd, *, on_event, cancel_event, **k):
            on_event({"kind": "done", "usage": {"duration_ms": 12300, "cost_usd": 0.0312,
                                                "input_tokens": 1000, "output_tokens": 540}})
            return "cc-x"

    _run(_stream(db, "s40", "/tmp/p", "修一下登录页的样式问题", _DoneRunner(),
                 emit_event=emitted.append, cancel=_threading.Event()))
    finals = [e for e in emitted if e.get("task")]
    assert len(finals) == 1
    ev = finals[0]
    assert ev["kind"] == "reminder"
    assert "编码任务完成" in ev["text"] and "$0.0312" in ev["text"] and "12s" in ev["text"]
    assert ev["task"] == {"id": "s40", "status": "done", "label": "修一下登录页的样式问题",
                          "prompt": "修一下登录页的样式问题", "plugin": "coding"}
    assert ev["plugin"] == "coding"
    assert db.updates[-1][1]["status"] == "done"


def test_stream_reports_failed_reminder():
    """error → failed：kind=reminder，task.status=failed（英文键，对齐 Feed 徽章映射），
    中文「失败」只留在用户可读的 text。"""
    db = _FakeDB(); db.rows["s41"] = {"id": "s41", "status": "running"}
    emitted = []

    class _ErrRunner:
        async def run(self, prompt, cwd, *, on_event, cancel_event, **k):
            on_event({"kind": "error", "text": "boom"})
            return None

    _run(_stream(db, "s41", "/tmp/p", "做个功能", _ErrRunner(),
                 emit_event=emitted.append, cancel=_threading.Event()))
    finals = [e for e in emitted if e.get("task")]
    assert len(finals) == 1 and finals[0]["kind"] == "reminder"
    assert "失败" in finals[0]["text"] and finals[0]["task"]["status"] == "failed"
    assert db.updates[-1][1]["status"] == "failed"


def test_stream_reports_stopped_as_event_not_reminder():
    """stopped（用户主动停）→ kind=event（仅 Feed 任务卡），不发 reminder 气泡。"""
    db = _FakeDB(); db.rows["s42"] = {"id": "s42", "status": "stopped"}  # 用户已主动停
    emitted = []
    _run(_stream(db, "s42", "/tmp/p", "长跑任务", _FakeRunner(cc_sid=None),
                 emit_event=emitted.append, cancel=_threading.Event()))
    finals = [e for e in emitted if e.get("task")]
    assert len(finals) == 1 and finals[0]["kind"] == "event"
    assert finals[0]["task"]["status"] == "stopped"
    assert not any(e.get("kind") == "reminder" and e.get("task") for e in emitted)
    assert db.updates[-1][1]["status"] == "stopped"


def test_stream_no_report_when_emit_event_none():
    """emit_event=None（测试/未注入）→ 终态汇报静默跳过，落库不受影响。"""
    db = _FakeDB(); db.rows["s43"] = {"id": "s43", "status": "running"}
    _run(_stream(db, "s43", "/tmp/p", "hi", _FakeRunner(cc_sid="cc-1"),
                 emit_event=None, cancel=_threading.Event()))
    assert db.updates[-1][1]["status"] == "done"


# ---------- P2 B3：coding.attach 接管（任务卡点击 → 打开面板恢复会话）----------
from coding import AttachTool  # noqa: E402


def test_attach_returns_session_and_attach_flag():
    """attach 成功：data 带 {session_id, attach:True}（逐字对齐 studio 面板 handleData init 判别）；
    任何终态/运行态会话都可接管（恢复/围观由面板侧处理）。"""
    db = _FakeDB()
    db.rows["s-att"] = {"id": "s-att", "status": "done"}
    res = AttachTool().run({"session_id": "s-att"}, _Ctx(db))
    assert res.success, res.error
    assert res.data["session_id"] == "s-att"
    assert res.data["attach"] is True
    db2 = _FakeDB()
    db2.rows["s-run"] = {"id": "s-run", "status": "running"}
    res2 = AttachTool().run({"session_id": "s-run"}, _Ctx(db2))
    assert res2.success and res2.data["attach"] is True


def test_attach_payload_carries_agent():
    """data.agent = 库行 agent（跨引擎接管徽标立即正确，不等首条流事件）；
    老行缺 agent 列 → 缺省 claude-code（同 _stream/_runner_for 缺省）。"""
    db = _FakeDB()
    db.rows["s-cx"] = {"id": "s-cx", "status": "done", "agent": "codex"}
    res = AttachTool().run({"session_id": "s-cx"}, _Ctx(db))
    assert res.success and res.data["agent"] == "codex"
    db2 = _FakeDB()
    db2.rows["s-old"] = {"id": "s-old", "status": "done"}    # 老行无 agent 列
    res2 = AttachTool().run({"session_id": "s-old"}, _Ctx(db2))
    assert res2.success and res2.data["agent"] == "claude-code"


def test_attach_rejects_unknown_session_and_missing_param():
    """会话不存在 → 明确错误（面板不开）；缺 session_id → 同样拒绝。"""
    db = _FakeDB()
    res = AttachTool().run({"session_id": "no-such"}, _Ctx(db))
    assert not res.success and "不存在" in (res.error or "")
    res2 = AttachTool().run({}, _Ctx(db))
    assert not res2.success and "session_id" in (res2.error or "")


def test_attach_is_l0_readonly():
    """attach 只校验存在 + 开面板，不改状态 → L0（直调不弹确认，任务卡点击零摩擦）。"""
    assert AttachTool.default_risk == RiskLevel.L0_READONLY


def test_attach_skill_openai_schema_shape():
    schema = AttachTool().openai_schema()
    assert schema["function"]["name"] == "coding.attach"
    props = schema["function"]["parameters"]["properties"]
    assert set(props.keys()) == {"session_id"}
    assert schema["function"]["parameters"]["required"] == ["session_id"]


# ---------- P1 C1：统一接续 popover —— coding.last_sessions / coding.attach_cc ----------
import json as _json  # noqa: E402
from datetime import datetime as _dt  # noqa: E402
from coding import LastSessionsTool, AttachCcTool  # noqa: E402

_C1_CWD = "/tmp/proj"            # slug = -tmp-proj（re.sub(r"[^A-Za-z0-9-]", "-", cwd)）
_C1_SLUG = "-tmp-proj"


def _c1_write_cc(home, rel, lines, mtime=None, slug=_C1_SLUG):
    """在 home/.claude/projects/<slug>/<rel> 造 CC transcript（rel 可带子目录）；返回路径。"""
    p = os.path.join(home, ".claude", "projects", slug, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write("\n".join(_json.dumps(x) for x in lines) + "\n")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def _c1_write_codex(root, rel, cwd, sid, ts, turns):
    """在 root 下造一个 Codex JSONL session（同 test_coding_handoff._write_session 形态）。"""
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    lines = [_json.dumps({"type": "session_meta",
                          "payload": {"session_id": sid, "cwd": cwd, "timestamp": ts}})]
    for role, text in turns:
        lines.append(_json.dumps({"type": "response_item",
                                  "payload": {"role": role, "content": text}}))
    with open(p, "w") as f:
        f.write("\n".join(lines) + "\n")
    return p


_C1_CC_LINES = [
    {"type": "user", "uuid": "u1", "timestamp": "2026-08-10T10:00:00Z",
     "message": {"content": "第一句话"}},
    {"type": "assistant", "uuid": "a1", "timestamp": "2026-08-10T10:01:00Z",
     "message": {"content": [{"type": "text", "text": "回答一"}]}},
    {"type": "system", "message": {"content": "忽略我"}},
    {"type": "user", "uuid": "u2", "timestamp": "2026-08-10T10:02:00Z",
     "message": {"content": [{"type": "text", "text": "第二句话"}]}},
]


def test_last_sessions_dual_source(tmp_path, monkeypatch):
    """双源命中：cc 取 slug 目录 mtime 最新；codex 取同 cwd timestamp 最新。"""
    home = str(tmp_path)
    long_first = "甲" * 70   # >60 字，验 summary 截 60
    _c1_write_cc(home, "cc-old.jsonl", [
        {"type": "user", "message": {"content": "旧的"}}], mtime=1000)
    _c1_write_cc(home, "cc-new.jsonl", [
        {"type": "user", "uuid": "u1", "timestamp": "2026-08-10T10:00:00Z",
         "message": {"content": long_first}},
        {"type": "assistant", "uuid": "a1", "timestamp": "2026-08-10T10:01:00Z",
         "message": {"content": "答"}},
    ], mtime=2000)
    root = str(tmp_path / "codex_sessions")
    _c1_write_codex(root, "2026/08/05/x.jsonl", _C1_CWD, "cx-old", "2026-08-05T10:00:00Z",
                    [("user", "旧 codex")])
    _c1_write_codex(root, "2026/08/09/y.jsonl", _C1_CWD, "cx-new", "2026-08-09T10:00:00Z",
                    [("user", "新 codex 任务")])
    _c1_write_codex(root, "2026/08/10/z.jsonl", "/tmp/other", "cx-other", "2026-08-10T10:00:00Z",
                    [("user", "别的项目")])
    monkeypatch.setenv("HOME", home)
    monkeypatch.setattr(codingmod, "_codex_sessions_root", lambda: root)
    res = LastSessionsTool().run({"cwd": _C1_CWD}, _Ctx(_FakeDB()))
    assert res.success
    cc = res.data["cc"]
    assert cc["cc_session_id"] == "cc-new"          # mtime 最新，而非 cc-old
    assert cc["ts"] == 2000
    assert cc["summary"] == long_first[:60]         # 首条 user 截 60 字
    assert cc["message_count"] == 2                 # user+assistant 各 1（system 不计）
    codex = res.data["codex"]
    assert codex["session_id"] == "cx-new"          # timestamp 最新；别的项目已过滤
    assert codex["ts"] == int(_dt.fromisoformat("2026-08-09T10:00:00+00:00").timestamp())
    assert codex["summary"] == "新 codex 任务"


def test_last_sessions_cc_only_and_empty(tmp_path, monkeypatch):
    """单源：只有 cc → codex 为 None；全空：两源都 None（不报错）。"""
    home = str(tmp_path)
    root = str(tmp_path / "codex_sessions")
    monkeypatch.setenv("HOME", home)
    monkeypatch.setattr(codingmod, "_codex_sessions_root", lambda: root)
    _c1_write_cc(home, "cc-1.jsonl", _C1_CC_LINES, mtime=1500)
    res = LastSessionsTool().run({"cwd": _C1_CWD}, _Ctx(_FakeDB()))
    assert res.data["cc"]["cc_session_id"] == "cc-1"
    assert res.data["cc"]["message_count"] == 3     # 2 user + 1 assistant
    assert res.data["cc"]["summary"] == "第一句话"
    assert res.data["codex"] is None
    # 全空：另一个无记录的 cwd
    res2 = LastSessionsTool().run({"cwd": "/tmp/nowhere"}, _Ctx(_FakeDB()))
    assert res2.success and res2.data == {"cc": None, "codex": None}


def test_last_sessions_excludes_subagents_and_tool_results(tmp_path, monkeypatch):
    """slug 排除：<uuid>/subagents/ 与 tool-results/ 下的 .jsonl 不参与最新判定。"""
    home = str(tmp_path)
    monkeypatch.setenv("HOME", home)
    monkeypatch.setattr(codingmod, "_codex_sessions_root", lambda: str(tmp_path / "none"))
    _c1_write_cc(home, "cc-main.jsonl", _C1_CC_LINES, mtime=1000)
    # 嵌套文件 mtime 更新也必须被排除
    _c1_write_cc(home, "11112222/subagents/agent-x.jsonl", [
        {"type": "user", "message": {"content": "子代理"}}], mtime=9999)
    _c1_write_cc(home, "11112222/tool-results/r.jsonl", [
        {"type": "user", "message": {"content": "工具结果"}}], mtime=8888)
    res = LastSessionsTool().run({"cwd": _C1_CWD}, _Ctx(_FakeDB()))
    assert res.data["cc"]["cc_session_id"] == "cc-main"
    assert res.data["cc"]["summary"] == "第一句话"


def test_last_sessions_missing_cwd_errors():
    res = LastSessionsTool().run({}, _Ctx(_FakeDB()))
    assert not res.success and "cwd" in res.error


def test_attach_cc_imports_transcript(tmp_path, monkeypatch):
    """导入落库：sessions 行字段（agent=cc/source=import/status=done/时间取内容时间）
    + messages 写整段 transcript（seq 1..n，user 带 uuid）；二次调用幂等返回同 id。"""
    home = str(tmp_path)
    _c1_write_cc(home, "cc-imp.jsonl", _C1_CC_LINES, mtime=5000)
    monkeypatch.setenv("HOME", home)
    db = _FakeDB()
    res = AttachCcTool().run({"cc_session_id": "cc-imp", "cwd": _C1_CWD}, _Ctx(db))
    assert res.success
    sid = res.data["session_id"]
    row = db.rows[sid]
    assert row["agent"] == "cc" and row["source"] == "import" and row["status"] == "done"
    assert row["cc_session_id"] == "cc-imp" and row["cwd"] == _C1_CWD
    assert row["created_at"] == int(_dt.fromisoformat("2026-08-10T10:00:00+00:00").timestamp())
    assert row["finished_at"] == int(_dt.fromisoformat("2026-08-10T10:02:00+00:00").timestamp())
    assert row["prompt"] == "第一句话"
    msgs = db._tables["messages"]
    assert [m["seq"] for m in msgs] == [1, 2, 3]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert [m["text"] for m in msgs] == ["第一句话", "回答一", "第二句话"]
    assert [m["uuid"] for m in msgs] == ["u1", "", "u2"]   # user 带 uuid（rewind 锚点）
    assert all(m["session_id"] == sid for m in msgs)
    # 幂等：再导一次 → 同 id，不重复插
    res2 = AttachCcTool().run({"cc_session_id": "cc-imp", "cwd": _C1_CWD}, _Ctx(db))
    assert res2.success and res2.data["session_id"] == sid
    assert len(db.rows) == 1 and len(db._tables["messages"]) == 3


def test_attach_cc_idempotent_with_existing_db_row(tmp_path, monkeypatch):
    """cc_session_id 已在库（如译宝自己跑过的会话）→ 直接返回既有 id，不读 transcript。"""
    monkeypatch.setenv("HOME", str(tmp_path))   # 无 transcript 也应命中幂等分支
    db = _FakeDB()
    db.rows["s-have"] = {"id": "s-have", "cc_session_id": "cc-1", "status": "done"}
    res = AttachCcTool().run({"cc_session_id": "cc-1", "cwd": _C1_CWD}, _Ctx(db))
    assert res.success and res.data["session_id"] == "s-have"
    assert len(db.rows) == 1


def test_attach_cc_missing_transcript_and_bad_params(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    db = _FakeDB()
    res = AttachCcTool().run({"cc_session_id": "ghost", "cwd": _C1_CWD}, _Ctx(db))
    assert not res.success and "ghost" in res.error
    res2 = AttachCcTool().run({"cc_session_id": "../escape", "cwd": _C1_CWD}, _Ctx(db))
    assert not res2.success                       # 白名单挡路径逃逸（同 _cc_reader）
    res3 = AttachCcTool().run({"cwd": _C1_CWD}, _Ctx(db))
    assert not res3.success and "cc_session_id" in res3.error
    res4 = AttachCcTool().run({"cc_session_id": "x"}, _Ctx(db))
    assert not res4.success and "cwd" in res4.error


def test_attach_cc_finds_transcript_outside_cwd_slug(tmp_path, monkeypatch):
    """cwd slug 目录没有时，全 projects 顶层 glob 兜底（仍排除 subagents 嵌套层）。"""
    home = str(tmp_path)
    _c1_write_cc(home, "cc-else.jsonl", _C1_CC_LINES, slug="-tmp-otherproj")
    monkeypatch.setenv("HOME", home)
    db = _FakeDB()
    res = AttachCcTool().run({"cc_session_id": "cc-else", "cwd": _C1_CWD}, _Ctx(db))
    assert res.success
    assert db.rows[res.data["session_id"]]["cc_session_id"] == "cc-else"


def test_send_on_imported_session_resumes_cc_natively(tmp_path, monkeypatch):
    """resume 链路核实：导入的会话走 coding.send 时，既有链路拿库里 cc_session_id
    透传 _spawn_stream(resume_session_id=cc) → SDK ClaudeAgentOptions(resume=…) 原生续。"""
    home = str(tmp_path)
    _c1_write_cc(home, "cc-r.jsonl", _C1_CC_LINES)
    monkeypatch.setenv("HOME", home)
    db = _FakeDB()
    sid = AttachCcTool().run({"cc_session_id": "cc-r", "cwd": _C1_CWD}, _Ctx(db)).data["session_id"]
    captured = {}
    monkeypatch.setattr(
        codingmod, "_spawn_stream",
        lambda *a, **k: captured.update({"args": a, "kwargs": k}))
    res = SendTool().run({"id": sid, "prompt": "继续干"}, _Ctx(db))
    assert res.success
    assert captured["kwargs"].get("resume_session_id") == "cc-r"   # SDK resume 原生续
    # history 按 DB id 读回导入的 transcript（resumeSession 链路）
    h = HistoryTool().run({"id": sid}, _Ctx(db))
    assert h.success and [m["text"] for m in h.data["messages"]] == ["第一句话", "回答一", "第二句话"]


def test_api_toml_registers_last_sessions_and_attach_cc():
    """api.toml 契约锁定：两方法 direct + quiet（popover 内调用，不发 panel 事件）、无 panel 字段。"""
    import tomllib
    api_path = os.path.join(os.path.dirname(__file__), "..", "..", "plugins", "coding", "api.toml")
    api = tomllib.loads(open(api_path, encoding="utf-8").read())
    methods = {m["name"]: m for m in api["method"]}
    for name in ("last_sessions", "attach_cc"):
        m = methods[name]
        assert m["handler"] == f"coding.{name}"
        assert m["direct"] is True and m["quiet"] is True
        assert "panel" not in m


# ---------- attach_codex 导入 rollout 消息（验收修：原生续恢复完整对话 + 空行补导）----------
from coding import AttachCodexTool  # noqa: E402

_D2_CWD = "/tmp/proj"


def _d2_msg(role, text, ts=None):
    """response_item 对话行（content 块列表形态，对齐真 rollout；ts 为行顶层 timestamp）。"""
    o = {"type": "response_item",
         "payload": {"type": "message", "role": role,
                     "content": [{"type": "input_text" if role == "user" else "output_text",
                                  "text": text}]}}
    if ts:
        o["timestamp"] = ts
    return o


def _d2_write_rollout(root, rel, sid, cwd, ts, lines):
    """在 root 下造 codex rollout：session_meta 首行 + lines（dict 序列化 / str 原样写，可造坏行）。"""
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    out = [_json.dumps({"type": "session_meta",
                        "payload": {"session_id": sid, "cwd": cwd, "timestamp": ts}})]
    out += [x if isinstance(x, str) else _json.dumps(x) for x in lines]
    with open(p, "w") as f:
        f.write("\n".join(out) + "\n")
    return p


# 对话 3 条 + 无 ts 兜底 1 条，机器条目 5 种（developer 指令/environment_context/event_msg 回声/
# turn_context/turn_aborted 标记）应全部跳过——前 4 种按类型/角色天然排除，末种按前缀过滤
_D2_LINES = [
    _d2_msg("developer", "<permissions instructions> 机器指令", "2026-08-16T09:59:00Z"),
    _d2_msg("user", "<environment_context>\n  <cwd>/tmp/proj</cwd>\n</environment_context>",
            "2026-08-16T09:59:30Z"),
    _d2_msg("user", "第一句", "2026-08-16T10:00:00Z"),
    _d2_msg("assistant", "回答一", "2026-08-16T10:01:00Z"),
    {"type": "event_msg", "payload": {"type": "user_message", "message": "回声"}},
    {"type": "turn_context", "payload": {"cwd": _D2_CWD}},
    _d2_msg("user", "<turn_aborted>\nThe user interrupted", "2026-08-16T10:01:30Z"),
    _d2_msg("user", "第二句", "2026-08-16T10:02:00Z"),
    _d2_msg("assistant", "无时间戳的尾句"),
]
_D2_TEXTS = ["第一句", "回答一", "第二句", "无时间戳的尾句"]


def test_attach_codex_imports_rollout_messages(tmp_path, monkeypatch):
    """导入落库：messages 写整段 rollout 对话（seq 1..n、role/ts 取行 timestamp，缺则会话 ts 兜底、
    uuid 留空）；机器条目全跳过；coding.history 读回完整对话（原生续对话框不再为空）。"""
    root = str(tmp_path / "codex_sessions")
    _d2_write_rollout(root, "2026/08/16/r.jsonl", "t-msg", _D2_CWD,
                      "2026-08-16T10:00:00Z", _D2_LINES)
    monkeypatch.setattr(codingmod, "_codex_sessions_root", lambda: root)
    db = _FakeDB()
    res = AttachCodexTool().run({"session_id": "t-msg"}, _Ctx(db))
    assert res.success
    sid = res.data["session_id"]
    row = db.rows[sid]
    assert row["agent"] == "codex" and row["source"] == "native" and row["status"] == "done"
    msgs = db._tables["messages"]
    assert [m["seq"] for m in msgs] == [1, 2, 3, 4]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert [m["text"] for m in msgs] == _D2_TEXTS
    assert [m["ts"] for m in msgs[:3]] == [
        int(_dt.fromisoformat("2026-08-16T10:00:00+00:00").timestamp()),
        int(_dt.fromisoformat("2026-08-16T10:01:00+00:00").timestamp()),
        int(_dt.fromisoformat("2026-08-16T10:02:00+00:00").timestamp())]
    assert msgs[3]["ts"] == row["created_at"]              # 无行 timestamp → 会话 ts 兜底
    assert all(m["session_id"] == sid and m["uuid"] == "" for m in msgs)
    # resumeSession 链路核实：coding.history 读本 DB id 的 messages 表
    h = HistoryTool().run({"id": sid}, _Ctx(db))
    assert h.success and [m["text"] for m in h.data["messages"]] == _D2_TEXTS


def test_attach_codex_idempotent_keeps_messages(tmp_path, monkeypatch):
    """幂等不重复：二次 attach 同 id 返回，messages 不重复插（messages 非空 → 直返）。"""
    root = str(tmp_path / "codex_sessions")
    _d2_write_rollout(root, "r.jsonl", "t-idem", _D2_CWD, "2026-08-16T10:00:00Z", _D2_LINES)
    monkeypatch.setattr(codingmod, "_codex_sessions_root", lambda: root)
    db = _FakeDB()
    sid = AttachCodexTool().run({"session_id": "t-idem"}, _Ctx(db)).data["session_id"]
    assert len(db._tables["messages"]) == 4
    res2 = AttachCodexTool().run({"session_id": "t-idem"}, _Ctx(db))
    assert res2.success and res2.data["session_id"] == sid
    assert len(db.rows) == 1 and len(db._tables["messages"]) == 4


def test_attach_codex_backfills_empty_session_row(tmp_path, monkeypatch):
    """空行补导：v2 初期 attach 产生的 sessions 空行（messages 为空）→ attach 时补导 rollout
    消息（复用既有 sid 不新建行）；补齐后再 attach 幂等直返不再补。"""
    root = str(tmp_path / "codex_sessions")
    _d2_write_rollout(root, "r.jsonl", "t-old", _D2_CWD, "2026-08-16T10:00:00Z", _D2_LINES)
    monkeypatch.setattr(codingmod, "_codex_sessions_root", lambda: root)
    db = _FakeDB()
    db.rows["s-old"] = {"id": "s-old", "agent": "codex", "cc_session_id": "t-old",
                        "status": "done", "created_at": 1234}
    res = AttachCodexTool().run({"session_id": "t-old"}, _Ctx(db))
    assert res.success and res.data["session_id"] == "s-old"
    assert len(db.rows) == 1                                 # 不新建 sessions 行
    msgs = db._tables["messages"]
    assert [m["text"] for m in msgs] == _D2_TEXTS
    assert all(m["session_id"] == "s-old" for m in msgs)
    assert msgs[3]["ts"] == 1234                             # 兜底 ts 取既有行 created_at
    res2 = AttachCodexTool().run({"session_id": "t-old"}, _Ctx(db))
    assert res2.data["session_id"] == "s-old" and len(db._tables["messages"]) == 4


def test_attach_codex_backfill_rollout_gone_is_silent(tmp_path, monkeypatch):
    """空行补导降级：rollout 已删 → 无源可补，幂等直返既有 id 不报错。"""
    monkeypatch.setattr(codingmod, "_codex_sessions_root", lambda: str(tmp_path / "none"))
    db = _FakeDB()
    db.rows["s-old"] = {"id": "s-old", "agent": "codex", "cc_session_id": "t-gone",
                        "status": "done", "created_at": 1234}
    res = AttachCodexTool().run({"session_id": "t-gone"}, _Ctx(db))
    assert res.success and res.data["session_id"] == "s-old"
    assert db._tables.get("messages", []) == []


def test_attach_codex_bad_rollout_degrades(tmp_path, monkeypatch):
    """坏 rollout 降级：meta 后的坏行跳过、能解析的对话仍导入；首行 meta 本身坏 → 未找到报错。"""
    root = str(tmp_path / "codex_sessions")
    _d2_write_rollout(root, "r.jsonl", "t-bad", _D2_CWD, "2026-08-16T10:00:00Z", [
        "{not json",                                          # 坏行跳过
        _d2_msg("user", "好行", "2026-08-16T10:00:00Z"),
        _json.dumps({"type": "response_item"})[:20],          # 截断坏行跳过
        _d2_msg("assistant", "也好", "2026-08-16T10:01:00Z"),
    ])
    monkeypatch.setattr(codingmod, "_codex_sessions_root", lambda: root)
    db = _FakeDB()
    res = AttachCodexTool().run({"session_id": "t-bad"}, _Ctx(db))
    assert res.success
    assert [m["text"] for m in db._tables["messages"]] == ["好行", "也好"]
    with open(os.path.join(root, "broken.jsonl"), "w") as f:  # 首行即坏 → 扫描找不到
        f.write("{ totally broken\n")
    res2 = AttachCodexTool().run({"session_id": "t-x"}, _Ctx(db))
    assert not res2.success and "t-x" in res2.error


# ---------- R4 阶段四 Task 1: coding.perm_pending（review 栏挂载快照源）----------
from coding import PermPendingTool  # noqa: E402


def test_perm_pending_lists_only_undecided():
    """_PERM 挂起项列出 rid/sid/tool/summary/params；已裁决项不出现；sid 含下划线解析安全。"""
    _runner_mod._PERM.clear()
    try:
        _runner_mod._PERM["perm_s1_1"] = {"event": _threading.Event(), "allow": None,
            "tool": "Bash", "summary": "ls -la", "params": {"command": "ls -la"}}
        _runner_mod._PERM["perm_s1_2"] = {"event": _threading.Event(), "allow": True,
            "tool": "Edit", "summary": "a.py", "params": {"file_path": "a.py"}}
        _runner_mod._PERM["perm_my_sid_9"] = {"event": _threading.Event(), "allow": None,
            "tool": "Read", "summary": "b.py", "params": {"file_path": "b.py"}}
        r = PermPendingTool().run({}, _Ctx(_FakeDB()))
        assert r.success
        pending = r.data["pending"]
        assert [p["rid"] for p in pending] == ["perm_s1_1", "perm_my_sid_9"]
        item = pending[0]
        assert item["sid"] == "s1"
        assert item["tool"] == "Bash" and item["summary"] == "ls -la"
        assert item["params"] == {"command": "ls -la"}
        assert pending[1]["sid"] == "my_sid"           # rsplit 去尾序号，sid 含下划线安全
    finally:
        _runner_mod._PERM.clear()


def test_perm_pending_legacy_entry_missing_fields():
    """老 entry 只有 event/allow（缺 tool/summary/params）→ summary 返 ""、params 返 {}，不炸。"""
    _runner_mod._PERM.clear()
    try:
        _runner_mod._PERM["perm_old_1"] = {"event": _threading.Event(), "allow": None}
        r = PermPendingTool().run({}, _Ctx(_FakeDB()))
        assert r.success
        item = r.data["pending"][0]
        assert item["rid"] == "perm_old_1" and item["sid"] == "old"
        assert item["tool"] == "" and item["summary"] == "" and item["params"] == {}
    finally:
        _runner_mod._PERM.clear()


def test_perm_entry_carries_tool_summary_params():
    """回调桥写 entry 带 tool/summary/params 三字段（perm_pending 快照数据源）。"""
    async def _probe():
        events = []
        cb = _runner_mod.make_permission_callback("s9", events.append, timeout_s=5.0)

        async def _decide():
            # 等 permission_request 发出（回调先同步发事件再 await 等裁决）
            while not events:
                await asyncio.sleep(0.005)
            rid = events[0]["rid"]
            entry = _runner_mod._PERM[rid]
            assert entry["tool"] == "Bash"
            assert entry["summary"] == "ls -la"
            assert entry["params"] == {"command": "ls -la"}
            r = DecideTool().run({"rid": rid, "allow": True}, _Ctx(_FakeDB()))
            assert r.success, r.error

        decider = asyncio.create_task(_decide())
        await cb("Bash", {"command": "ls -la"})
        await decider

    _run(_probe())



# ---------- R4 阶段五 Task 2: codex usage baseline 落库（多轮 resume 差分跨轮持久）----------
from _codex_runner import CodexCliRunner  # noqa: E402


class _CodexFakeProc:
    """鸭式 asyncio.subprocess.Process（codex runner fake，不触真 CLI）：
    stdin 写入/关闭收下即弃；stdout=自身异步迭代预置 JSONL 行；无 stderr 属性
    （runner getattr 容缺不排）；wait 即退、returncode=0。"""
    def __init__(self, lines):
        self.stdin = SimpleNamespace(write=lambda b: None, close=lambda: None)
        self._lines = list(lines)
        self.returncode = 0
    async def wait(self): return self.returncode
    @property
    def stdout(self): return self
    def __aiter__(self): return self._gen()
    async def _gen(self):
        for ln in self._lines:
            yield (ln + "\n").encode()


def _codex_runner_with(lines):
    """预置 JSONL 行的 CodexCliRunner（process_factory 注入 fake proc）。"""
    proc = _CodexFakeProc(lines)
    async def factory(argv, cwd): return proc
    return CodexCliRunner(process_factory=factory)


def _wait_stream_done(sid, timeout=5.0):
    """等 _spawn_stream 的 daemon 线程收尾（entry pop = 流终）；超时直接断言失败。"""
    deadline = time.time() + timeout
    while sid in codingmod._sess._SESSIONS:
        assert time.time() < deadline, f"session {sid} 流式线程未在 {timeout}s 内收尾"
        time.sleep(0.01)


def test_usage_baseline_persisted_and_restored(monkeypatch):
    """两轮 codex send（真 _spawn_stream 线程）：第一轮 done 后 sessions 行 usage_baseline
    已落库（thread 累计值 JSON）；entry 流终 pop 后第二轮 _spawn_stream 从库回填——
    done 的 usage 是增量而非 thread 全量（修多轮 resume token 逐轮重复计）。"""
    db = _FakeDB()
    db.rows["s-cx"] = {"id": "s-cx", "status": "running", "agent": "codex",
                       "cwd": "/tmp/p", "cc_session_id": "t-1"}
    monkeypatch.setattr(codingmod._sess, "_SESSIONS", {})   # 隔离真注册表；流式线程内读写同此 dict
    emitted = []

    # 第一轮：全新 entry（无 baseline）→ done 报全量 1000/100，baseline 落库
    codingmod._spawn_stream(db, "s-cx", "/tmp/p", "第一轮", _codex_runner_with([
        _json.dumps({"type": "thread.started", "thread_id": "t-1"}),
        _json.dumps({"type": "turn.completed",
                     "usage": {"input_tokens": 1000, "output_tokens": 100}}),
    ]), emitted.append, agent="codex")
    _wait_stream_done("s-cx")
    base1 = _json.loads(db.rows["s-cx"]["usage_baseline"])
    assert base1 == {"input_tokens": 1000, "output_tokens": 100}

    # 第二轮：entry 已 pop（模拟重启/多轮形态）→ _spawn_stream 从库回填 → done 只报增量
    db.update("sessions", "s-cx", {"status": "running"})   # send 的重置（本测试直调 _spawn_stream）
    emitted.clear()
    codingmod._spawn_stream(db, "s-cx", "/tmp/p", "第二轮", _codex_runner_with([
        _json.dumps({"type": "thread.started", "thread_id": "t-1"}),
        _json.dumps({"type": "turn.completed",
                     "usage": {"input_tokens": 1400, "output_tokens": 130}}),
    ]), emitted.append, resume_session_id="t-1", agent="codex")
    _wait_stream_done("s-cx")
    dones = [e["payload"]["data"]["event"] for e in emitted
             if e.get("kind") == "panel_data"
             and e["payload"]["data"]["event"].get("kind") == "done"]
    assert len(dones) == 1
    assert dones[0]["usage"]["input_tokens"] == 400     # 增量 = 1400 − 1000（非全量 1400）
    assert dones[0]["usage"]["output_tokens"] == 30
    # 第二轮 baseline 同步刷新落库（第三轮差分基准）
    assert _json.loads(db.rows["s-cx"]["usage_baseline"]) == {
        "input_tokens": 1400, "output_tokens": 130}


def test_usage_baseline_corrupt_row_does_not_break_stream(monkeypatch):
    """坏数据不炸流：sessions 行 usage_baseline 非法 JSON → 回填静默跳过（baseline 按 0，
    本轮退化为全量上报），流照常 done；结束后以新累计值覆盖落库。"""
    db = _FakeDB()
    db.rows["s-bad"] = {"id": "s-bad", "status": "running", "agent": "codex",
                        "cwd": "/tmp/p", "cc_session_id": "t-9",
                        "usage_baseline": "{not json"}
    monkeypatch.setattr(codingmod._sess, "_SESSIONS", {})
    emitted = []
    codingmod._spawn_stream(db, "s-bad", "/tmp/p", "任务", _codex_runner_with([
        _json.dumps({"type": "thread.started", "thread_id": "t-9"}),
        _json.dumps({"type": "turn.completed",
                     "usage": {"input_tokens": 50, "output_tokens": 5}}),
    ]), emitted.append, resume_session_id="t-9", agent="codex")
    _wait_stream_done("s-bad")
    dones = [e["payload"]["data"]["event"] for e in emitted
             if e.get("kind") == "panel_data"
             and e["payload"]["data"]["event"].get("kind") == "done"]
    assert len(dones) == 1
    assert dones[0]["usage"]["input_tokens"] == 50      # baseline 按 0 → 全量上报一轮（退化不炸）
    assert _json.loads(db.rows["s-bad"]["usage_baseline"]) == {
        "input_tokens": 50, "output_tokens": 5}


# ---------- P2 收尾 spec §A/§C：steer 队列 drain / stop 清队 / 陈旧 running 对账 ----------

class _SteerRunner:
    """鸭式 runner：记录每轮 (prompt, resume)；恒返回 cc-1。
    on_run 钩子（按已跑轮数回调）模拟「续跑期间新到 steer 入队」。"""
    def __init__(self, on_run=None):
        self.calls = []
        self._on_run = on_run
    async def run(self, prompt, cwd, *, on_event, cancel_event, resume_session_id=None,
                  permission_mode="acceptEdits", can_use_tool=None, session_entry=None):
        self.calls.append({"prompt": prompt, "resume": resume_session_id})
        if self._on_run is not None:
            self._on_run(len(self.calls))
        on_event({"kind": "done", "usage": {}})
        return "cc-1"


def test_stream_drains_steer_queue_and_resumes(monkeypatch):
    """一轮 done 后 steer 队列非空 → 全部排队消息按序合并为【督导补充】prompt，
    以 resume_session_id=cc_sid 原地续跑；队列空才落终态 done + 接续 marker 留痕。"""
    db = _FakeDB(); db.rows["st1"] = {"id": "st1", "status": "running"}
    entry = {"cancel": _threading.Event(), "steer": ["补充一", "补充二"]}
    monkeypatch.setattr(codingmod._sess, "_SESSIONS", {"st1": entry})
    runner = _SteerRunner()
    _run(_stream(db, "st1", "/tmp/p", "原始任务", runner, emit_event=None,
                 cancel=_threading.Event()))
    assert len(runner.calls) == 2
    assert runner.calls[1]["prompt"] == "【督导补充】\n补充一\n补充二"   # 多条合并 + 前缀
    assert runner.calls[1]["resume"] == "cc-1"                          # 同会话同引擎 resume
    assert entry["steer"] == []                                         # 队列已 drain
    assert db.updates[-1][1]["status"] == "done"                        # 队列空才落终态
    texts = [m["text"] for m in db._tables.get("messages", []) if m["role"] == "marker"]
    assert any("督导补充已接续" in t and "2 条" in t for t in texts)


def test_stream_drains_steer_arriving_mid_continuation(monkeypatch):
    """续跑期间新到的 steer 继续入队 → 再续一轮（逐轮 drain 直到队列空才真终态）。"""
    db = _FakeDB(); db.rows["st2"] = {"id": "st2", "status": "running"}
    entry = {"cancel": _threading.Event(), "steer": ["第一轮补充"]}
    monkeypatch.setattr(codingmod._sess, "_SESSIONS", {"st2": entry})

    def _on_run(n):
        if n == 2:
            entry["steer"].append("第二轮补充")      # 模拟续跑进行中 SendTool 新入队

    runner = _SteerRunner(on_run=_on_run)
    _run(_stream(db, "st2", "/tmp/p", "原始任务", runner, emit_event=None,
                 cancel=_threading.Event()))
    assert [c["prompt"] for c in runner.calls] == [
        "原始任务", "【督导补充】\n第一轮补充", "【督导补充】\n第二轮补充"]
    assert db.updates[-1][1]["status"] == "done"


def test_stop_clears_steer_queue_and_stream_does_not_continue(monkeypatch):
    """spec 验收 B：stop 连 steer 队列一起清；cancel 后 _stream drain 循环顶即 break，
    不发生「停了又自己跑起来」；stopped 终态保留不被覆盖。"""
    db = _FakeDB(); db.rows["st3"] = {"id": "st3", "status": "running"}
    cancel = _threading.Event()
    entry = {"cancel": cancel, "steer": ["别跑了"]}
    ok = _stop_session(db, {"st3": entry}, "st3")
    assert ok and cancel.is_set()
    assert entry["steer"] == []                                   # 队列连停清空
    assert db.rows["st3"]["status"] == "stopped"
    # 极端时序兜底：队列又有残留 + cancel 已 set → _stream 也不续跑
    entry["steer"].append("尾巴")
    monkeypatch.setattr(codingmod._sess, "_SESSIONS", {"st3": entry})
    runner = _SteerRunner()
    _run(_stream(db, "st3", "/tmp/p", "原始任务", runner, emit_event=None, cancel=cancel))
    assert len(runner.calls) == 1                                 # 只跑首轮，不续跑
    assert db.updates[-1][1]["status"] == "stopped"


def test_make_tools_reconciles_stale_running(monkeypatch):
    """spec §C：make_tools 对账——sessions 表 running 但内存无活体 entry → interrupted +
    messages 落 marker「底座重启，会话中断，可 send 续跑」；有活体的行与非 running 行不动；
    对账后该会话可直接 send 续跑（验收 C）；db=None 形态静默跳过不炸加载。"""
    db = _FakeDB()
    db.rows["old-run"] = {"id": "old-run", "status": "running", "finished_at": 0,
                          "cwd": "/tmp/p", "cc_session_id": "cc-x"}
    db.rows["live-run"] = {"id": "live-run", "status": "running", "finished_at": 0}
    db.rows["done-1"] = {"id": "done-1", "status": "done"}
    monkeypatch.setattr(codingmod._sess, "_SESSIONS", {"live-run": {"cancel": _threading.Event()}})
    tools = codingmod.make_tools(type("C", (), {"db": db, "emit_event": None})())
    assert tools                                                   # 加载照常
    assert db.rows["old-run"]["status"] == "interrupted"
    assert db.rows["old-run"]["finished_at"] > 0
    marks = [m for m in db._tables.get("messages", [])
             if m["session_id"] == "old-run" and m["role"] == "marker"]
    assert len(marks) == 1 and "底座重启" in marks[0]["text"] and "send 续跑" in marks[0]["text"]
    assert db.rows["live-run"]["status"] == "running"              # 活体不动
    assert db.rows["done-1"]["status"] == "done"                   # 非 running 不动
    # 对账后 interrupted 会话可直接 send 续跑（走既有 resume 链路）
    monkeypatch.setattr(codingmod, "_spawn_stream", lambda *a, **k: None)
    r = SendTool().run({"id": "old-run", "prompt": "续跑"}, _Ctx(db))
    assert r.success, r.error
    # db=None（测试/无 db capability 形态）→ 跳过，不炸
    assert codingmod.make_tools(type("C", (), {"db": None, "emit_event": None})())


def test_c_fallback_loads_main_module_when_unregistered(monkeypatch):
    """回归（2026-08-24 生产「技能执行异常：'coding'」）：生产加载器不挂 sys.modules
    （plugins._import_file），且无人 _sibling("coding")——两名字都不在表中时
    _c() 须现场兜底加载主模块，而不是 KeyError。"""
    import _session_skills

    monkeypatch.delitem(sys.modules, "yibao_plugin_coding_coding", raising=False)
    monkeypatch.delitem(sys.modules, "coding", raising=False)
    mod = _session_skills._c()
    assert hasattr(mod, "make_tools")
    # 兜底经 _sibling 挂表：重复调用拿同一实例
    assert _session_skills._c() is mod
    # 不污染后续用例的 monkeypatch 约定（_c() 优先命中生产名实例）
    sys.modules.pop("yibao_plugin_coding_coding", None)


# ---------- B1：cancel watchdog（「等下一条消息」挂起期中断） ----------
def test_runner_watchdog_interrupts_when_drain_suspended():
    """B1：cancel 在 drain 挂在「等下一条消息」（长工具执行中）期间置位 → watchdog 轮询
    唤醒 interrupt + stopped，不再依赖下一条流事件才检查（中断延迟从无上界收敛到轮询间隔）。"""
    calls = []

    class SuspendedClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def query(self, p): pass
        async def interrupt(self):
            calls.append("interrupt")
            unblock.set()                       # interrupt 解锁挂起的流
        async def receive_response(self):
            yield SimpleNamespace(content=[_FakeText("first")])
            await unblock.wait()                # 模拟长工具执行中：流挂起无消息
            return                              # interrupt 后流尽

    client = SuspendedClient()
    unblock = asyncio.Event()
    events = []

    async def scenario():
        cancel = asyncio.Event()

        async def set_later():
            await asyncio.sleep(0.05)
            cancel.set()
        t = asyncio.ensure_future(set_later())
        await ClaudeCodeRunner(client_factory=lambda *a, **k: client, cancel_poll_s=0.02).run(
            "p", "/tmp", on_event=events.append, cancel_event=cancel)
        await t

    _run(scenario())
    assert calls == ["interrupt"]               # watchdog 路径真杀一次
    kinds = [e["kind"] for e in events]
    assert kinds == ["text_delta", "stopped"]   # 无 done；stopped 恰一条


# ---------- B2：收尾竞态封口（closing 协议） ----------
def test_send_rejects_and_retracts_when_closing(monkeypatch):
    """B2：_stream 收尾前立 closing → SendTool 入队后复读到 closing 自撤并回拒绝，
    不回假 ack（否则消息永不执行、会话落 done，用户被静默吞消息）。"""
    spawned = {"n": 0}
    monkeypatch.setattr(codingmod, "_spawn_stream",
                        lambda *a, **k: spawned.__setitem__("n", spawned["n"] + 1))
    ctx = _make_ctx_with_session(status="running")
    entry = {"cancel": _threading.Event(), "closing": True}   # 收尾意向已立
    monkeypatch.setattr(codingmod._sess, "_SESSIONS", {"sid-1": entry})
    r = SendTool().run({"id": "sid-1", "prompt": "晚到的一句"}, ctx)
    assert r.success is False                                   # 拒绝，不 ack
    assert "收尾" in (r.error or "")
    assert entry["steer"] == []                                 # 入队的消息已自撤


def test_stream_reopens_closing_when_steer_drained(monkeypatch):
    """B2 续跑窗重开：查到排队 steer → closing 复位再续跑（期间新 steer 可继续入队）；
    查空 → closing 保持 True 到线程 pop（收尾窗内 SendTool 一律拒）。"""
    db = _FakeDB(); db.rows["s9"] = {"id": "s9", "status": "running"}

    class SteerOnceRunner:
        """首轮后往 entry 塞一条 steer（模拟收尾查队前一瞬入队的晚到消息）→ 第二轮被 drain。"""
        def __init__(self): self.rounds = 0
        async def run(self, prompt, cwd, *, on_event, cancel_event, resume_session_id=None, **_):
            self.rounds += 1
            if self.rounds == 1:
                entry = _sess_module._SESSIONS.get("s9")
                entry.setdefault("steer", []).append("晚到督导")
                on_event({"kind": "done"})
                return "cc-1"
            assert "督导补充" in prompt                          # 晚到消息被合并续跑
            on_event({"kind": "done"})
            return "cc-1"

    _sess_module = codingmod._sess
    entry = {"cancel": _threading.Event()}
    monkeypatch.setattr(_sess_module, "_SESSIONS", {"s9": entry})
    runner = SteerOnceRunner()
    _run(_stream(db, "s9", "/tmp/p", "hi", runner, emit_event=None, cancel=_threading.Event()))
    assert runner.rounds == 2
    assert entry.get("closing") is True                         # 最终收尾：closing 保持立起
    assert db.updates[-1][1]["status"] == "done"


# ---------- B4：权限裁决 TOCTOU ----------
def test_perm_timeout_racing_decide_still_honors_allow():
    """B4：超时醒来前裁决已写入（event 未及 set 的相撞窗）→ waiter pop 前复读 entry
    按允许收场，不误判 timeout（用户看到「已允许」= 实际允许）。"""
    import _runner as R
    rid_holder = {}

    def on_event(e):
        if e.get("kind") == "permission_request":
            rid_holder["rid"] = e["rid"]
            R._PERM[e["rid"]]["allow"] = True   # 裁决先落，event 未 set（相撞窗）

    async def scenario():
        cb = R.make_permission_callback("s-race", on_event, timeout_s=0.01)
        result = await cb("Bash", {"command": "ls"})
        return result

    result = _run(scenario())
    assert "Allow" in type(result).__name__     # 按允许收场（旧行为：超时 deny）
    assert rid_holder["rid"] not in R._PERM     # 注册表已清理


def test_decide_rejects_already_decided_entry():
    """B4：已裁决（含 stop 放行 deny）的 entry 不再被覆盖——先到先得，不回假成功。"""
    from coding import DecideTool
    # 经 coding 模块别名拿生产注册表（测试直接 import _runner 会得到另一个实例）
    perm = codingmod._PERM
    perm["perm_s-x_1"] = {"event": _threading.Event(), "allow": False,
                          "tool": "Bash", "summary": "", "params": {}, "created_at": 0}
    try:
        res = DecideTool().run({"rid": "perm_s-x_1", "allow": True}, ctx=None)
        assert res.success is False
        assert "已被裁决" in (res.error or "")
        assert perm["perm_s-x_1"]["allow"] is False              # 未被覆盖成 True
    finally:
        perm.pop("perm_s-x_1", None)


# ---------- B5：流终残留 rewind pending 兜底 ----------
def test_stream_reports_unexecuted_rewind_pending(monkeypatch):
    """B5：本轮在最后一条消息后收尾（pending 无消费点）→ 补 marker 讲实话，
    不再「回执成功却未执行」。mode_pending 静默清（库值已更新，下轮生效）。"""
    db = _FakeDB(); db.rows["s5"] = {"id": "s5", "status": "running"}
    entry = {"cancel": _threading.Event(), "rewind_pending": "uuid-1", "mode_pending": "plan"}
    monkeypatch.setattr(codingmod._sess, "_SESSIONS", {"s5": entry})
    events = []
    runner = _FakeRunner(cc_sid="cc-5")
    _run(_stream(db, "s5", "/tmp/p", "hi", runner, emit_event=events.append,
                 cancel=_threading.Event()))
    marks = [e["payload"]["data"]["event"]["text"] for e in events
             if e.get("kind") == "panel_data"
             and e["payload"]["data"]["event"].get("kind") == "marker"]
    assert any("回滚未执行" in t for t in marks)
    assert "rewind_pending" not in entry and "mode_pending" not in entry


# ---------- B6：线程启动失败半态收口 ----------
def test_spawn_stream_thread_start_failure_lands_failed(monkeypatch):
    """B6：Thread.start 抛（资源耗尽等）→ 落 failed + 终态汇报，不永久卡 running。"""
    db = _FakeDB()
    sid = codingmod.start_session(db, agent="claude-code", cwd="/tmp/p", prompt="hi")

    class _BoomThread:
        def __init__(self, *a, **k): pass
        def start(self): raise RuntimeError("can't start new thread")

    monkeypatch.setattr(codingmod._sess.threading, "Thread", _BoomThread)
    events = []
    codingmod._sess._spawn_stream(db, sid, "/tmp/p", "hi", object(), events.append)
    assert db.rows[sid]["status"] == "failed"
    reminders = [e for e in events if e.get("kind") == "reminder"]
    assert reminders and "失败" in reminders[0]["text"]


# ---------- B7：终态覆盖缝（done 写后复核 cancel） ----------
def test_stream_corrects_done_to_stopped_when_cancel_late():
    """B7：读态 running → 写 done 之间用户 stop 完成（先落 stopped 后 set cancel）→
    写后复核 cancel 撞上即纠正回 stopped + 补 stopped 终态事件，不被 done 覆盖。"""
    cancel = _threading.Event()

    class _CancelOnFinalUpdateDB(_FakeDB):
        def update(self, table, rid, fields):
            super().update(table, rid, fields)
            if table == "sessions" and fields.get("status") in ("done", "failed"):
                cancel.set()   # 模拟 stop 的 set cancel 恰在我们的终态写入后、复核前被观察到

    db = _CancelOnFinalUpdateDB()
    db.rows["s7"] = {"id": "s7", "status": "running"}
    events = []
    _run(_stream(db, "s7", "/tmp/p", "hi", _FakeRunner(cc_sid="cc-7"),
                 emit_event=events.append, cancel=cancel))
    assert db.rows["s7"]["status"] == "stopped"                 # 纠正回 stopped
    stopped_events = [e for e in events if e.get("kind") == "panel_data"
                      and e["payload"]["data"]["event"].get("kind") == "stopped"]
    assert stopped_events                                       # 面板收到 stopped 终态
    reports = [e for e in events if e.get("kind") == "event"]
    assert reports and "已停止" in reports[0]["text"]           # 汇报按 stopped（非 ✅ 完成）
