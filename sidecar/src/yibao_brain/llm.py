"""LLM provider 抽象 + GLM(智谱) 实现 + 测试用 Fake。"""
from __future__ import annotations

import json
import re
import sys
from collections.abc import AsyncIterator
from typing import Protocol

from pydantic import BaseModel, Field

from .config import llm_api_key, llm_base_url, llm_model, vision_api_key, vision_base_url, vision_model

# 流式响应空闲超时（秒）：建连或任意 chunk 超过该时长无数据即判定连接僵死，
# 避免死连接永久挂住 agent 任务、进而触发看门狗误杀。
_STREAM_IDLE_TIMEOUT = 60.0

OBSERVE_SYSTEM_PROMPT = (
    "你看用户当前屏幕截图。只在有明显、值得主动帮忙的点时才开口："
    "例如报错、编译失败、卡住的对话框、明显困惑。没有值得说的就别说。"
    '只回一个 JSON：{"speak": true/false, "text": "≤20字中文建议；没有则空串"}。'
)


def parse_observe(content: str) -> dict | None:
    """从视觉模型回复里取 {"speak","text"}；非法返回 None。"""
    m = re.search(r"\{.*\}", content or "", re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or type(obj.get("speak")) is not bool:
        return None
    if obj["speak"] is False:
        return {"speak": False, "text": ""}
    text = " ".join(str(obj.get("text", "")).split())[:20]
    if not text:
        return None
    return {"speak": True, "text": text}


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


def _vision_create_with_retry(create_fn, *, retries: int = 2, base_delay: float = 0.8):
    """视觉模型远端调用：对连接/超时错误短退避重试。

    GLM 视觉端点偶发 Connection error；若不重试，一次抖动就让整步 computer_use 报废、
    模型只好从头再来（重新开应用+截图），放大延迟。仅对网络类错误重试，其余立即抛出。
    """
    import time

    try:
        from openai import APIConnectionError, APITimeoutError

        retryable = (APIConnectionError, APITimeoutError, TimeoutError)
    except Exception:
        retryable = (TimeoutError,)
    for attempt in range(retries + 1):
        try:
            return create_fn()
        except retryable:
            if attempt >= retries:
                raise
            time.sleep(base_delay * (attempt + 1))


CHOOSE_TEMPERATURE = 0.1  # SoM 选号要确定性，低温降抖动

SCREEN_DESCRIBE_PROMPT = (
    "列出这张屏幕截图里可见的应用窗口：每个窗口一行，给出应用名、大致位置（左/右/上/下/全屏）"
    "和大致内容（80 字以内）。不要遗漏占画面比例大的窗口。只输出清单本身。"
)


def describe_screen(client, b64: str) -> str | None:
    """屏幕可见窗口枚举（截屏看屏幕/截图唤起共用）。client 为 ComputerUseClient；失败返 None。"""
    try:
        resp = _vision_create_with_retry(lambda: client.client.chat.completions.create(
            model=client.model,
            messages=[
                {"role": "system", "content": SCREEN_DESCRIBE_PROMPT},
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": b64}}]},
            ],
        ))
        text = (resp.choices[0].message.content or "").strip() if resp.choices else ""
        return text[:200] or None
    except Exception as e:
        print(f"[yibao] 屏幕描述失败（已跳过）：{e}", file=sys.stderr)
        return None


SNIP_QA_PROMPT = (
    "你是屏幕问答助手。根据这张屏幕截图回答用户的问题：简洁直接（200 字以内），"
    "只依据截图里可见的内容作答；截图里看不到答案就明说「截图中看不到」。"
)


def answer_image_query(client, b64: str, question: str) -> str | None:
    """区域截图问答（截图即问）。client 为 ComputerUseClient；失败返 None。"""
    try:
        resp = _vision_create_with_retry(lambda: client.client.chat.completions.create(
            model=client.model,
            messages=[
                {"role": "system", "content": SNIP_QA_PROMPT},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": b64}},
                    {"type": "text", "text": question},
                ]},
            ],
        ))
        text = (resp.choices[0].message.content or "").strip() if resp.choices else ""
        return text or None
    except Exception as e:
        print(f"[yibao] 截图问答失败（已跳过）：{e}", file=sys.stderr)
        return None


SCREEN_SUMMARY_PROMPT = (
    "用一句话（80 字以内）概括这张屏幕截图里前台应用正在显示的内容："
    "应用名 + 内容主题 + 可见的关键文字。只输出这句话。"
)


