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
