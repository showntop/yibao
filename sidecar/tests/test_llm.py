import asyncio

from yibao_brain.llm import (
    GLMProvider,
    FakeProvider,
    LLMResponse,
    ToolCall,
    merge_tool_call_deltas,
    ToolCallDelta,
    _vision_create_with_retry,
    parse_observe,
)


def test_vision_create_retries_on_timeout_then_succeeds():
    calls = []

    def fn():
        calls.append(1)
        if len(calls) < 3:
            raise TimeoutError("transient")
        return "ok"

    out = _vision_create_with_retry(fn, retries=2, base_delay=0)  # base_delay=0 不真睡
    assert out == "ok"
    assert len(calls) == 3  # 初试 1 + 重试 2


def test_vision_create_retries_exhausted_reraises():
    calls = []

    def fn():
        calls.append(1)
        raise TimeoutError("transient")

    try:
        _vision_create_with_retry(fn, retries=1, base_delay=0)
        raise AssertionError("应抛出 TimeoutError")
    except TimeoutError:
        pass
    assert len(calls) == 2  # 初试 + 1 次重试后放弃


def test_vision_create_non_retryable_reraises_immediately():
    calls = []

    class Boom(Exception):
        pass

    def fn():
        calls.append(1)
        raise Boom("not a network error")

    try:
        _vision_create_with_retry(fn, retries=2, base_delay=0)
        raise AssertionError("应抛出 Boom")
    except Boom:
        pass
    assert len(calls) == 1  # 非网络类错误不重试，立即抛出


def test_parse_observe_requires_strict_boolean_and_bounded_text():
    assert parse_observe('{"speak": "false", "text": "别说话"}') is None
    assert parse_observe('{"speak": true, "text": ""}') is None
    parsed = parse_observe('前缀 {"speak": true, "text": "  这是一个很长很长很长很长很长的建议  "} 后缀')
    assert parsed == {"speak": True, "text": "这是一个很长很长很长很长很长的建议"[:20]}
    assert parse_observe('{"speak": false, "text": "不应保留"}') == {"speak": False, "text": ""}


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


def test_glm_chat_forwards_timeout_only_when_given():
    # Distiller 离线调用显式传 60s 上限；不传时不下发，保持主对话回路 SDK 默认行为
    captured = {}

    class FakeMsg:
        content = "ok"
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
                    captured.update(kw)
                    return FakeResp()

    p = GLMProvider(api_key="x", model="glm-4.6", client_factory=FakeClient)
    p.chat(messages=[{"role": "user", "content": "hi"}])
    assert "timeout" not in captured
    p.chat(messages=[{"role": "user", "content": "hi"}], timeout=60)
    assert captured["timeout"] == 60


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


def test_glm41v_thinking_normalized_box_is_converted_to_image_pixels(tmp_path):
    """GLM-4.1V-Thinking 的 0..1000 grounding 坐标要还原成截图物理像素。"""
    import base64

    from PIL import Image

    from yibao_brain.llm import ComputerUseClient

    shot = tmp_path / "shot.png"
    Image.new("RGB", (760, 520), "white").save(shot)
    image_b64 = "data:image/png;base64," + base64.b64encode(shot.read_bytes()).decode()

    class FakeMsg:
        content = '{"action":"click","box":[883,30,936,93]}'

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

    client = ComputerUseClient(
        api_key="x",
        model="glm-4.1v-thinking-flashx",
        base_url="https://open.bigmodel.cn/api/paas/v4/",
        client_factory=FakeClient,
    )

    assert client.prefers_raw_bbox is True
    action = client.next_action(image_b64, "点击帮助按钮")

    assert action == {
        "action": "click",
        "box": [671.08, 15.6, 711.36, 48.36],
    }


