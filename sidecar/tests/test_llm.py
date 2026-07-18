import asyncio

from yibao_brain.llm import (
    GLMProvider,
    FakeProvider,
    LLMResponse,
    ToolCall,
    merge_tool_call_deltas,
    ToolCallDelta,
)


def test_tool_call_fields():
    tc = ToolCall(id="t1", skill_id="echo", params={"text": "x"})
    assert tc.id == "t1" and tc.skill_id == "echo" and tc.params == {"text": "x"}


def test_fake_provider_returns_canned():
    p = FakeProvider(text="ok", tool_calls=[ToolCall(id="t1", skill_id="echo", params={"text": "hi"})])
    resp = p.chat(messages=[{"role": "user", "content": "hi"}])
    assert resp.text == "ok"
    assert resp.tool_calls[0].skill_id == "echo"


def test_glm_provider_parses_openai_response():
    # 用假 client 注入，避免真实联网
    class FakeMsg:
        content = "hello"
        tool_calls = None

    class FakeChoice:
        message = FakeMsg()

    class FakeResp:
        choices = [FakeChoice()]

    class FakeClient:
        def __init__(self, **kw):
            pass

        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    return FakeResp()

    p = GLMProvider(api_key="x", model="glm-4.6", client_factory=FakeClient)
    resp = p.chat(messages=[{"role": "user", "content": "hi"}])
    assert resp.text == "hello"
    assert resp.tool_calls == []


def test_merge_tool_call_deltas_accumulates_arguments():
    # 同一 index 的 arguments 片段要拼接后解析成 params
    deltas = [
        ToolCallDelta(index=0, id="c1", skill_id="echo", arguments='{"text": "h'),
        ToolCallDelta(index=0, arguments='i"}'),
    ]
    out = merge_tool_call_deltas(deltas)
    assert len(out) == 1
    assert out[0].id == "c1"
    assert out[0].skill_id == "echo"
    assert out[0].params == {"text": "hi"}


def test_fake_provider_astream_yields_text_chunks():
    p = FakeProvider(chunks=["你", "好", "呀"])
    deltas = asyncio.run(_collect(p.astream([{"role": "user", "content": "hi"}])))
    assert [d.text for d in deltas] == ["你", "好", "呀"]
    assert p.astream_calls and p.astream_calls[0]["messages"][0]["content"] == "hi"


def test_fake_provider_astream_tool_calls_one_shot():
    p = FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="echo", params={"text": "hi"})])
    deltas = asyncio.run(_collect(p.astream([{"role": "user", "content": "hi"}])))
    assert len(deltas) == 1
    tcs = merge_tool_call_deltas(deltas[0].tool_call_deltas)
    assert tcs[0].skill_id == "echo" and tcs[0].params == {"text": "hi"}


def test_glm_provider_astream_parses_stream():
    # 假 AsyncClient：create(stream=True) 返回异步 chunk 迭代器
    class FakeDelta:
        def __init__(self, content=None, tcs=None):
            self.content = content
            self.tool_calls = tcs

    class FakeChoice:
        def __init__(self, delta):
            self.delta = delta

    class FakeChunk:
        def __init__(self, delta):
            self.choices = [FakeChoice(delta)]

    class FakeFn:
        def __init__(self, name="", arguments=""):
            self.name = name
            self.arguments = arguments

    class FakeTC:
        def __init__(self, index, id="", fn=None):
            self.index = index
            self.id = id
            self.function = fn

    async def _chunks():
        yield FakeChunk(FakeDelta(content="hel"))
        yield FakeChunk(FakeDelta(content="lo"))
        yield FakeChunk(
            FakeDelta(
                tcs=[
                    FakeTC(0, id="c1", fn=FakeFn(name="echo", arguments='{"text":"hi"}')),
                ]
            )
        )

    class FakeAsyncCompletions:
        @staticmethod
        async def create(**kw):
            assert kw.get("stream") is True
            return _chunks()

    class FakeAsyncChat:
        completions = FakeAsyncCompletions()

    class FakeAsyncClient:
        def __init__(self, **kw):
            self.chat = FakeAsyncChat()

    p = GLMProvider(api_key="x", model="glm-4.6", async_client_factory=FakeAsyncClient)
    deltas = asyncio.run(_collect(p.astream([{"role": "user", "content": "hi"}])))
    text = "".join(d.text for d in deltas)
    all_tcs = merge_tool_call_deltas([d for dl in deltas for d in dl.tool_call_deltas])
    assert text == "hello"
    assert all_tcs[0].skill_id == "echo" and all_tcs[0].params == {"text": "hi"}


async def _collect(ait):
    out = []
    async for d in ait:
        out.append(d)
    return out


def test_computer_use_thinking_via_extra_body():
    # GLM 的 thinking 参数必须走 extra_body（openai SDK 不认顶层 kwargs）
    from yibao_brain.llm import ComputerUseClient

    seen = {}

    class FakeMsg:
        content = '{"action":"finish"}'

    class FakeChoice:
        message = FakeMsg()

    class FakeResp:
        choices = [FakeChoice()]

    class FakeClient:
        def __init__(self, **kw):
            pass

        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    seen.update(kw)
                    return FakeResp()

    c = ComputerUseClient(api_key="x", model="glm-4.6v-flash", base_url="https://open.bigmodel.cn/api/paas/v4/", client_factory=FakeClient)
    assert c.next_action("data:image/png;base64,x", "任务") == {"action": "finish"}
    assert "thinking" not in seen
    assert seen["extra_body"] == {"thinking": {"type": "enabled"}}


def test_computer_use_enabled_only_for_glm(monkeypatch):
    from yibao_brain import config

    monkeypatch.setenv("YIBAO_LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/")
    monkeypatch.delenv("YIBAO_GLM_BASE_URL", raising=False)
    assert config.computer_use_enabled() is True
    monkeypatch.setenv("YIBAO_LLM_BASE_URL", "https://api.deepseek.com")
    assert config.computer_use_enabled() is False
