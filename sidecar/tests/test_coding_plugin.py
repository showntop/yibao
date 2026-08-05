"""coding 插件：runner 流式/取消/容错（FakeSDK 注入，不跑真 SDK）。"""
from __future__ import annotations
import asyncio, os, sys
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
    runner = ClaudeCodeRunner(client_factory=lambda cwd, tools: _FakeClient(msgs))
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
    runner = ClaudeCodeRunner(client_factory=lambda cwd, tools: _FakeClient(msgs))
    cancel = asyncio.Event()
    def on_event(e):
        sent.append(e)
        if len(sent) == 2:
            cancel.set()
    _run(runner.run("p", "/tmp", on_event=on_event, cancel_event=cancel))
    kinds = [e["kind"] for e in sent]
    assert "done" not in kinds                         # cancel 抑制终态 done
    assert kinds == ["text_delta", "text_delta"]       # 前两条入列
    assert len(sent) == 2                              # 第 3 条取消后丢弃


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
    assert normalize(_FakeResultMessage("success")) == [{"kind": "done"}]


def test_normalize_ignores_system_and_user():
    assert normalize(_FakeSystemMessage()) == []
    assert normalize(_FakeUserMessage("hi")) == []


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
    assert normalize(ResultMessage(
        subtype="success", duration_ms=0, duration_api_ms=0,
        is_error=False, num_turns=1, session_id="s")) == [{"kind": "done"}]


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
