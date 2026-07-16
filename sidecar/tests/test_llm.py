from yibao_brain.llm import GLMProvider, FakeProvider, LLMResponse, ToolCall


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
