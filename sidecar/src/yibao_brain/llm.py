"""LLM provider 抽象 + GLM(智谱) 实现 + 测试用 Fake。"""
from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Protocol

from pydantic import BaseModel, Field

from .config import llm_api_key, llm_base_url, llm_model, vision_model


class ToolCall(BaseModel):
    id: str
    skill_id: str
    params: dict = Field(default_factory=dict)


class LLMResponse(BaseModel):
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)


class ToolCallDelta(BaseModel):
    """流式增量里的工具调用片段（OpenAI delta.tool_calls 元素）。

    index 用来跨 chunk 聚合同一个 tool_call；id/name/arguments 都是逐步拼接的。
    """

    index: int = 0
    id: str = ""
    skill_id: str = ""  # function.name（增量，最终拼接）
    arguments: str = ""  # function.arguments 片段（增量拼接，整体是 JSON 字符串）


class LLMDelta(BaseModel):
    """单次流式增量：text 是自上一 delta 起的文字增量；tool_call_deltas 是工具片段。"""

    text: str = ""
    tool_call_deltas: list[ToolCallDelta] = Field(default_factory=list)


class LLMProvider(Protocol):
    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse: ...

    async def astream(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> AsyncIterator[LLMDelta]: ...


def merge_tool_call_deltas(deltas: list[ToolCallDelta]) -> list[ToolCall]:
    """把跨 chunk 的 ToolCallDelta 按 index 聚合成完整 ToolCall 列表。"""
    acc: dict[int, dict] = {}
    for d in deltas:
        slot = acc.setdefault(d.index, {"id": "", "skill_id": "", "arguments": ""})
        if d.id:
            slot["id"] = d.id
        if d.skill_id:
            slot["skill_id"] += d.skill_id
        slot["arguments"] += d.arguments
    out: list[ToolCall] = []
    for idx in sorted(acc):
        slot = acc[idx]
        try:
            params = json.loads(slot["arguments"] or "{}")
        except json.JSONDecodeError:
            params = {}
        out.append(
            ToolCall(
                id=slot["id"] or f"call_{idx}",
                skill_id=slot["skill_id"],
                params=params,
            )
        )
    return out


class FakeProvider:
    """测试用：chat 返预设响应；astream 把 text 切片流式吐出。"""

    def __init__(
        self,
        text: str = "",
        tool_calls: list[ToolCall] | None = None,
        chunks: list[str] | None = None,
        delay: float = 0.0,
    ):
        self._text = text
        self._tool_calls = tool_calls or []
        self._chunks = chunks  # 显式分片；None 时按 text 整体（或切片）输出
        self._delay = delay
        self.calls: list[dict] = []
        self.astream_calls: list[dict] = []

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        self.calls.append({"messages": messages, "tools": tools})
        return LLMResponse(text=self._text, tool_calls=list(self._tool_calls))

    async def astream(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> AsyncIterator[LLMDelta]:
        import asyncio

        self.astream_calls.append({"messages": messages, "tools": tools})
        if self._tool_calls:
            # 工具调用一次性吐完（参数 JSON 已是完整的）
            yield LLMDelta(
                tool_call_deltas=[
                    ToolCallDelta(
                        index=i,
                        id=tc.id,
                        skill_id=tc.skill_id,
                        arguments=json.dumps(tc.params, ensure_ascii=False),
                    )
                    for i, tc in enumerate(self._tool_calls)
                ]
            )
            return
        pieces = self._chunks if self._chunks is not None else ([self._text] if self._text else [])
        for piece in pieces:
            if self._delay:
                await asyncio.sleep(self._delay)
            yield LLMDelta(text=piece)


class GLMProvider:
    """智谱 GLM，走 OpenAI-兼容端点。client_factory 注入便于测试。

    chat 走同步 OpenAI；astream 走 AsyncOpenAI（懒加载，首次用时建）。
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        client_factory=None,
        async_client_factory=None,
    ):
        from openai import AsyncOpenAI, OpenAI

        self.model = model or llm_model()
        creds_key = api_key or llm_api_key()
        creds_url = base_url or llm_base_url()
        factory = client_factory or OpenAI
        self.client = factory(api_key=creds_key, base_url=creds_url)

        self._async_factory = async_client_factory or AsyncOpenAI
        self._async_creds = (creds_key, creds_url)
        self._async_client = None

    def _ensure_async_client(self):
        if self._async_client is None:
            self._async_client = self._async_factory(
                api_key=self._async_creds[0], base_url=self._async_creds[1]
            )
        return self._async_client

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

    async def astream(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> AsyncIterator[LLMDelta]:
        kwargs = {"model": self.model, "messages": messages, "stream": True}
        if tools:
            kwargs["tools"] = [
                {"type": "function", "function": t} if "function" not in t else t
                for t in tools
            ]
        client = self._ensure_async_client()
        stream = await client.chat.completions.create(**kwargs)
        async for chunk in stream:
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            delta = choices[0].delta
            text = getattr(delta, "content", None) or ""
            raw_tcs = getattr(delta, "tool_calls", None) or []
            tcd: list[ToolCallDelta] = []
            for tc in raw_tcs:
                fn = getattr(tc, "function", None)
                tcd.append(
                    ToolCallDelta(
                        index=getattr(tc, "index", 0) or 0,
                        id=getattr(tc, "id", "") or "",
                        skill_id=(getattr(fn, "name", "") or "") if fn else "",
                        arguments=(getattr(fn, "arguments", "") or "") if fn else "",
                    )
                )
            yield LLMDelta(text=text, tool_call_deltas=tcd)


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

        self.model = model or vision_model()
        factory = client_factory or OpenAI
        self.client = factory(
            api_key=api_key or llm_api_key(),
            base_url=base_url or llm_base_url(),
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
            model=self.model,
            messages=messages,
            extra_body={"thinking": {"type": "enabled"}},  # GLM 特有参数走 extra_body（openai SDK 不认顶层 kwargs）
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
