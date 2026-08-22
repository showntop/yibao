"""LLM provider 抽象 + OpenAI 兼容端点实现（智谱 GLM / DeepSeek / OpenAI…）+ 测试用 Fake。

R-32b（2026-08-22）：视觉域（ComputerUseClient/describe_screen/summarize_screen/
answer_image_query/parse_observe/OBSERVE_SYSTEM_PROMPT 等）已拆至 llm_vision.py；
本文件保留 provider 抽象与流式实现，并 re-export 视觉域符号——server/background/cli
与测试的 `from .llm import ComputerUseClient` 等路径不变（引用面均为函数内延迟 import，
monkeypatch yibao_brain.llm.* 约定不受影响）。
"""
from __future__ import annotations

from .log import log
import json
import sys
from collections.abc import AsyncIterator
from typing import Protocol

from pydantic import BaseModel, Field

from .config import llm_api_key, llm_base_url, llm_model
from .llm_vision import ComputerUseClient, answer_image_query, describe_screen, summarize_screen  # noqa: F401  re-export：视觉域已拆出，路径兼容

# 流式响应空闲超时（秒）：建连或任意 chunk 超过该时长无数据即判定连接僵死，
# 避免死连接永久挂住 agent 任务、进而触发看门狗误杀。
_STREAM_IDLE_TIMEOUT = 60.0


class ToolCall(BaseModel):
    id: str
    skill_id: str
    params: dict = Field(default_factory=dict)


