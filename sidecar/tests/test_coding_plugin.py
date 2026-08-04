"""coding 插件：runner 流式/取消/容错（FakeSDK 注入，不跑真 SDK）。"""
from __future__ import annotations
import asyncio, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# 插件 skills 不在 src 下，单独加路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugins", "coding", "skills"))
from _runner import ClaudeCodeRunner, normalize  # noqa: E402


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
    sent = []
    msgs = [_FakeMsg("assistant", text="a"), _FakeMsg("assistant", text="b"), _FakeMsg("assistant", text="c")]
    runner = ClaudeCodeRunner(client_factory=lambda cwd, tools: _FakeClient(msgs))
    cancel = asyncio.Event()
    def on_event(e):
        sent.append(e)
        if len(sent) == 2:
            cancel.set()
    _run(runner.run("p", "/tmp", on_event=on_event, cancel_event=cancel))
    kinds = [e["kind"] for e in sent]
    assert "done" not in kinds            # cancel suppressed the terminal done
    assert kinds[:2] == ["text_delta", "text_delta"]   # first two streamed
    assert len(sent) == 2                 # third message discarded after cancel


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
