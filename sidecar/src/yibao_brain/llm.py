"""LLM provider 抽象 + GLM(智谱) 实现 + 测试用 Fake。"""
from __future__ import annotations

import json
import re
from typing import Protocol

from pydantic import BaseModel, Field

from .config import glm_api_key, glm_base_url, glm_model, glm_vision_model


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


class ComputerUseClient:
    """GLM-4.6V 视觉 grounding 兜底：截图 + 任务 → 下一步动作 JSON。

    动作 JSON: {"action":"click|type|scroll|finish","box":[x1,y1,x2,y2],"text":"..."}
    box 为截图绝对像素 bbox。client_factory 注入便于测试。
    """

    SYSTEM_PROMPT = (
        "你是桌面 GUI 操作助手。观察截图，根据用户任务输出【下一个动作】的 JSON：\n"
        '{"action":"click|type|scroll|finish","box":[x1,y1,x2,y2],"text":"..."}\n'
        "规则：box 是目标元素在截图中的绝对像素 bbox（左上角 0,0，基于原图分辨率）；"
        "click 用 box 中心点；type 时 text 为要输入的文字；"
        "任务完成或无法继续时 action=finish。只输出这一个 JSON，不要多余文字。"
    )

    def __init__(self, api_key=None, model=None, base_url=None, client_factory=None):
        from openai import OpenAI

        self.model = model or glm_vision_model()
        factory = client_factory or OpenAI
        self.client = factory(
            api_key=api_key or glm_api_key(),
            base_url=base_url or glm_base_url(),
        )

    def next_action(self, screenshot_b64: str, task: str, history: list | None = None) -> dict | None:
        messages: list[dict] = [{"role": "system", "content": self.SYSTEM_PROMPT}]
        if history:
            messages.extend(history)
        messages.append({
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": screenshot_b64}},
                {"type": "text", "text": f"任务：{task}\n请给出下一步动作 JSON。"},
            ],
        })
        resp = self.client.chat.completions.create(
            model=self.model, messages=messages, thinking={"type": "enabled"}
        )
        content = (resp.choices[0].message.content or "") if resp.choices else ""
        return self._parse_action(content)

    @staticmethod
    def _parse_action(content: str) -> dict | None:
        m = re.search(r"\{.*\}", content, re.S)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
