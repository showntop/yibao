"""LLM provider 抽象 + GLM(智谱) 实现 + 测试用 Fake。"""
from __future__ import annotations

import json
from typing import Protocol

from pydantic import BaseModel, Field

from .config import glm_api_key, glm_base_url, glm_model


class ToolCall(BaseModel):
    id: str
    skill_id: str
    params: dict = Field(default_factory=dict)


class LLMResponse(BaseModel):
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)


class LLMProvider(Protocol):
    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse: ...


class FakeProvider:
    """测试用：返回预设响应。"""

    def __init__(self, text: str = "", tool_calls: list[ToolCall] | None = None):
        self._text = text
        self._tool_calls = tool_calls or []
        self.calls: list[dict] = []

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        self.calls.append({"messages": messages, "tools": tools})
        return LLMResponse(text=self._text, tool_calls=list(self._tool_calls))


class GLMProvider:
    """智谱 GLM，走 OpenAI-兼容端点。client_factory 注入便于测试。"""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        client_factory=None,
    ):
        from openai import OpenAI

        self.model = model or glm_model()
        factory = client_factory or OpenAI
        self.client = factory(
            api_key=api_key or glm_api_key(),
            base_url=base_url or glm_base_url(),
        )

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        kwargs = {"model": self.model, "messages": messages}
        if tools:
            kwargs["tools"] = [
                {"type": "function", "function": t} if "function" not in t else t
                for t in tools
            ]
        resp = self.client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        tool_calls: list[ToolCall] = []
        raw = getattr(msg, "tool_calls", None) or []
        for tc in raw:
            fn = tc.function
            try:
                params = json.loads(fn.arguments or "{}")
            except json.JSONDecodeError:
                params = {}
            tool_calls.append(ToolCall(id=tc.id, skill_id=fn.name, params=params))
        return LLMResponse(text=msg.content or "", tool_calls=tool_calls)