class Usage(BaseModel):
    """一次 LLM 调用的 token 用量（OpenAI 兼容 usage 结构；缺省 0）。

    cached 为「命中缓存」的输入 token（DeepSeek/GLM 等按更低价计费）。
    未命中/写入由 prompt_cached 拆分：详情页把 cached 标「命中」，其余输入标「未命中」。
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0


class LLMResponse(BaseModel):
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)


class ToolCallDelta(BaseModel):
    """流式增量里的工具调用片段（OpenAI delta.tool_calls 元素）。

    index 用来跨 chunk 聚合同一个 tool_call；id/name/arguments 都是逐步拼接的。
    """

    index: int = 0
    id: str = ""
    skill_id: str = ""  # function.name（增量，最终拼接）
    arguments: str = ""  # function.arguments 片段（增量拼接，整体是 JSON 字符串）


class LLMDelta(BaseModel):
    """单次流式增量：text 是自上一 delta 起的文字增量；tool_call_deltas 是工具片段。

    usage 只在流式末尾（最后一个 chunk，choices 空但带 usage）出现一次。
    """

    text: str = ""
    tool_call_deltas: list[ToolCallDelta] = Field(default_factory=list)
    usage: Usage | None = None


class LLMProvider(Protocol):
    def chat(
        self, messages: list[dict], tools: list[dict] | None = None, timeout: float | None = None
    ) -> LLMResponse: ...

    async def astream(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> AsyncIterator[LLMDelta]: ...


def _usage_from_openai(raw) -> Usage:
    """把 OpenAI SDK 的 usage 对象（或 dict）转成 Usage；无/异常返回全 0。

    cached_tokens 取 prompts 里的 cached_tokens（DeepSeek/GLM 的缓存计费字段）。
    """
    if raw is None:
        return Usage()
    try:
        if hasattr(raw, "prompt_tokens"):
            prompt = int(getattr(raw, "prompt_tokens", 0) or 0)
            completion = int(getattr(raw, "completion_tokens", 0) or 0)
            total = int(getattr(raw, "total_tokens", 0) or 0)
            cached = 0
            # prompt_tokens_details.cached_tokens（OpenAI 新结构）或顶层 cached_tokens
            details = getattr(raw, "prompt_tokens_details", None)
            if details is not None:
                cached = int(getattr(details, "cached_tokens", 0) or 0)
            if not cached:
                cached = int(getattr(raw, "cached_tokens", 0) or 0)
            return Usage(
                prompt_tokens=prompt,
                completion_tokens=completion,
                total_tokens=total or (prompt + completion),
                cached_tokens=cached,
            )
        d = dict(raw)
        prompt = int(d.get("prompt_tokens", 0) or 0)
        completion = int(d.get("completion_tokens", 0) or 0)
        total = int(d.get("total_tokens", 0) or 0)
        cached = int(d.get("cached_tokens", 0) or 0)
        details = d.get("prompt_tokens_details") or {}
        if isinstance(details, dict):
            cached = cached or int(details.get("cached_tokens", 0) or 0)
        return Usage(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total or (prompt + completion),
            cached_tokens=cached,
        )
    except (TypeError, ValueError):
        return Usage()


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
        usage: Usage | dict | None = None,
    ):
        self._text = text
        self._tool_calls = tool_calls or []
        self._chunks = chunks  # 显式分片；None 时按 text 整体（或切片）输出
        self._delay = delay
        self._usage = usage if isinstance(usage, Usage) else (Usage(**usage) if usage else None)
        self.calls: list[dict] = []
        self.astream_calls: list[dict] = []

    def chat(
        self, messages: list[dict], tools: list[dict] | None = None, timeout: float | None = None
    ) -> LLMResponse:
        self.calls.append({"messages": messages, "tools": tools})
        return LLMResponse(text=self._text, tool_calls=list(self._tool_calls), usage=self._usage or Usage())

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
            if self._usage:
                yield LLMDelta(usage=self._usage)  # 流式末尾 usage chunk（对齐真实 provider）
            return
        pieces = self._chunks if self._chunks is not None else ([self._text] if self._text else [])
        for piece in pieces:
            if self._delay:
                await asyncio.sleep(self._delay)
            yield LLMDelta(text=piece)
        if self._usage:
            yield LLMDelta(usage=self._usage)  # 末尾 usage chunk


class OpenAICompatProvider:
    """通用 OpenAI 兼容端点 provider（智谱 GLM / DeepSeek / OpenAI 等都走它）。

    端点由 YIBAO_LLM_API_KEY / YIBAO_LLM_MODEL / YIBAO_LLM_BASE_URL 配置（旧名
    YIBAO_GLM_* 仍作回退，见 config.py）。client_factory 注入便于测试。
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

    def chat(
        self, messages: list[dict], tools: list[dict] | None = None, timeout: float | None = None
    ) -> LLMResponse:
        kwargs = {"model": self.model, "messages": messages}
        if tools:
            kwargs["tools"] = [
                {"type": "function", "function": t} if "function" not in t else t
                for t in tools
            ]
        if timeout is not None:  # 仅显式传入时下发（如 Distiller 离线提炼 60s）；主对话回路保持 SDK 默认
            kwargs["timeout"] = timeout
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
        usage = _usage_from_openai(getattr(resp, "usage", None))
        return LLMResponse(text=msg.content or "", tool_calls=tool_calls, usage=usage)

    async def astream(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> AsyncIterator[LLMDelta]:
        kwargs = {"model": self.model, "messages": messages, "stream": True}
        # OpenAI 兼容端点：要求流式末尾带 usage chunk（否则 token 统计拿不到）
        kwargs["stream_options"] = {"include_usage": True}
        if tools:
            kwargs["tools"] = [
                {"type": "function", "function": t} if "function" not in t else t
                for t in tools
            ]
        import asyncio

        client = self._ensure_async_client()
        try:
            stream = await asyncio.wait_for(
                client.chat.completions.create(**kwargs), timeout=_STREAM_IDLE_TIMEOUT
            )
        except asyncio.TimeoutError:
            raise TimeoutError("LLM 流式请求建立超时（60s 无响应）") from None
        it = stream.__aiter__()
        usage_yielded = False  # usage 是累计值，多个 chunk 重复带时应只 yield 一次（防 loop 重复累加）
        while True:
            try:
                chunk = await asyncio.wait_for(it.__anext__(), timeout=_STREAM_IDLE_TIMEOUT)
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                raise TimeoutError("LLM 流式响应超过 60s 无数据（连接僵死）") from None
            # usage 提取不受 choices 空否影响：OpenAI 兼容端点的 usage chunk
            # 常带非空 choices（[{delta:{}, finish_reason:"stop"}]）而非空数组，
            # 只在「choices 为空」时查 usage 会漏掉它 → token 恒 0。
            if not usage_yielded:
                usage = _usage_from_openai(getattr(chunk, "usage", None))
                if usage.total_tokens:
                    usage_yielded = True
                    yield LLMDelta(usage=usage)
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


# 旧名别名，新代码用 OpenAICompatProvider
GLMProvider = OpenAICompatProvider




