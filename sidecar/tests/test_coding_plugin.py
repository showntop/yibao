"""coding 插件：runner 流式/取消/容错（FakeSDK 注入，不跑真 SDK）。"""
from __future__ import annotations
import asyncio, os, sys
from types import SimpleNamespace
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# 插件 skills 不在 src 下，单独加路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugins", "coding", "skills"))
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
    """极小鸭式 db：记录 insert/update，query 全量返回。"""
    def __init__(self): self.rows = {}; self.updates = []
    def insert(self, table, row): self.rows[row["id"]] = dict(row); return row["id"]
    def update(self, table, rid, fields): self.updates.append((rid, fields)); self.rows.setdefault(rid, {}).update(fields)
    def query(self, *a, **k): return list(self.rows.values())


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
    async def run(self, prompt, cwd, *, on_event, cancel_event, resume_session_id=None):
        self.called_with = {"prompt": prompt, "cwd": cwd, "resume": resume_session_id}
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


def test_stream_stores_empty_cc_session_id_when_runner_returns_none():
    """runner 返回 None（取消/失败）时，cc_session_id 落 ""。"""
    db = _FakeDB(); db.rows["s2"] = {"id": "s2", "status": "running"}
    runner = _FakeRunner(cc_sid=None)
    cancel = _threading.Event()
    _run(_stream(db, "s2", "/tmp/p", "hi", runner, emit_event=None, cancel=cancel))
    last = db.updates[-1][1]
    assert last["cc_session_id"] == ""


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
from coding import SendSkill, StartSkill  # noqa: E402


class _Ctx:
    """最小 ctx 鸭式：db + emit_event（SendSkill.run 只用这俩）。"""
    def __init__(self, db): self.db = db; self.emit_event = lambda *a, **k: None


def test_send_skill_resumes_with_cc_session_id(monkeypatch):
    """cc_session_id 非空 → _spawn_stream 收到 resume_session_id=cc。"""
    db = _FakeDB()
    db.rows["s1"] = {"id": "s1", "cwd": "/tmp/p", "cc_session_id": "cc-old-1"}
    captured = {}
    monkeypatch.setattr(
        codingmod, "_spawn_stream",
        lambda *a, **k: captured.update({"args": a, "kwargs": k}))
    res = SendSkill().run({"id": "s1", "prompt": "再来一轮"}, _Ctx(db))
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
    res = SendSkill().run({"id": "ghost", "prompt": "x"}, _Ctx(db))
    assert res.success is False and "不存在" in res.error
    assert called["n"] == 0


