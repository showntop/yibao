"""GLM astream 的 usage 抓取：OpenAI 兼容端点的 usage chunk 带非空 choices 也能抓到。"""
import asyncio

import pytest


class _Chunk:
    """OpenAI SDK 流式 chunk 的最小形状模拟。"""

    def __init__(self, choices, usage=None):
        self.choices = choices
        self.usage = usage


class _Choice:
    def __init__(self, delta, finish_reason=None):
        self.delta = delta
        self.finish_reason = finish_reason


class _Delta:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class _Stream:
    def __init__(self, chunks):
        self._chunks = iter(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._chunks)
        except StopIteration:
            raise StopAsyncIteration


class _Usage:
    def __init__(self, prompt, completion, total, cached=0):
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.total_tokens = total
        self.cached_tokens = cached
        self.prompt_tokens_details = None


def test_usage_chunk_with_nonempty_choices(tmp_path):
    """关键回归：usage chunk 的 choices 是 [{delta:{}, finish_reason:stop}]（非空）——
    旧实现只在 choices 空时查 usage，导致漏抓 → token 恒 0。"""

    from yibao_brain.llm import GLMProvider, LLMDelta

    # 模拟真实 OpenAI 兼容响应：
    #  - 正常文本 chunk（带 choices，无 usage）
    #  - 末尾 usage chunk（choices 非空但 delta 空 + finish_reason=stop + usage）
    chunks = [
        _Chunk([_Choice(_Delta(content="你好"))]),
        _Chunk([_Choice(_Delta(content="，世界"))]),
        _Chunk([_Choice(_Delta(), finish_reason="stop")], _Usage(prompt=120, completion=30, total=150)),
    ]

    provider = GLMProvider(api_key="test", model="glm-4.6", base_url="http://test")
    provider._async_client = _FakeAsyncClient(_Stream(chunks))
    provider._async_creds = ("test", "http://test")

    async def go():
        out: list[LLMDelta] = []
        async for d in provider.astream([{"role": "user", "content": "hi"}]):
            out.append(d)
        return out

    deltas = asyncio.run(go())
    # stream_options.include_usage 必须下发（否则端点不会回 usage chunk）
    assert provider._async_client._last_kwargs["stream_options"] == {"include_usage": True}
    texts = "".join(d.text for d in deltas)
    assert texts == "你好，世界"
    # usage 被抓到且只 yield 一次
    usages = [d for d in deltas if d.usage is not None]
    assert len(usages) == 1
    assert usages[0].usage.prompt_tokens == 120
    assert usages[0].usage.completion_tokens == 30
    assert usages[0].usage.total_tokens == 150


def test_usage_yielded_only_once(tmp_path):
    """多 chunk 重复带 usage（累计值）→ 只 yield 一次（防 loop 重复累加）。"""

    from yibao_brain.llm import GLMProvider

    chunks = [
        _Chunk([_Choice(_Delta(content="a"))], _Usage(prompt=10, completion=5, total=15)),
        _Chunk([_Choice(_Delta(content="b"))], _Usage(prompt=10, completion=5, total=15)),
        _Chunk([_Choice(_Delta(), finish_reason="stop")], _Usage(prompt=10, completion=5, total=15)),
    ]
    provider = GLMProvider(api_key="test", model="glm-4.6", base_url="http://test")
    provider._async_client = _FakeAsyncClient(_Stream(chunks))
    provider._async_creds = ("test", "http://test")

    async def go():
        out = []
        async for d in provider.astream([{"role": "user", "content": "hi"}]):
            out.append(d)
        return out

    deltas = asyncio.run(go())
    usages = [d for d in deltas if d.usage is not None]
    assert len(usages) == 1
    assert usages[0].usage.total_tokens == 15
    assert "".join(d.text for d in deltas) == "ab"


class _FakeAsyncClient:
    """模拟 AsyncOpenAI 的 client.chat.completions.create 返回流。"""

    def __init__(self, stream):
        self._stream = stream
        self._last_kwargs = None
        self.chat = _Chat(self)

    async def _create(self, **kwargs):
        self._last_kwargs = kwargs
        return self._stream


class _Chat:
    def __init__(self, client):
        self.completions = _Completions(client)


class _Completions:
    def __init__(self, client):
        self._client = client

    def create(self, **kwargs):
        return self._client._create(**kwargs)