def test_computer_use_uses_separate_glm_vision_provider(monkeypatch):
    from yibao_brain import config
    from yibao_brain.llm import ComputerUseClient

    monkeypatch.setenv("YIBAO_LLM_API_KEY", "deepseek-key")
    monkeypatch.setenv("YIBAO_LLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("YIBAO_VISION_API_KEY", "vision-key")
    monkeypatch.setenv("YIBAO_VISION_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/")

    seen = {}

    class FakeClient:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    ComputerUseClient(client_factory=FakeClient)

    assert config.vision_api_key() == "vision-key"
    assert config.vision_base_url() == "https://open.bigmodel.cn/api/paas/v4/"
    assert config.computer_use_enabled() is True
    assert seen == {
        "api_key": "vision-key",
        "base_url": "https://open.bigmodel.cn/api/paas/v4/",
    }


def test_computer_use_vision_provider_falls_back_to_main(monkeypatch):
    from yibao_brain import config

    monkeypatch.setenv("YIBAO_LLM_API_KEY", "glm-key")
    monkeypatch.setenv("YIBAO_LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/")
    monkeypatch.delenv("YIBAO_VISION_API_KEY", raising=False)
    monkeypatch.delenv("YIBAO_VISION_BASE_URL", raising=False)
    monkeypatch.delenv("YIBAO_GLM_API_KEY", raising=False)
    monkeypatch.delenv("YIBAO_GLM_BASE_URL", raising=False)

    assert config.vision_api_key() == "glm-key"
    assert config.vision_base_url() == "https://open.bigmodel.cn/api/paas/v4/"
    assert config.computer_use_enabled() is True

    monkeypatch.setenv("YIBAO_LLM_BASE_URL", "https://api.deepseek.com")
    assert config.computer_use_enabled() is False



def test_choose_action_uses_low_temperature():
    from yibao_brain.llm import ComputerUseClient

    captured = {}

    class FakeResp:
        choices = [type("C", (), {"message": type("M", (), {"content": "3"})()})()]

    class FakeClient:
        def __init__(self, **kw):
            pass

        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    captured.update(kw)
                    return FakeResp()

    c = ComputerUseClient(api_key="x", model="glm-4.6v-flash",
                          base_url="https://open.bigmodel.cn/api/paas/v4/",
                          client_factory=FakeClient)
    action = c.choose_action("data:image/jpeg;base64,x", "点按钮", 5, [])
    assert action == {"action": "click", "mark": 3}
    assert captured.get("temperature") == 0.1


def test_parse_marked_action_zoom_letter():
    from yibao_brain.llm import ComputerUseClient

    assert ComputerUseClient._parse_marked_action("B", 5, 6) == {"action": "zoom", "zone": "B"}
    assert ComputerUseClient._parse_marked_action("B.", 5, 6) == {"action": "zoom", "zone": "B"}
    assert ComputerUseClient._parse_marked_action('{"action":"zoom","zone":"C"}', 5, 6) == {"action": "zoom", "zone": "C"}
    assert ComputerUseClient._parse_marked_action("G", 5, 6) is None   # 超出 A-F
    assert ComputerUseClient._parse_marked_action("B", 5, 0) is None   # 无区域轨
    assert ComputerUseClient._parse_marked_action("3", 5, 6) == {"action": "click", "mark": 3}
    assert ComputerUseClient._parse_marked_action('{"action":"zoom","zone":"Z"}', 5, 6) is None


def test_choose_action_prompt_mentions_zones():
    from yibao_brain.llm import ComputerUseClient

    captured = {}

    class FakeResp:
        choices = [type("C", (), {"message": type("M", (), {"content": "B"})()})()]

    class FakeClient:
        def __init__(self, **kw):
            pass

        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    captured.update(kw)
                    return FakeResp()

    c = ComputerUseClient(api_key="x", model="glm-4.6v-flash",
                          base_url="https://open.bigmodel.cn/api/paas/v4/",
                          client_factory=FakeClient)
    action = c.choose_action("data:image/jpeg;base64,x", "点按钮", 5, [], n_zones=6)
    assert action == {"action": "zoom", "zone": "B"}
    user_text = captured["messages"][-1]["content"][-1]["text"]
    assert "字母区域" in user_text and "A-F" in user_text


def test_describe_screen_returns_text_and_prompt_asks_window_list():
    captured = {}

    class FakeResp:
        choices = [type("C", (), {"message": type("M", (), {"content": "终端(左，代码)；译宝(右上，桌宠)"})()})()]

    class FakeClient:
        def __init__(self, **kw):
            pass

        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    captured.update(kw)
                    return FakeResp()

    from yibao_brain.llm import ComputerUseClient, describe_screen

    c = ComputerUseClient(api_key="x", model="glm-4.6v-flash",
                          base_url="https://open.bigmodel.cn/api/paas/v4/",
                          client_factory=FakeClient)
    desc = describe_screen(c, "data:image/png;base64,x")
    assert desc == "终端(左，代码)；译宝(右上，桌宠)"
    sys_prompt = captured["messages"][0]["content"]
    assert "窗口" in sys_prompt and "遗漏" in sys_prompt


def test_describe_screen_failure_returns_none():
    class BoomClient:
        def __init__(self, **kw):
            pass

        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    raise RuntimeError("api down")

    from yibao_brain.llm import ComputerUseClient, describe_screen

    c = ComputerUseClient(api_key="x", model="m", base_url="https://x", client_factory=BoomClient)
    assert describe_screen(c, "data:image/png;base64,x") is None


def test_summarize_screen_prompt_and_result():
    captured = {}

    class FakeResp:
        choices = [type("C", (), {"message": type("M", (), {"content": "VS Code 编辑 App.vue，左侧文件树"})()})()]

    class FakeClient:
        def __init__(self, **kw):
            pass

        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    captured.update(kw)
                    return FakeResp()

    from yibao_brain.llm import ComputerUseClient, summarize_screen

    c = ComputerUseClient(api_key="x", model="m", base_url="https://x", client_factory=FakeClient)
    out = summarize_screen(c, "data:image/png;base64,x")
    assert out == "VS Code 编辑 App.vue，左侧文件树"
    assert "概括" in captured["messages"][0]["content"]


def test_summarize_screen_failure_returns_none():
    class BoomClient:
        def __init__(self, **kw):
            pass

        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    raise RuntimeError("api down")

    from yibao_brain.llm import ComputerUseClient, summarize_screen

    c = ComputerUseClient(api_key="x", model="m", base_url="https://x", client_factory=BoomClient)
    assert summarize_screen(c, "data:image/png;base64,x") is None