def test_send_skill_empty_cc_session_id_errors(monkeypatch):
    """cc_session_id 为空 → 友好错误，提示先首条开始，不调 _spawn_stream。"""
    db = _FakeDB()
    db.rows["s2"] = {"id": "s2", "cwd": "/tmp/p", "cc_session_id": ""}
    called = {"n": 0}
    monkeypatch.setattr(codingmod, "_spawn_stream",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    res = SendSkill().run({"id": "s2", "prompt": "x"}, _Ctx(db))
    assert res.success is False and "尚未建立" in res.error
    assert called["n"] == 0


def test_send_skill_missing_prompt_errors(monkeypatch):
    """缺少 prompt → 早退报错，不查库、不调 _spawn_stream。"""
    db = _FakeDB()
    called = {"n": 0}
    monkeypatch.setattr(codingmod, "_spawn_stream",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    res = SendSkill().run({"id": "s1"}, _Ctx(db))   # 无 prompt
    assert res.success is False and "prompt" in res.error
    assert called["n"] == 0


def test_send_skill_openai_schema_shape():
    schema = SendSkill().openai_schema()
    assert schema["function"]["name"] == "coding.send"
    props = schema["function"]["parameters"]["properties"]
    assert set(props.keys()) == {"id", "prompt"}
    assert schema["function"]["parameters"]["required"] == ["id", "prompt"]


def test_make_tools_includes_send():
    """make_tools 返回 Start/Send/Stop/List + HandoffList/HandoffBrief/History 七件。"""
    tools = codingmod.make_tools(type("C", (), {"db": None, "emit_event": None})())
    ids = [t.id for t in tools]
    assert "coding.send" in ids
    assert ids == ["coding.start", "coding.send", "coding.stop", "coding.list",
                   "coding.handoff_list", "coding.handoff_brief", "coding.history"]


def test_start_skill_does_not_pass_resume(monkeypatch):
    """回归保护：StartSkill 调 _spawn_stream 不传 resume_session_id（fresh）。"""
    db = _FakeDB()
    captured = {}
    monkeypatch.setattr(codingmod, "_spawn_stream",
                        lambda *a, **k: captured.update({"kwargs": k}))
    monkeypatch.setattr(codingmod, "ClaudeCodeRunner", lambda: object())
    StartSkill().run({"cwd": "/tmp", "prompt": "p"}, _Ctx(db))
    assert captured["kwargs"].get("resume_session_id") is None


# ---------- 流式防重入：send 拒 running 会话 ----------


def _make_ctx_with_session(status):
    """仿 _Ctx/_FakeDB 既有约定：造一个带指定 status 会话（sid-1）的 ctx。"""
    db = _FakeDB()
    db.rows["sid-1"] = {"id": "sid-1", "cwd": "/tmp/p",
                        "cc_session_id": "cc-old-1", "status": status}
    return _Ctx(db)


def test_send_rejects_when_session_running(monkeypatch):
    """会话 running 时 send 必须拒绝（防同 sid 双 runner 竞态）；终态会话放行。"""
    # 不真起线程：把 _spawn_stream 占位成空
    monkeypatch.setattr(codingmod, "_spawn_stream", lambda *a, **k: None)
    ctx = _make_ctx_with_session(status="running")
    r = SendSkill().run({"id": "sid-1", "prompt": "再来一条"}, ctx)
    assert not r.success and "正在运行" in (r.error or "")
    ctx2 = _make_ctx_with_session(status="done")
    r2 = SendSkill().run({"id": "sid-1", "prompt": "再来一条"}, ctx2)
    assert r2.success, r2.error


# ---------- start/send 风险降噪：L2 → L1 ----------
from yibao_brain.ipc import RiskLevel  # noqa: E402


def test_start_send_are_l1_no_confirm():
    """会话启动/续聊 = L1（直调不弹确认）：文件改动已由 SDK permission_mode=acceptEdits 管理，
    高频对话循环不该每次弹风险确认。"""
    assert StartSkill.default_risk == RiskLevel.L1_LOW
    assert SendSkill.default_risk == RiskLevel.L1_LOW


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
from coding import StopSkill  # noqa: E402


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
    monkeypatch.setattr(codingmod, "_SESSIONS", {})   # 无 live runner
    ctx = _EmitCtx(db)
    res = StopSkill().run({"id": "s-stale"}, ctx)
    assert res.success is True
    assert db.rows["s-stale"]["status"] == "stopped"          # db 已落 stopped
    term = [e for e in ctx.events
            if e.get("kind") == "panel_data"
            and e["payload"]["data"]["event"].get("kind") == "stopped"]
    assert term, ctx.events
    assert term[0]["payload"]["panel"] == "coding:chat"
    assert term[0]["payload"]["data"]["session_id"] == "s-stale"


def test_stop_with_live_runner_no_extra_terminal(monkeypatch):
    """有 live runner：_stop_session 走正常 cancel（runner 取消路径自发 stopped），
    StopSkill 不补发——否则面板「已中断」marker 翻倍。"""
    db = _FakeDB()
    db.rows["s-live"] = {"id": "s-live", "status": "running"}
    cancel = _threading.Event()
    monkeypatch.setattr(codingmod, "_SESSIONS", {"s-live": {"cancel": cancel}})
    ctx = _EmitCtx(db)
    res = StopSkill().run({"id": "s-live"}, ctx)
    assert res.success is True and cancel.is_set()
    assert ctx.events == []     # 不重复补发


def test_send_rejects_when_runner_finishing(monkeypatch):
    """check-then-act 缝：db=stopped 但 _SESSIONS 仍有 live entry（runner 卡长工具）→ 拒；
    entry 退清后放行。"""
    called = {"n": 0}
    monkeypatch.setattr(codingmod, "_spawn_stream",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    ctx = _make_ctx_with_session(status="stopped")   # sid-1：db 已 terminal
    monkeypatch.setattr(codingmod, "_SESSIONS", {"sid-1": {"cancel": _threading.Event()}})
    r = SendSkill().run({"id": "sid-1", "prompt": "再来一条"}, ctx)
    assert not r.success and "收尾" in (r.error or "")
    assert called["n"] == 0
    monkeypatch.setattr(codingmod, "_SESSIONS", {})   # runner 线程退清 → 放行
    r2 = SendSkill().run({"id": "sid-1", "prompt": "再来一条"}, ctx)
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
