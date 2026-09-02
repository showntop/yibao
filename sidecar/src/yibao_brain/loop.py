"""Agent 回路：输入 -> 规划 -> 逐步执行 -> 结果，产出 Event 流。tool 执行收编到 ToolInvoker。"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import datetime

from .audit import AuditLog
from .history import ConversationHistory
from .host import Host
from .invoker import ToolInvoker
from .ipc import Action, ActionResult, Event
from .llm import LLMProvider, LLMResponse, ToolCall, Usage, merge_tool_call_deltas
from .pricing import compute_cost
from .memory import Memory
from .plugins import get_panel, get_panel_title, panel_payload
from .safety import Decision, Gate, RiskClassifier
from .tools import ToolRegistry

Confirmer = Callable[[list[Action]], dict[str, tuple[bool, bool]]]

SYSTEM_PROMPT = (
    "你是译宝，一个桌面 AI 助手。通过调用工具帮用户操作电脑。\n"
    "铁律：用户的任何动作类请求（记录、查询、删除、修改、打开面板、操作电脑等）"
    "都必须调用工具完成；只有工具执行成功后，才能告诉用户「已完成」。\n"
    "禁止在未调用工具的情况下声称做了任何事，禁止编造执行结果（条数、内容、时间等）；"
    "没有对应工具就如实说做不到。\n"
    "记忆与闪念的边界：用户随口说的偏好/习惯/事实（「我喜欢…」「我叫…」「我住…」）"
    "不必调用工具——系统会自动记进长期记忆；只有用户明确要「记个想法/灵感/备忘」"
    "（如「记条闪念：…」）时才调用闪念工具。把偏好塞进闪念工具是错的。\n"
    "描述里带「会打开面板」的工具被调用后会在用户屏幕上弹出对应面板窗；"
    "用户说「打开/看看某看板、面板、详情」时调用对应工具即可，不要只在对话里列数据。\n"
    "面板窗可能被用户手动收起（窗口隐藏但内容保留）。用户再次说「打开/再看/恢复面板」时，"
    "必须重新调用对应的打开工具弹出面板——即使之前已经打开过；不要仅凭对话历史判断面板还开着。\n"
    "只有纯闲聊/知识问答才直接用自然语言回复。\n"
    "桌面 GUI 操作时：标准应用（计算器、访达、系统设置、备忘录、邮件等原生应用）的控件都有 AX 角色/标题，"
    "必须先 read_tree 看结构，再用 click_control（按 role+title）或 type_text 操作——本地、瞬时、可靠。"
    "只有 read_tree 明确找不到目标控件时才用 computer_use：它每步都要调一次视觉大模型，又慢又常因网络失败，是最后手段。"
    "computer_use 会自行截图并只执行一个动作，不要在 computer_use 前后重复调用 screenshot；"
    "执行到用户要求的最后一个控件后立即停止调用工具并回复结果。\n"
    "回答「当前屏幕/在哪个页面/有什么窗口」类问题时：区分「前台应用」与「可见窗口」——"
    "前台应用身份以 read_tree 为准，可见窗口枚举以最近一次 screenshot 的视觉描述为准；"
    "没有最近的屏幕描述就先调 screenshot 再回答，不要凭印象编造窗口布局。\n"
    "长耗时任务（编译/下载/测试/长跑命令）用 watch_command 后台盯，它会立即返回、完成或失败时自动报告，不要干等也不要反复截图。\n"
    "回复风格：聊天气泡很窄，回复要口语化、简短直接；不要用表格（改成每行一条「键：值」），"
    "不要用 # 标题，emoji 一条回复最多 2 个，列表不超过 5 条。\n"
    "很多能力按插件组织且默认隐藏；需要的能力不在工具列表里时，先调 use_plugin 展开对应插件"
    "（可用插件清单见该工具描述），再继续。"
    "\n\n【coding 分工】用户要做的若是交互式 coding（写功能/修 bug/重构——需要来回对话、看文件改动），"
    "引导用户去「编码面板」（插件页 → 编码，选项目后跟 Claude Code 多轮）。"
    "`agents.dispatch_task` 仅用于后台 fire-and-forget 长任务（跑完报告，不交互）。"
    "后台或并行的编码任务用 coding.start 传 background=true：不开面板静默执行，完成会自动汇报；"
    "需要用户盯着看改动的任务不要加。"
)

_TOOL_BUDGET_FINAL_PROMPT = (
    "本轮工具调用已达到安全上限。禁止继续调用任何工具。请根据已有工具结果简短说明："
    "任务若已完成就报告结果；若未完成或无法确认，就明确说明已停止以及还差什么。"
    "不得把未验证的状态说成已完成。"
)
_TOOL_BUDGET_FALLBACK = "已达到安全操作上限，我已停止继续操作；当前结果还需要你确认。"


async def _offload(fn, *args):
    """同步阻塞调用（技能执行 / 记忆读写：HTTP、torch、subprocess）挪到线程池。

    压在事件循环上会冻结整个 sidecar：看门狗 ping 答不了 → 15s 无 pong 被杀。
    """
    return await asyncio.get_running_loop().run_in_executor(None, lambda: fn(*args))


def _with_surface_hints(payload: dict, result: ActionResult, origin: str | None) -> dict:
    """把技能的表面建议并进 panel 载荷。origin 供宿主做 matched-geometry 与返回定位。"""
    return {
        **payload,
        "presentation": result.presentation,
        "attention": result.attention,
        "object": result.object,
        "origin": origin,
    }


class AgentLoop:
    def __init__(
        self,
        provider: LLMProvider,
        skills: ToolRegistry,
        classifier: RiskClassifier,
        gate: Gate,
        memory: Memory,
        log: AuditLog,
        confirmer: Confirmer | None = None,
        user_id: str = "default",
        max_steps: int = 50,
        host: Host | None = None,
        history: ConversationHistory | None = None,
        focus_provider=None,
        active_plugins: set | None = None,
        feed=None,
    ):
        self.provider = provider
        self.host = host
        self.memory = memory
        self.log = log
        self._run_start = 0.0  # run_metrics 耗时基准（run/arun 开头置 time.monotonic）
        # 批量 confirmer：list[Action] -> {action.id: (approved, remember)}；
        # 默认空 dict（=全拒），由 invoker.batch_confirm 透传，loop 用 .get(id, (False,False)) 读。
        self.confirmer = confirmer or (lambda _actions: {})
        self.user_id = user_id
        self.max_steps = max_steps
        self.history = history
        # 主屏 Feed 存储（Task 4）：新记忆成立时按小时合并写一条「记住了：…」；
        # None=测试/未接入，旧路径（不写 Feed）保持不变。
        self.feed = feed
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
            # 无面板焦点：明确告知「没有打开的面板窗」，防止 LLM 凭对话历史误判面板仍开着
            # （面板可能已被用户收起）。用户若要求「打开/再看/恢复面板」，就必须调对应打开工具重弹。
            return {"role": "system", "content":
                    "用户当前没有打开任何面板窗（面板可能被收起了）。"
                    "用户若要求「打开/再看/恢复面板」，必须调用对应的打开工具重新弹出。"}
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
    def skills(self) -> ToolRegistry:
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

    def _auto_activate(self, tool_id: str) -> None:
        """插件 tool 被执行过 → 该插件激活（直接点名调用也算展开，后续步骤工具可见）。"""
        if self._active is not None and "." in tool_id:
            self._active.add(tool_id.split(".", 1)[0])

    @skills.setter
    def skills(self, reg: ToolRegistry) -> None:
        self.invoker.skills = reg

    def _panel_with_refresh(self, action, result, conversation_id: str | None = None) -> dict | None:
        """面板载荷：tool 声明了 refresh 时跟一次本插件只读查询，面板拿刷新数据而非操作回执。

        写操作（insert/delete 等）的 result.data 是回执 {"id":…}，直接喂面板会显示空；
        声明 refresh（如 notes.list）则面板事件携带查询结果。刷新意外需确认/失败 →
        回退原数据（刷新不该弹确认打断用户，与 panel._emit_refresh_panel 同一策略）。
        conversation_id 随刷新执行的 meta 透传：项目作用域工具的跟单刷新要与本 run
        同一会话数据边界（与 panel._emit_refresh_panel 同一语义）。

        refresh 传参取「action 入参 ∩ refresh tool 声明参数」（如 save{id,content} → get{id}），
        无交集传 {}（list 类刷新不带条件）。最后做 focus 重定向：用户正盯着同插件 webview
        面板（如写作编辑器）的同一条目时，回跳面板落在该 webview 上而不是硬切走——
        编辑器收到 rows 重推后自行刷新稿件，对话改稿不打断工作台。
        """
        payload = panel_payload(result)
        if payload is None or not result.success:
            return payload
        refresh_id = getattr(self.skills.get(action.tool_id), "refresh", None)
        if not refresh_id:
            # hints 必须在 _redirect_to_focused_webview 之后并入：它重建 dict，会丢掉新字段
            return _with_surface_hints(self._redirect_to_focused_webview(payload), result, action.id)
        r_params: dict = {}
        try:
            props = (
                self.skills.get(refresh_id).openai_schema().get("parameters", {}).get("properties", {})
            )
            r_params = {k: action.params[k] for k in props if k in action.params}
        except Exception:
            r_params = {}
        r_action = self.invoker.propose(
            ToolCall(id=f"refresh_{action.id}", tool_id=refresh_id, params=r_params)
        )
        if self.invoker.decide(r_action) != Decision.AUTO:
            return _with_surface_hints(self._redirect_to_focused_webview(payload), result, action.id)
        r_result = self.invoker.execute(
            r_action, r_params, {"conversation_id": conversation_id or ""}
        )
        refreshed = panel_payload(r_result)
        if refreshed is not None:
            # explicit 属于用户直调的工具；refresh 是内部跟单，不许借它抬表面档位
            # （否则「记个选题」经 refresh=list 也会 stage 抢页）
            if getattr(result, "explicit", False):
                refreshed["explicit"] = True
            else:
                refreshed.pop("explicit", None)
                # 同理压注意力：非用户点名的刷新跟单只刷数据 + 记活动轨（quiet），
                # 不弹 peek——「存个稿/记个选题」不该有浮层抢视线（走查 M2）
                refreshed["attention"] = "quiet"
            payload = refreshed
        return _with_surface_hints(self._redirect_to_focused_webview(payload), result, action.id)

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

    def run(self, user_text: str, surface: str | None = None, conversation_id: str | None = None) -> Iterator[Event]:
        """同步回路（历史按 conversation_id 分桶）。

        实现委托 arun（唯一异步实现），过滤流式 final_reply_chunk 保持同步事件流契约。
        """
        async def _drain() -> list[Event]:
            return [
                event
                async for event in self.arun(user_text, surface=surface, conversation_id=conversation_id)
                if event.kind != "final_reply_chunk"
            ]

        yield from asyncio.run(_drain())

    async def arun(
        self, user_text: str, cancel=None, surface: str | None = None, conversation_id: str | None = None
    ) -> AsyncIterator[Event]:
        """流式异步回路：LLM 边生成边吐 final_reply_chunk；cancel.is_set() 随时打断。

        cancel 为 asyncio.Event（或任何带 is_set() 的对象）。打断时产出 interrupted 并返回；
        返回前把本轮已积累的部分轨迹（user + 已完成的工具调用/结果 + 已有部分回复）落史，
        下一轮上下文能看到中断前已取得的证据（见 _record_interrupted）。
        confirmer 可同步也可异步（返回协程则 await）。
        surface 为会话分流标签（pet / panel:<plugin>）：只落历史，不进发给 provider 的消息。
        conversation_id（M3 会话隔离）：该 run 所属会话，历史按此分桶读写——
        模型上下文只含本会话的最近轮次，不跨会话串台（小窗不再知道你在别的会话问过什么）。
        """
        self._run_start = time.monotonic()  # run_metrics 耗时基准
        memories = await _offload(self.memory.recall, user_text, self.user_id)
        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        focus_msg = self._focus_message()
        if focus_msg:
            messages.append(focus_msg)
        if self.history:
            messages.extend(self.history.messages(conversation_id))
        # 变化内容（记忆/时间戳）放 history 之后：固定段（system+focus+history）整段为缓存前缀。
        # 记忆按 query 语义召回、每次可能不同，放前面会把前缀切断 → 整轮 history+user 全 miss。
        if memories:
            messages.append({"role": "system", "content": "关于用户的记忆：\n" + "\n".join(memories)})
        messages.append(_now_message())
        messages.append({"role": "user", "content": user_text})
        run_start = len(messages) - 1  # 本轮轨迹起点（user 消息），成功收尾时整轮入史（含工具调用）
        safe_tool_content: dict[str, str] = {}
        sensitive_turn = False
        post_reply_notices: list[str] = []
        usage_acc = Usage()  # 整轮 LLM 调用用量累加（流式末尾 usage chunk 收集）

        def _acc_usage(u: Usage) -> None:
            usage_acc.prompt_tokens += u.prompt_tokens
            usage_acc.completion_tokens += u.completion_tokens
            usage_acc.cached_tokens += u.cached_tokens
            usage_acc.total_tokens += u.total_tokens

        def cancelled() -> bool:
            return bool(cancel and cancel.is_set())

        def _interrupted_evidence(partial: str = "") -> None:
            """中断留证：中断路径直接 return、不走正常收尾的 record_messages——
            不落史的话下一轮上下文里「已做过的研究」凭空消失，agent 会对自己的历史自相矛盾。
            partial 为已被打断的流式部分回复。"""
            self._record_interrupted(
                messages, run_start, surface, safe_tool_content, sensitive_turn,
                conversation_id, partial,
            )

        for _ in range(self.max_steps):
            if cancelled():
                _interrupted_evidence()
                yield Event(kind="interrupted")
                return
            text_buf = ""
            delta_acc: list = []
            async for delta in self.provider.astream(messages, tools=self._visible_tools()):
                if cancelled():
                    _interrupted_evidence(text_buf)
                    yield Event(kind="interrupted")
                    return
                if delta.usage is not None:
                    _acc_usage(delta.usage)  # 流式末尾 usage chunk
                if delta.text:
                    text_buf += delta.text
                    yield Event(kind="final_reply_chunk", text=delta.text)
                if delta.tool_call_deltas:
                    delta_acc.extend(delta.tool_call_deltas)
            tool_calls = merge_tool_call_deltas(delta_acc)
            if not tool_calls:
                added = await _offload(self.memory.add, user_text, self.user_id)
                if added and self.feed is not None:
                    h = int(time.time()) // 3600 * 3600
                    self.feed.append_hourly(
                        "event", f"记住了：{user_text[:40]}",
                        {"type": "memory", "hour": h}, h,
                    )
                if self.history:
                    span = messages[run_start:] + [{"role": "assistant", "content": text_buf}]
                    span[0] = _tag_surface(span[0], surface)
                    self.history.record_messages(
                        _history_safe_span(span, safe_tool_content, sensitive_turn),
                        conversation_id,
                    )
                yield Event(kind="final_reply", text=text_buf, payload=self._metrics_payload(usage_acc))
                for notice in post_reply_notices:
                    yield Event(kind="notice", text=notice)
                return
            messages.append(_assistant_with_tools(text_buf, tool_calls))
            proceeded = False
            # Task 2 攒批：先按 LLM 顺序收集决策（不立即执行——AUTO 不抢在 CONFIRM 前，
            # 依赖链 a→b 的 a 仍在 b 前），再一轮批量确认，最后按序执行。spec §3.1。
            plan: list[tuple[ToolCall, Action, Decision]] = []
            for tc in tool_calls:
                if cancelled():
                    _interrupted_evidence()
                    yield Event(kind="interrupted")
                    return
                tc.tool_id = self.skills.resolve_llm_name(tc.tool_id)  # 安全名 → 真实 id
                action = self.invoker.propose(tc)
                yield Event(kind="action_proposed", action=action)
                reason = self.invoker.precheck(action)  # 本地启发式拦截（不执行、不弹审批）
                if reason:
                    yield Event(kind="action_result", action=action,
                                result=ActionResult(success=False, error=reason))
                    messages.append({"role": "tool", "tool_call_id": tc.id,
                                     "content": f"{reason}（未执行，请改用更合适的工具重试）"})
                    proceeded = True
                    continue
                plan.append((tc, action, self.invoker.decide(action)))
            confirm_actions = [a for _tc, a, d in plan if d == Decision.CONFIRM]
            verdicts: dict[str, tuple[bool, bool]] = {}
            if confirm_actions:
                # 一次推收件箱（旧前端读 action=actions[0]，Task 4/5 切 actions）
                yield Event(kind="confirmation_needed", actions=confirm_actions,
                            action=confirm_actions[0], confirmation_id=confirm_actions[0].id)
                verdicts = await self.invoker.batch_confirm(
                    confirm_actions, meta={"conversation_id": conversation_id or ""},
                )
            for tc, action, decision in plan:
                if cancelled():
                    _interrupted_evidence()
                    yield Event(kind="interrupted")
                    return
                if decision == Decision.CONFIRM:
                    approved, remember = verdicts.get(action.id, (False, False))
                    self.invoker.apply_verdict(action, approved, remember)
                    if not approved:
                        yield Event(kind="error", action=action, text=f"用户拒绝执行 {tc.tool_id}")
                        messages.append(
                            {"role": "tool", "tool_call_id": tc.id, "content": "用户拒绝执行该操作"}
                        )
                        continue
                elif decision == Decision.DENY:
                    yield Event(kind="error", text=f"策略禁止执行 {tc.tool_id}（风险过高）")
                    messages.append(
                        {"role": "tool", "tool_call_id": tc.id, "content": "策略禁止该操作"}
                    )
                    continue
                running_loop = asyncio.get_running_loop()

                def request_cancel() -> None:
                    if cancel is not None:
                        running_loop.call_soon_threadsafe(cancel.set)

                result = await _offload(
                    self.invoker.execute,
                    action,
                    tc.params,
                    {
                        "cancel": cancel,
                        "request_cancel": request_cancel,
                        "conversation_id": conversation_id or "",
                        "surface": surface or "",
                    },
                )
                self._auto_activate(action.tool_id)
                skill = self.skills.get(action.tool_id)
                safe = self.invoker.safe_result(action, result)
                yield Event(kind="action_result", action=action, result=safe)
                # 结果先入账再判打断：中断留证（_record_interrupted）读 messages——
                # 已完成的工具结果必须已在其内，否则被占位「未产生结果」盖掉（研究失忆的根）。
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": _stringify_result(result)}
                )
                safe_tool_content[tc.id] = _stringify_result(safe)
                if skill.sensitive_output and result.success:
                    sensitive_turn = True
                if cancelled():
                    _interrupted_evidence()
                    yield Event(kind="interrupted")
                    return
                if action.tool_id == "use_plugin" and result.success and not (result.data or {}).get("already"):
                    # 插件展开要知情（§12-2 已定）：轻提示，不弹窗不打断
                    yield Event(kind="notice", text=(result.data or {}).get("human", "插件已展开"))
                payload = await _offload(self._panel_with_refresh, action, safe, conversation_id)  # 壳侧面板也只拿安全副本
                if payload is not None:
                    yield Event(kind="panel", payload=payload)
                try:
                    notice = skill.post_reply_notice(result)
                except Exception:
                    notice = None
                if notice and notice not in post_reply_notices:
                    post_reply_notices.append(notice)
                proceeded = True
            if not proceeded:
                continue
        # 与同步路径一致：保留 max_steps 的动作硬上限，但额外给一次无工具收口。
        final_messages = messages + [{"role": "system", "content": _TOOL_BUDGET_FINAL_PROMPT}]
        final_text = ""
        final_tool_deltas: list = []
        async for delta in self.provider.astream(final_messages, tools=[]):
            if cancelled():
                _interrupted_evidence(final_text)
                yield Event(kind="interrupted")
                return
            if delta.usage is not None:
                _acc_usage(delta.usage)
            if delta.text:
                final_text += delta.text
                yield Event(kind="final_reply_chunk", text=delta.text)
            if delta.tool_call_deltas:
                final_tool_deltas.extend(delta.tool_call_deltas)
        final_text = final_text.strip()
        if final_tool_deltas or not final_text:
            yield Event(kind="error", text=_TOOL_BUDGET_FALLBACK)
            return
        if self.history:
            span = messages[run_start:] + [{"role": "assistant", "content": final_text}]
            span[0] = _tag_surface(span[0], surface)
            self.history.record_messages(
                _history_safe_span(span, safe_tool_content, sensitive_turn),
                conversation_id,
            )
        yield Event(kind="final_reply", text=final_text, payload=self._metrics_payload(usage_acc))
        for notice in post_reply_notices:
            yield Event(kind="notice", text=notice)

    def _record_interrupted(
        self,
        messages: list[dict],
        run_start: int,
        surface: str | None,
        safe_tool_content: dict[str, str],
        sensitive_turn: bool,
        conversation_id: str | None,
        partial_text: str = "",
    ) -> None:
        """中断路径落史：user + 已完成的工具调用/结果 + 已有部分回复，以「已打断」收尾。

        与正常收尾同一写入格式（_tag_surface + _history_safe_span）；正常路径在 record_messages
        后直接 return，到不了这里，不会写两遍。悬空的 tool_calls（被打断时还没执行/出结果）
        补占位 tool 结果——严格校验的 provider（DeepSeek 等）遇悬空 tool_calls 下一轮直接 400。
        敏感轮次的占位替换由 _history_safe_span 兜底（敏感安全优先于打断标注）。
        """
        if not self.history:
            return
        span = list(messages[run_start:])
        answered = {m.get("tool_call_id") for m in span if m.get("role") == "tool"}
        dangling = [
            tc["id"]
            for m in span
            for tc in (m.get("tool_calls") or [])
            if tc.get("id") and tc["id"] not in answered
        ]
        for tc_id in dangling:
            span.append({"role": "tool", "tool_call_id": tc_id,
                         "content": "（执行被中断，未产生结果）"})
        span.append({"role": "assistant",
                     "content": f"{partial_text}\n【已打断】" if partial_text else "【已打断】"})
        span[0] = _tag_surface(span[0], surface)
        self.history.record_messages(
            _history_safe_span(span, safe_tool_content, sensitive_turn),
            conversation_id,
        )

    def _metrics_payload(self, usage: Usage) -> dict:
        """整轮用量 → final_reply 的 payload 里带 metrics（token/cost/elapsed）。

        放 final_reply 而非独立事件：不破坏「run 以 final_reply 收尾」的既有契约，
        前端拿到 final_reply 即获得统计。
        """
        cost = compute_cost(self.provider.model if hasattr(self.provider, "model") else "", usage)
        elapsed_ms = int((time.monotonic() - self._run_start) * 1000) if self._run_start else 0
        return {
            "metrics": {
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "cached_tokens": usage.cached_tokens,
                "total_tokens": usage.total_tokens,
                "cost": cost,
                "elapsed_ms": elapsed_ms,
                "model": getattr(self.provider, "model", ""),
            }
        }


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
                    "name": tc.tool_id,
                    "arguments": json.dumps(tc.params, ensure_ascii=False),
                },
            }
            for tc in tool_calls
        ]
    return msg


def _stringify_result(result) -> str:
    payload = {"success": result.success, "data": result.data, "error": result.error}
    return json.dumps(payload, ensure_ascii=False)


def _history_safe_span(
    span: list[dict], safe_tool_content: dict[str, str], sensitive: bool
) -> list[dict]:
    """复制本轮轨迹并替换敏感工具结果；敏感回答仅保留不可逆占位说明。"""
    out: list[dict] = []
    for message in span:
        item = dict(message)
        tool_call_id = item.get("tool_call_id")
        if item.get("role") == "tool" and tool_call_id in safe_tool_content:
            item["content"] = safe_tool_content[tool_call_id]
        out.append(item)
    if sensitive and out and out[-1].get("role") == "assistant":
        out[-1]["content"] = "【本轮使用敏感工具回答，敏感内容未写入会话历史】"
    return out