def summarize_screen(client, b64: str) -> str | None:
    """B 源截图兜底概括。client 为 ComputerUseClient；失败返 None。"""
    try:
        resp = _vision_create_with_retry(lambda: client.client.chat.completions.create(
            model=client.model,
            messages=[
                {"role": "system", "content": SCREEN_SUMMARY_PROMPT},
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": b64}}]},
            ],
        ))
        text = (resp.choices[0].message.content or "").strip() if resp.choices else ""
        return text[:120] or None
    except Exception as e:
        print(f"[yibao] B 源截图概括失败（已跳过）：{e}", file=sys.stderr)
        return None


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

    MARK_SYSTEM_PROMPT = (
        "你是桌面 GUI 操作助手。屏幕上红色数字框是可交互元素(1..N)，灰色字母框是区域(A..F)。"
        "根据用户任务给出【下一个动作】：目标在某个红框元素上就输出它的数字编号（一个整数）；"
        "目标不在任何红框元素上（如网页、画布等自绘内容），输出它所在的字母区域（一个字母）；"
        "需要输入文字时输出 JSON {\"action\":\"type\",\"text\":\"...\"}；"
        "任务完成时输出 finish。只输出整数编号或一个字母，不要任何其他文字。"
    )

    def __init__(self, api_key=None, model=None, base_url=None, client_factory=None):
        from openai import OpenAI

        self.model = model or vision_model()
        self.prefers_raw_bbox = self.model.startswith("glm-4.1v-thinking-")
        factory = client_factory or OpenAI
        self.client = factory(
            api_key=api_key or vision_api_key(),
            base_url=base_url or vision_base_url(),
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
        resp = _vision_create_with_retry(lambda: self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            extra_body={"thinking": {"type": "enabled"}},  # GLM 特有参数走 extra_body（openai SDK 不认顶层 kwargs）
        ))
        content = (resp.choices[0].message.content or "") if resp.choices else ""
        action = self._parse_action(content)
        return self._convert_model_box(action, screenshot_b64)

    def _convert_model_box(self, action: dict | None, screenshot_b64: str) -> dict | None:
        """把特定视觉模型的 grounding 坐标还原为截图物理像素。"""
        if not action or not self.model.startswith("glm-4.1v-thinking-"):
            return action
        box = action.get("box") or []
        if len(box) != 4:
            return action
        try:
            import base64
            import io

            from PIL import Image

            payload = screenshot_b64.split(",", 1)[1]
            with Image.open(io.BytesIO(base64.b64decode(payload))) as image:
                width, height = image.size
            x1, y1, x2, y2 = (float(v) for v in box)
        except (IndexError, TypeError, ValueError, OSError):
            return None
        converted = dict(action)
        converted["box"] = [
            x1 * width / 1000,
            y1 * height / 1000,
            x2 * width / 1000,
            y2 * height / 1000,
        ]
        return converted

    @staticmethod
    def _parse_action(content: str) -> dict | None:
        m = re.search(r"\{.*\}", content, re.S)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None

    def choose_action(self, marked_image_b64: str, task: str, n_marks: int,
                      history: list | None = None, n_zones: int = 0):
        messages: list[dict] = [{"role": "system", "content": self.MARK_SYSTEM_PROMPT}]
        if history:
            messages.extend(history)
        zone_hint = f"和 {n_zones} 个灰框字母区域(A-{chr(64 + n_zones)})" if n_zones else ""
        messages.append({
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": marked_image_b64}},
                {"type": "text", "text": f"任务：{task}\n共有 {n_marks} 个红框数字标记(1-{n_marks}){zone_hint}。给出下一个动作。"},
            ],
        })
        resp = _vision_create_with_retry(lambda: self.client.chat.completions.create(
            model=self.model, messages=messages, temperature=CHOOSE_TEMPERATURE,
        ))
        content = (resp.choices[0].message.content or "") if resp.choices else ""
        return self._parse_marked_action(content, n_marks, n_zones)

    def observe(self, screenshot_b64: str, app: str) -> dict | None:
        """视觉模型看一眼：是否有值得主动搭话的点。返 {"speak":bool,"text":str} 或 None。"""
        try:
            resp = _vision_create_with_retry(lambda: self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": OBSERVE_SYSTEM_PROMPT},
                    {"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": screenshot_b64}},
                        {"type": "text", "text": f"前台应用：{app}。判断是否值得搭话。"},
                    ]},
                ],
            ))
            content = (resp.choices[0].message.content or "") if resp.choices else ""
        except Exception as e:
            print(f"[yibao] 主动搭话视觉调用失败：{e}", file=sys.stderr)
            return None
        return parse_observe(content)

    @staticmethod
    def _parse_marked_action(content: str, n_marks: int, n_zones: int = 0) -> dict | None:
        s = (content or "").strip()
        m = re.search(r"\{.*\}", s, re.S)
        if m:
            try:
                obj = json.loads(m.group(0))
                if obj.get("action") in ("click", "type", "finish"):
                    mk = obj.get("mark")
                    if mk is not None and not (isinstance(mk, int) and 1 <= mk <= n_marks):
                        return None
                    return obj
                if obj.get("action") == "zoom":
                    zone = str(obj.get("zone") or "").upper()
                    if n_zones and len(zone) == 1 and "A" <= zone < chr(65 + n_zones):
                        return {"action": "zoom", "zone": zone}
                    return None
            except json.JSONDecodeError:
                pass
        if "finish" in s.lower():
            return {"action": "finish"}
        lm = re.fullmatch(r"([A-Za-z])\.?", s)
        if lm and n_zones:
            zone = lm.group(1).upper()
            if "A" <= zone < chr(65 + n_zones):
                return {"action": "zoom", "zone": zone}
            return None
        m2 = re.search(r"\d+", s)
        if m2:
            val = int(m2.group(0))
            if 1 <= val <= n_marks:
                return {"action": "click", "mark": val}
        return None

