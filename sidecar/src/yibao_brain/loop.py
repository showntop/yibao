"""Agent 回路：输入 -> 规划 -> 逐步执行 -> 结果，产出 Event 流。tool 执行收编到 ToolInvoker。"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import datetime

from .audit import AuditLog
from .history import ConversationHistory
from .host import Host
from .invoker import ToolInvoker
from .ipc import Action, Event
from .llm import LLMProvider, LLMResponse, ToolCall, merge_tool_call_deltas
from .memory import Memory
from .plugins import get_panel, get_panel_title, panel_payload
from .safety import Decision, Gate, RiskClassifier
from .skills import SkillRegistry

Confirmer = Callable[[Action], bool]

SYSTEM_PROMPT = (
    "你是译宝，一个桌面 AI 助手。通过调用工具帮用户操作电脑。\n"
    "铁律：用户的任何动作类请求（记录、查询、删除、修改、打开面板、操作电脑等）"
    "都必须调用工具完成；只有工具执行成功后，才能告诉用户「已完成」。\n"
    "禁止在未调用工具的情况下声称做了任何事，禁止编造执行结果（条数、内容、时间等）；"
    "没有对应工具就如实说做不到。\n"
    "描述里带「会打开面板」的工具被调用后会在用户屏幕上弹出对应面板窗；"
    "用户说「打开/看看某看板、面板、详情」时调用对应工具即可，不要只在对话里列数据。\n"
    "只有纯闲聊/知识问答才直接用自然语言回复。\n"
    "回复风格：聊天气泡很窄，回复要口语化、简短直接；不要用表格（改成每行一条「键：值」），"
    "不要用 # 标题，emoji 一条回复最多 2 个，列表不超过 5 条。\n"
    "很多能力按插件组织且默认隐藏；需要的能力不在工具列表里时，先调 use_plugin 展开对应插件"
    "（可用插件清单见该工具描述），再继续。"
)


async def _offload(fn, *args):
    """同步阻塞调用（技能执行 / 记忆读写：HTTP、torch、subprocess）挪到线程池。

    压在事件循环上会冻结整个 sidecar：看门狗 ping 答不了 → 15s 无 pong 被杀。
    """
    return await asyncio.get_running_loop().run_in_executor(None, lambda: fn(*args))


class AgentLoop:
    def __init__(
        self,
        provider: LLMProvider,
        skills: SkillRegistry,
        classifier: RiskClassifier,
        gate: Gate,
        memory: Memory,
        log: AuditLog,
        confirmer: Confirmer | None = None,
        user_id: str = "default",
        max_steps: int = 8,
        host: Host | None = None,
        history: ConversationHistory | None = None,
        focus_provider=None,
        active_plugins: set | None = None,
    ):
        self.provider = provider
        self.host = host
        self.memory = memory
        self.log = log
        self.confirmer = confirmer or (lambda _a: False)
        self.user_id = user_id
        self.max_steps = max_steps
        self.history = history
        # 面板焦点（v2 §5 focus）：() -> {"plugin","panel","item"} | None，由壳侧 panel_context 维护
        self.focus_provider = focus_provider
        # 路由式暴露（§12-2）：None=全量暴露（测试/兼容）；集合=仅暴露已激活插件（use_plugin 激活）
        self._active = active_plugins
        # tool 执行收编到唯一执行器；loop 只留事件路由与 LLM 往返
        self.invoker = ToolInvoker(skills, classifier, gate, log, self.confirmer, host)

    def _focus_message(self) -> dict | None:
        """当前面板焦点 → system 消息（无焦点/异常 → None，不打扰对话）。"""
        if self.focus_provider is None:
            return None
        try:
            focus = self.focus_provider()
        except Exception:
            return None
        if not focus or not focus.get("plugin"):
            return None
        item = focus.get("item") or {}
        text = f"用户当前正在看「{focus['plugin']}」插件的 {focus.get('panel', '?')} 面板"
        if item.get("title"):
            text += f"，选中条目「{item['title']}」"
        if item.get("id"):
            text += f"（id={item['id']}" + (f"，状态={item['status']}" if item.get("status") else "") + "）"
        if item.get("title") or item.get("id"):
            text += "。用户说的「这个/它/当前这条」默认指该条目"
        text += "；用户没问到时不要主动提及此上下文。"
        return {"role": "system", "content": text}

    @property
    def skills(self) -> SkillRegistry:
        """委托给 invoker：替换 registry 时执行器同步生效（测试/运行期换注册表）。"""
        return self.invoker.skills

    def _visible_tools(self) -> list[dict]:
        """本步发给 LLM 的工具清单：全量（_active 为 None）或 底座 + 已激活插件 + 焦点插件。

        用户正盯着某插件面板（focus）时该插件视为激活——面板场景的对话必须能用它的工具。
        """
        if self._active is None:
            return self.skills.openai_tools()
        active = set(self._active)
        if self.focus_provider is not None:
            try:
                focus = self.focus_provider()
            except Exception:
                focus = None
            if focus and focus.get("plugin"):
                active.add(focus["plugin"])
        return self.skills.openai_tools(active_plugins=active)

    def _auto_activate(self, skill_id: str) -> None:
        """插件 tool 被执行过 → 该插件激活（直接点名调用也算展开，后续步骤工具可见）。"""
        if self._active is not None and "." in skill_id:
            self._active.add(skill_id.split(".", 1)[0])

    @skills.setter
    def skills(self, reg: SkillRegistry) -> None:
        self.invoker.skills = reg

    def _panel_with_refresh(self, action, result) -> dict | None:
        """面板载荷：tool 声明了 refresh 时跟一次本插件只读查询，面板拿刷新数据而非操作回执。

        写操作（insert/delete 等）的 result.data 是回执 {"id":…}，直接喂面板会显示空；
        声明 refresh（如 notes.list）则面板事件携带查询结果。刷新意外需确认/失败 →
        回退原数据（刷新不该弹确认打断用户，与 server._emit_refresh_panel 同一策略）。

        refresh 传参取「action 入参 ∩ refresh tool 声明参数」（如 save{id,content} → get{id}），
        无交集传 {}（list 类刷新不带条件）。最后做 focus 重定向：用户正盯着同插件 webview
        面板（如写作编辑器）的同一条目时，回跳面板落在该 webview 上而不是硬切走——
        编辑器收到 rows 重推后自行刷新稿件，对话改稿不打断工作台。
        """
        payload = panel_payload(result)
        if payload is None or not result.success:
            return payload
        refresh_id = getattr(self.skills.get(action.skill_id), "refresh", None)
        if not refresh_id:
            return self._redirect_to_focused_webview(payload)
        r_params: dict = {}
        try:
            props = (
                self.skills.get(refresh_id).openai_schema().get("parameters", {}).get("properties", {})
            )
            r_params = {k: action.params[k] for k in props if k in action.params}
        except Exception:
            r_params = {}
        r_action = self.invoker.propose(
            ToolCall(id=f"refresh_{action.id}", skill_id=refresh_id, params=r_params)
        )
        if self.invoker.decide(r_action) != Decision.AUTO:
            return self._redirect_to_focused_webview(payload)
        r_result = self.invoker.execute(r_action, r_params)
        payload = panel_payload(r_result) or payload
        return self._redirect_to_focused_webview(payload)

    def _redirect_to_focused_webview(self, payload: dict) -> dict:
        """用户正盯着同插件 webview 面板的同一条目（focus）→ 回跳改落到该 webview。

        编辑器/工作台类 webview 面板靠 rows 重推自刷新；无 focus、跨插件、非同一条目、
        或 focus 面板不是 webview 时原样返回。
        """
        if self.focus_provider is None:
            return payload
        try:
            focus = self.focus_provider()
        except Exception:
            return payload
        if not focus or not focus.get("plugin") or not focus.get("panel"):
            return payload
        ref = f"{focus['plugin']}:{focus['panel']}"
        if not str(payload.get("panel", "")).startswith(f"{focus['plugin']}:"):
            return payload
        rows = (payload.get("data") or {}).get("rows") or []
        item = focus.get("item") or {}
        if not rows or item.get("id") is None or str(rows[0].get("id")) != str(item["id"]):
            return payload
        panel = get_panel(ref)
        if not (isinstance(panel, dict) and panel.get("type") == "webview" and "html" in panel):
            return payload
        return {
            "panel": ref,
            "title": get_panel_title(ref),
            "schema": None,
            "webview": {"html": panel["html"]},
            "data": payload["data"],
        }

    def run(self, user_text: str, surface: str | None = None) -> Iterator[Event]:
        memories = self.memory.recall(user_text, self.user_id)
        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        if memories:
            messages.append({"role": "system", "content": "关于用户的记忆：\n" + "\n".join(memories)})
        focus_msg = self._focus_message()
        if focus_msg:
            messages.append(focus_msg)
        messages.append(_now_message())
        if self.history:
            messages.extend(self.history.messages())
        messages.append({"role": "user", "content": user_text})
        run_start = len(messages) - 1  # 本轮轨迹起点（user 消息），成功收尾时整轮入史（含工具调用）

        for _ in range(self.max_steps):
            resp: LLMResponse = self.provider.chat(messages, tools=self._visible_tools())
            if not resp.tool_calls:
                self.memory.add(user_text, self.user_id)
                if self.history:
                    span = messages[run_start:] + [{"role": "assistant", "content": resp.text}]
                    span[0] = _tag_surface(span[0], surface)
                    self.history.record_messages(span)
                yield Event(kind="final_reply", text=resp.text)
                return
            messages.append(_assistant_with_tools(resp.text, resp.tool_calls))
            proceeded = False
            for tc in resp.tool_calls:
                tc.skill_id = self.skills.resolve_llm_name(tc.skill_id)  # 安全名 → 真实 id
                action = self.invoker.propose(tc)
                yield Event(kind="action_proposed", action=action)
                decision = self.invoker.decide(action)
                if decision == Decision.CONFIRM:
                    yield Event(kind="confirmation_needed", action=action, confirmation_id=action.id)
                    if not self.invoker.confirm_sync(action):
                        yield Event(kind="error", text=f"用户拒绝执行 {tc.skill_id}")
                        messages.append({"role": "tool", "tool_call_id": tc.id, "content": "用户拒绝执行该操作"})
                        continue
                elif decision == Decision.DENY:
                    yield Event(kind="error", text=f"策略禁止执行 {tc.skill_id}（风险过高）")
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": "策略禁止该操作"})
                    continue
                result = self.invoker.execute(action, tc.params)
                self._auto_activate(action.skill_id)
                yield Event(kind="action_result", action=action, result=result)
                if action.skill_id == "use_plugin" and result.success and not (result.data or {}).get("already"):
                    # 插件展开要知情（§12-2 已定）：轻提示，不弹窗不打断
                    yield Event(kind="notice", text=(result.data or {}).get("human", "插件已展开"))
                payload = self._panel_with_refresh(action, result)  # 声明 refresh 则面板拿刷新数据
                if payload is not None:
                    yield Event(kind="panel", payload=payload)
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": _stringify_result(result)}
                )
                proceeded = True
            if not proceeded:
                # 所有工具调用都被拒/禁，给模型一次机会换策略
                continue
        yield Event(kind="error", text="达到最大步数仍未完成")

    async def arun(
        self, user_text: str, cancel=None, surface: str | None = None
    ) -> AsyncIterator[Event]:
        """流式异步回路：LLM 边生成边吐 final_reply_chunk；cancel.is_set() 随时打断。

        cancel 为 asyncio.Event（或任何带 is_set() 的对象）。打断时产出 interrupted 并返回。
        confirmer 可同步也可异步（返回协程则 await）。
        surface 为会话分流标签（pet / panel:<plugin>）：只落历史，不进发给 provider 的消息。
        """
        memories = await _offload(self.memory.recall, user_text, self.user_id)
        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        if memories:
            messages.append({"role": "system", "content": "关于用户的记忆：\n" + "\n".join(memories)})
        focus_msg = self._focus_message()
        if focus_msg:
            messages.append(focus_msg)
        messages.append(_now_message())
        if self.history:
            messages.extend(self.history.messages())
        messages.append({"role": "user", "content": user_text})
        run_start = len(messages) - 1  # 本轮轨迹起点（user 消息），成功收尾时整轮入史（含工具调用）

        def cancelled() -> bool:
            return bool(cancel and cancel.is_set())

        for _ in range(self.max_steps):
            if cancelled():
                yield Event(kind="interrupted")
                return
            text_buf = ""
            delta_acc: list = []
            async for delta in self.provider.astream(messages, tools=self._visible_tools()):
                if cancelled():
                    yield Event(kind="interrupted")
                    return
                if delta.text:
                    text_buf += delta.text
                    yield Event(kind="final_reply_chunk", text=delta.text)
                if delta.tool_call_deltas:
                    delta_acc.extend(delta.tool_call_deltas)
            tool_calls = merge_tool_call_deltas(delta_acc)
            if not tool_calls:
                await _offload(self.memory.add, user_text, self.user_id)
                if self.history:
                    span = messages[run_start:] + [{"role": "assistant", "content": text_buf}]
                    span[0] = _tag_surface(span[0], surface)
                    self.history.record_messages(span)
                yield Event(kind="final_reply", text=text_buf)
                return
            messages.append(_assistant_with_tools(text_buf, tool_calls))
            proceeded = False
            for tc in tool_calls:
                if cancelled():
                    yield Event(kind="interrupted")
                    return
                tc.skill_id = self.skills.resolve_llm_name(tc.skill_id)  # 安全名 → 真实 id
                action = self.invoker.propose(tc)
                yield Event(kind="action_proposed", action=action)
                decision = self.invoker.decide(action)
                if decision == Decision.CONFIRM:
                    yield Event(kind="confirmation_needed", action=action, confirmation_id=action.id)
                    ok = await self.invoker.confirm(action)
                    if cancelled() or not ok:
                        yield Event(kind="error", text=f"用户拒绝执行 {tc.skill_id}")
                        messages.append(
                            {"role": "tool", "tool_call_id": tc.id, "content": "用户拒绝执行该操作"}
                        )
                        continue
                elif decision == Decision.DENY:
                    yield Event(kind="error", text=f"策略禁止执行 {tc.skill_id}（风险过高）")
                    messages.append(
                        {"role": "tool", "tool_call_id": tc.id, "content": "策略禁止该操作"}
                    )
                    continue
                result = await _offload(self.invoker.execute, action, tc.params)
                self._auto_activate(action.skill_id)
                yield Event(kind="action_result", action=action, result=result)
                if action.skill_id == "use_plugin" and result.success and not (result.data or {}).get("already"):
                    # 插件展开要知情（§12-2 已定）：轻提示，不弹窗不打断
                    yield Event(kind="notice", text=(result.data or {}).get("human", "插件已展开"))
                payload = await _offload(self._panel_with_refresh, action, result)  # 声明 refresh 则面板拿刷新数据
                if payload is not None:
                    yield Event(kind="panel", payload=payload)
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": _stringify_result(result)}
                )
                proceeded = True
            if not proceeded:
                continue
        yield Event(kind="error", text="达到最大步数仍未完成")


_WEEKDAYS = "一二三四五六日"


def _now_message() -> dict:
    """当前本地时间 → system 消息（LLM 要把「明早 9 点」翻成绝对时间，必须知道现在几点）。"""
    now = datetime.now()
    return {
        "role": "system",
        "content": f"当前本地时间：{now.strftime('%Y-%m-%d %H:%M')}（星期{_WEEKDAYS[now.weekday()]}）",
    }


def _tag_surface(user_msg: dict, surface: str | None) -> dict:
    """落史前给本轮 user 消息打 surface 标签（pet / panel:<plugin>）。

    只存在于历史层：喂 provider 的 messages 列表不受影响（严格校验的 provider 遇未知字段会 400）。
    history.messages() 渲染上下文时剥掉标签、给面板轮加【xx 面板】标记。
    """
    if not surface or surface == "pet":
        return user_msg
    return {**user_msg, "surface": surface}


def _assistant_with_tools(content: str, tool_calls) -> dict:
    """构造 assistant 消息：带 tool_calls 时附 OpenAI 标准字段。

    DeepSeek 等严格校验：tool 消息必须紧跟带 tool_calls 的 assistant 消息，
    否则 400（GLM 容忍缺字段，但不能依赖）。
    """
    msg: dict = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.skill_id,
                    "arguments": json.dumps(tc.params, ensure_ascii=False),
                },
            }
            for tc in tool_calls
        ]
    return msg


def _stringify_result(result) -> str:
    payload = {"success": result.success, "data": result.data, "error": result.error}
    return json.dumps(payload, ensure_ascii=False)
