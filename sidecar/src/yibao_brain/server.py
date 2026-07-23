"""stdio 行分隔 JSON 服务：把 AgentLoop 接到桌面壳（Phase B 的 Tauri 侧）。

协议（脑→壳）：hello（启动握手，含权限状态）、pong、permissions、event、run_done。
协议（壳→脑）：run、confirm、voice_start、interrupt、ping、check_permissions、prompt_permission、panel_context。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from collections.abc import Callable

from . import permissions
from .audit import AuditLog
from .config import a11y_enabled, computer_use_enabled, history_path, llm_api_key, screenshot_dir, stt_model_dir, tts_voice, vad_max_seconds, vad_min_silence, vad_model_path, voice_enabled
from .history import ConversationHistory
from .ipc import Action, Event, RiskLevel
from .llm import FakeProvider, GLMProvider, ToolCall
from .loop import AgentLoop, _offload
from .memory import FakeMemory, LazyMem0Memory
from .plugins import get_api, panel_payload
from .safety import Decision, Gate, GatePolicy, RiskClassifier
from .skills import EchoSkill, SkillRegistry
from .skills_composite import register_composite_skills
from .skills_real import ComputerUseSkill, register_real_skills

ReadMsg = Callable[[], dict | None]
WriteMsg = Callable[[dict], None]

# 面板焦点（v2 §5）：壳侧 panel_context 消息维护，run 时注入 LLM 上下文（「这个/它」有解）
_FOCUS: dict = {"value": None}

# 被抢占任务的收尾宽限（秒）：超时强制取消，防 hung 任务把槽位卡死（「点了没反应」的根）
_PREEMPT_GRACE_S = 8.0

# 看门狗心跳：pong 改由读线程直接应答（见 serve_async._reader），不经事件循环——
# 循环被长任务占住时照样 pong（忙 ≠ 死，历史误杀的根）；
# 但循环 _TICK_FRESH_S 秒没调度（真卡死）→ 扣住 pong，让看门狗杀掉重启。
_LOOP_TICK = {"t": 0.0}
_TICK_FRESH_S = 12.0


def _permissions_status() -> dict:
    """检测辅助功能/屏幕录制权限；检测本身失败时乐观返回 True（不出误报 banner）。"""
    try:
        return {"ax": permissions.check_ax(), "screen": permissions.check_screen()}
    except Exception:
        return {"ax": True, "screen": True}


def build_loop(
    read_msg: ReadMsg,
    use_real: bool,
    db_path: str,
    provider=None,
    skills_factory=None,
    confirmer=None,
    history_file: str | None = None,
) -> AgentLoop:
    real_a11y = use_real and a11y_enabled() and sys.platform == "darwin"
    reg = skills_factory() if skills_factory else SkillRegistry()
    if not skills_factory:
        reg.register(EchoSkill())
        if real_a11y:
            register_real_skills(reg)
            register_composite_skills(reg)
            if llm_api_key() and computer_use_enabled():
                try:
                    from .llm import ComputerUseClient

                    reg.register(ComputerUseSkill(ComputerUseClient()))
                except Exception as e:
                    print(f"[yibao] computer-use 兜底未启用：{e}", file=sys.stderr)

    if provider is not None:
        prov = provider
    else:
        prov = GLMProvider() if (use_real and llm_api_key()) else FakeProvider(text="(未配置 LLM key，使用 fake 回复)")

    try:
        # 懒加载：构造秒回（不 import torch/mem0），真实 mem0 后台线程就绪后接入
        memory = LazyMem0Memory() if use_real else FakeMemory()
    except Exception:
        memory = FakeMemory()

    host = None
    if real_a11y:
        try:
            from .mac.host_mac import MacHost

            host = MacHost(screenshot_dir=screenshot_dir())
        except Exception as e:  # pyobjc 未装 / 非 mac → 回退无基座（技能会优雅报错）
            print(f"[yibao] MacHost 不可用，回退无基座：{e}", file=sys.stderr)

    active_plugins: set | None = None  # None=全量暴露（测试/兼容）；集合=路由式暴露
    reminder_store = None
    if use_real and not skills_factory:
        # 底座提醒存储先建：提醒管理插件（reminders capability）与底座技能共享同一实例
        from .reminders import ReminderStore, make_skills

        reminder_store = ReminderStore(os.path.join(os.path.dirname(db_path), "reminders.json"))
        _load_plugins_safe(reg, memory, prov, host, reminders=reminder_store)
        # 路由式暴露（§12-2）：插件 tool 默认隐藏，use_plugin 按需展开；
        # active 集合与 AgentLoop 共享（技能执行即改，下一步 LLM 调用即见新工具）
        from .plugins import get_plugin_summaries
        from .skills import UsePluginSkill

        active_plugins = set()
        reg.register(UsePluginSkill(reg, active_plugins, get_plugin_summaries()))
        for sk in make_skills(reminder_store):
            reg.register(sk)

    def default_confirmer(action) -> bool:
        # 由 serve 在 confirmation_needed 事件之后触发；阻塞读壳的回答
        ans = read_msg() or {}
        return bool(ans.get("approved", False))

    # 会话历史：仅真实模式默认落盘（fake/测试模式不污染本地文件）
    hist = history_file or (history_path() if use_real else None)

    agent = AgentLoop(
        provider=prov,
        skills=reg,
        classifier=RiskClassifier(),
        gate=Gate(GatePolicy(auto_below_or_equal=RiskLevel.L1_LOW)),  # L2+ 走确认
        memory=memory,
        log=AuditLog(db_path),
        confirmer=confirmer or default_confirmer,
        host=host,
        history=ConversationHistory(hist) if hist else None,
        focus_provider=lambda: _FOCUS["value"],
        active_plugins=active_plugins,
    )
    if use_real and not skills_factory:
        agent.reminder_store = reminder_store  # serve 的调度循环经它触发提醒
    return agent


def _load_plugins_safe(reg, memory, prov, host, reminders=None) -> None:
    """加载 <repo>/plugins 下的插件（env YIBAO_PLUGINS_DIR 可覆盖）。

    只在 use_real 且无自定义 skills_factory 时调用（测试不碰真实文件系统）；
    整个加载过程再兜一层 try：插件系统任何问题都不许拖垮底座启动。
    """
    try:
        from pathlib import Path

        from .plugins import HttpClient, LlmChat, load_plugins

        # sidecar/src/yibao_brain/server.py → 上四级即仓库根
        default_dir = Path(__file__).resolve().parents[3] / "plugins"
        plugins_dir = os.environ.get("YIBAO_PLUGINS_DIR") or str(default_dir)
        results = load_plugins(
            plugins_dir, reg,
            memory=memory, http=HttpClient(), llm=LlmChat(prov),
            host_available=host is not None, reminders=reminders,
        )
        for pid, status in results.items():
            print(f"[yibao] 插件 {pid}: {status}", file=sys.stderr)
    except Exception as e:
        print(f"[yibao] 插件加载失败（已跳过）：{e}", file=sys.stderr)


class _KeepMissing(dict):
    """format_map 缺键时保留 {key} 原样（intent 渲染不炸）。"""

    def __missing__(self, key):
        return "{" + key + "}"


def _render_intent(api, params: dict) -> str:
    """intent 模板用 params 渲染（{key} 占位）；无 intent 用「调用 <handler>」。"""
    template = api.intent or f"调用 {api.handler}"
    return template.format_map(_KeepMissing(params))


async def _emit_refresh_panel(agent: AgentLoop, emit, refresh_tool: str) -> None:
    """直调成功后的声明式刷新：执行查询 tool（应为本插件 L0 只读），把它的 panel 事件推给壳。

    刷新 tool 若意外需要确认/被拒，静默跳过（不弹确认——刷新不该打断用户）。
    """
    action = agent.invoker.propose(ToolCall(id=f"pa_refresh_{id(emit)}", skill_id=refresh_tool, params={}))
    if agent.invoker.decide(action) != Decision.AUTO:
        return
    result = await _offload(agent.invoker.execute, action, {})
    payload = panel_payload(result)
    if payload is not None:
        emit(Event(kind="panel", payload=payload))


async def handle_panel_action(msg: dict, agent: AgentLoop, write_msg: WriteMsg, *, run_text) -> None:
    """处理壳侧 panel_action（v2 §7）：api.toml 白名单内的面板方法。

    direct=true：invoker 直调（propose → api.risk 只许收紧 → decide → 确认/执行 → 审计）；
    direct=false：intent 渲染后交给 run_text（与 type="run" 同路径的 agent 流程）。
    """
    surface = str(msg.get("surface") or "pet")  # 会话分流：事件随发起场景标记，壳侧各窗按 surface 过滤

    def emit(event: Event) -> None:
        write_msg({"type": "event", "surface": surface, "event": event.model_dump(mode="json")})

    rid = msg.get("id")
    method = ""
    tag = Action(id=f"pa_{rid}", skill_id="?")  # 错误事件归属标签：壳侧桥按 pa_<rid> 认领，不误杀其他调用
    try:
        method = str(msg.get("method", ""))
        tag = Action(id=f"pa_{rid}", skill_id=method or "?")
        params = msg.get("params") or {}
        api = get_api(method)
        if api is None:  # 白名单外：拒绝执行
            emit(Event(kind="error", text=f"面板方法未在白名单：{method}", action=tag))
            write_msg({"type": "run_done", "id": rid})
            return
        if not api.direct:
            await run_text(_render_intent(api, params), rid)
            return

        action = agent.invoker.propose(ToolCall(id=f"pa_{rid}", skill_id=api.handler, params=params))
        action.id = f"pa_{rid}"  # propose 会重新发 id；壳侧桥靠 pa_<rid> 关联回包/确认/错误，必须保留
        if api.risk is not None:
            action.risk = max(action.risk, api.risk)  # api.toml 只许收紧，不许放宽
        decision = agent.invoker.decide(action)
        if decision == Decision.DENY:
            emit(Event(kind="error", text=f"策略禁止执行 {api.handler}（风险过高）", action=action))
            write_msg({"type": "run_done", "id": rid})
            return
        if decision == Decision.CONFIRM:
            emit(Event(kind="confirmation_needed", action=action, confirmation_id=action.id))
            if not await agent.invoker.confirm(action):  # 等壳 confirm 消息（复用单槽确认流）
                emit(Event(kind="error", text=f"用户拒绝执行 {api.handler}", action=action))
                write_msg({"type": "run_done", "id": rid})
                return
        result = await _offload(agent.invoker.execute, action, params)  # 与 arun 一致挪线程池
        emit(Event(kind="action_result", action=action, result=result))
        if result.success and api.refresh is not None:
            # 声明式刷新：删除类操作后跟一次查询，面板拿新数据而不是操作回执
            await _emit_refresh_panel(agent, emit, api.refresh)
        else:
            if result.success and api.panel is not None:
                result.panel = api.panel  # method 声明的面板优先于 tool 自带引用（如 webview 编辑器）
            payload = panel_payload(result)
            if payload is not None:
                emit(Event(kind="panel", payload=payload))
        write_msg({"type": "run_done", "id": rid})
    except Exception as e:  # 兜底：任何意外都要给壳一个交代，别让面板卡死
        emit(Event(kind="error", text=f"面板操作失败：{e}", action=tag))
        write_msg({"type": "run_done", "id": rid})


async def _readonly_no_run(text: str, rid) -> None:
    """L0 只读直调永远不会走 agent 路径（direct=true 才并发）；防御性兜底。"""
    raise RuntimeError("只读直调不应进入 agent 路径")


def _is_readonly_direct(msg: dict, agent: AgentLoop) -> bool:
    """L0 只读直调（get/list/article_read 等纯查询）→ 不占槽位、不抢占。

    面板/编辑器的数据加载与在跑的对话是并行关系：互相抢占会让 read_article 顶掉
    写稿 run（回复截断），也让 run 期间的面板加载被排队/取消（「编辑器没反应」）。
    db 层单连接+锁，并发读安全。
    """
    api = get_api(str(msg.get("method", "")))
    if api is None or not api.direct:
        return False
    action = agent.invoker.propose(
        ToolCall(id=f"pa_{msg.get('id')}", skill_id=api.handler, params=msg.get("params") or {})
    )
    if api.risk is not None:
        action.risk = max(action.risk, api.risk)
    return action.risk <= RiskLevel.L0_READONLY


def _run_and_emit(loop: AgentLoop, text: str, write_msg: WriteMsg, rid, voice=None) -> None:
    for event in loop.run(text):
        write_msg({"type": "event", "event": event.model_dump(mode="json")})
        if voice is not None and event.kind == "final_reply" and event.text:
            write_msg({"type": "event", "event": {"kind": "speaking"}})
            try:
                voice.speak(event.text)
            except Exception as e:
                write_msg({"type": "event", "event": {"kind": "error", "text": f"语音播报失败：{e}"}})
    write_msg({"type": "run_done", "id": rid})


def serve(loop: AgentLoop, read_msg: ReadMsg, write_msg: WriteMsg, voice=None) -> None:
    while True:
        req = read_msg()
        if req is None:
            return
        rtype = req.get("type")
        if rtype == "run":
            _run_and_emit(loop, req.get("text", ""), write_msg, req.get("id"), voice)
        elif rtype == "voice_start" and voice is not None:
            write_msg({"type": "event", "event": {"kind": "listening"}})
            try:
                text = voice.listen()
            except Exception as e:
                write_msg({"type": "event", "event": {"kind": "error", "text": f"语音识别失败：{e}"}})
                write_msg({"type": "run_done", "id": req.get("id")})
                continue
            write_msg({"type": "event", "event": {"kind": "listening_done", "text": text}})
            if text:
                _run_and_emit(loop, text, write_msg, req.get("id"), voice)
            else:
                write_msg({"type": "run_done", "id": req.get("id")})


async def serve_async(
    read_msg: ReadMsg,
    write_msg: WriteMsg,
    *,
    use_real: bool = False,
    db_path: str = "audit.db",
    voice=None,
    provider=None,
    skills_factory=None,
) -> None:
    """异步控制平面：stdin 读线程 → asyncio.Queue → 分发；支持 interrupt 打断。

    与同步 serve 的关键差异：读消息在独立线程，故生成/TTS 进行中仍能收到 interrupt，
    cancel_event 一键"三连取消"（停 TTS + 终止 LLM 生成 + 清 TTS 队列）。
    新 run 到来会抢占并打断未完成的旧 run。
    """
    ai_loop = asyncio.get_running_loop()
    _LOOP_TICK["t"] = time.monotonic()

    async def _tick() -> None:
        """主循环存活刻度：只要循环还能调度就每秒前进；读线程据此判断忙/死。"""
        while True:
            _LOOP_TICK["t"] = time.monotonic()
            await asyncio.sleep(1)

    tick_task = asyncio.ensure_future(_tick())
    queue: asyncio.Queue = asyncio.Queue()
    # 确认单槽 + 早到缓存：confirm 可能先于 confirmer 注册 future 到达
    # （读线程瞬时投递 run+confirm，主循环先处理 confirm），直接丢会死锁。
    pending_confirm: dict = {"future": None, "early": None}
    # preempt_gen：抢占代数。新请求到来即 +1；排队中的任务启动时发现自己落后 →
    # 一启动即置 cancel（快速跳过），保证「只有最新请求真正执行」。
    run_state: dict = {"task": None, "cancel": None, "preempt_gen": 0}
    # 并发的 L0 只读面板调用（不占槽位）：跟踪起来，stdin 关闭时一起收尾
    readonly_tasks: set[asyncio.Task] = set()

    async def confirmer(action) -> bool:
        # 早到的 confirm 直接兑现
        if pending_confirm["early"] is not None:
            approved = pending_confirm["early"]
            pending_confirm["early"] = None
            return bool(approved)
        # 单槽 future：收到任意 confirm 消息即兑现（v1 run 串行，确认也串行）
        fut = ai_loop.create_future()
        pending_confirm["future"] = fut
        # 确认等待必须响应抢占/打断：否则新请求 join 一个永不结束的确认 →
        # 派发循环卡死、ping 不应答、看门狗误杀（2026-07-19 复现确认）
        cancel = run_state["cancel"]
        cancel_wait = ai_loop.create_task(cancel.wait()) if cancel is not None else None
        skill_id = getattr(action, "skill_id", "?")
        print(f"[yibao] 等待用户确认：{skill_id}", file=sys.stderr)
        try:
            waiters: set = {fut}
            if cancel_wait is not None:
                waiters.add(cancel_wait)
            done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            if fut in done:
                approved = bool(fut.result())
                print(f"[yibao] 确认结果：{'允许' if approved else '拒绝'}（{skill_id}）", file=sys.stderr)
                return approved
            print(f"[yibao] 确认被抢占取消：{skill_id}", file=sys.stderr)
            return False
        finally:
            if cancel_wait is not None:
                cancel_wait.cancel()
            if pending_confirm["future"] is fut:
                pending_confirm["future"] = None

    agent = build_loop(
        read_msg, use_real, db_path, provider, skills_factory, confirmer=confirmer
    )
    # mem0 降级（如多实例争 qdrant 锁）→ 显式推到壳，别让「失忆」无声发生
    mem = getattr(agent, "memory", None)
    if hasattr(mem, "set_status_callback"):
        mem.set_status_callback(
            lambda text: ai_loop.call_soon_threadsafe(
                write_msg, {"type": "event", "event": {"kind": "error", "text": text}}
            )
        )
    # 启动握手：壳靠它确认大脑上线（守护重启后也靠它判断已恢复）
    write_msg({"type": "hello", "version": 1, "permissions": _permissions_status()})

    async def _reminder_loop() -> None:
        """主动能力：每 10s 扫到期提醒 → 推 reminder 事件到壳；空闲时顺手语音播报。"""
        store = getattr(agent, "reminder_store", None)
        if store is None:
            return
        while True:
            await asyncio.sleep(10)
            try:
                due = await _offload(store.pop_due, time.time())
            except Exception as e:
                print(f"[yibao] 提醒扫描失败：{e}", file=sys.stderr)
                continue
            for r in due:
                text = str(r.get("text", ""))
                print(f"[yibao] 提醒触发 id={r.get('id')}：{text[:30]!r}", file=sys.stderr)
                write_msg({"type": "event", "surface": "pet",
                           "event": {"kind": "reminder", "text": text}})
                if agent.history:  # 落历史：用户回「知道了」时大脑有上下文
                    try:
                        await _offload(agent.history.record_messages,
                                       [{"role": "assistant", "content": f"⏰ 到点提醒：{text}"}])
                    except Exception:
                        pass
                # 有任务在跑就只在气泡里提醒，不打断在播的语音
                task = run_state["task"]
                if voice is not None and (task is None or task.done()):
                    async def _once(t=text):
                        yield f"提醒：{t}"
                    try:
                        write_msg({"type": "event", "surface": "pet", "event": {"kind": "speaking"}})
                        await voice.speak_stream(_once(), asyncio.Event())
                    except Exception as e:
                        print(f"[yibao] 提醒播报失败：{e}", file=sys.stderr)
                    write_msg({"type": "event", "surface": "pet", "event": {"kind": "speaking_done"}})

    reminder_task = asyncio.ensure_future(_reminder_loop())

    def _reader():
        while True:
            msg = read_msg()
            # 看门狗心跳：读线程直接答 pong（循环被长任务占住时也不误杀）；
            # 循环 _TICK_FRESH_S 秒未调度 = 真卡死 → 扣住 pong 让看门狗杀掉重启
            if isinstance(msg, dict) and msg.get("type") == "ping":
                lag = time.monotonic() - _LOOP_TICK["t"]
                if lag < _TICK_FRESH_S:
                    write_msg({"type": "pong"})
                else:
                    print(f"[yibao] 主循环 {lag:.0f}s 未调度，扣住 pong 待看门狗处置", file=sys.stderr)
                continue
            try:
                ai_loop.call_soon_threadsafe(queue.put_nowait, msg)
            except RuntimeError:
                return  # 事件循环已关（进程退出中），daemon 读者线程随之结束
            if msg is None:
                return

    threading.Thread(target=_reader, daemon=True).start()

    async def _tts_chunks(tts_q: asyncio.Queue):
        while True:
            item = await tts_q.get()
            if item is None:
                return
            yield item

    async def _pump_tts(tts_q: asyncio.Queue, cancel: asyncio.Event, surface: str = "pet"):
        if voice is None:
            return
        try:
            await voice.speak_stream(_tts_chunks(tts_q), cancel)
        except asyncio.CancelledError:
            return  # 打断命中合成/播放的正常取消，不是播报失败
        except Exception as e:
            write_msg({"type": "event", "surface": surface, "event": {"kind": "error", "text": f"语音播报失败：{e}"}})
            return
        if not cancel.is_set():
            write_msg({"type": "event", "surface": surface, "event": {"kind": "speaking_done"}})

    async def _stream_agent(text: str, rid, cancel: asyncio.Event, surface: str = "pet"):
        t0 = time.monotonic()
        tts_q: asyncio.Queue | None = asyncio.Queue() if voice is not None else None
        tts_task = asyncio.create_task(_pump_tts(tts_q, cancel, surface)) if tts_q is not None else None
        started_speaking = False
        try:
            async for event in agent.arun(text, cancel, surface=surface):
                write_msg({"type": "event", "surface": surface, "event": event.model_dump(mode="json")})
                if (
                    tts_q is not None
                    and event.kind == "final_reply_chunk"
                    and event.text
                ):
                    if not started_speaking:
                        started_speaking = True
                        write_msg({"type": "event", "surface": surface, "event": {"kind": "speaking"}})
                    await tts_q.put(event.text)
        except Exception as e:
            # arun 抛异常（如 provider 400）→ 发 error + 停 TTS，别让前端卡死
            cancel.set()
            write_msg({"type": "event", "surface": surface, "event": {"kind": "error", "text": f"大脑出错：{e}"}})
        finally:
            if tts_q is not None:
                await tts_q.put(None)  # 收尾哨兵，唤醒可能在 get() 上等待的 _pump_tts
            if tts_task is not None:
                await tts_task
            write_msg({"type": "run_done", "id": rid})
            print(f"[yibao] run 完成 rid={rid}（{time.monotonic() - t0:.1f}s）", file=sys.stderr)

    async def _drive_run(text: str, rid, cancel: asyncio.Event, surface: str = "pet"):
        await _stream_agent(text, rid, cancel, surface)

    async def _drive_voice_start(rid, cancel: asyncio.Event, surface: str = "pet"):
        write_msg({"type": "event", "surface": surface, "event": {"kind": "listening"}})

        async def _watch_cancel():
            await cancel.wait()
            voice.stop_listen()  # 打断（interrupt）→ 录音循环下一拍退出

        watcher = asyncio.ensure_future(_watch_cancel())
        t0 = time.monotonic()
        try:
            text = await ai_loop.run_in_executor(None, voice.listen)
        except Exception as e:
            write_msg({"type": "event", "surface": surface, "event": {"kind": "error", "text": f"语音识别失败：{e}"}})
            write_msg({"type": "run_done", "id": rid})
            return
        finally:
            watcher.cancel()
        print(f"[yibao] 聆听结束（{time.monotonic() - t0:.1f}s）：{text[:30]!r}", file=sys.stderr)
        if cancel.is_set():  # 聆听被打断：不走 listening_done（避免误进 think 态）
            write_msg({"type": "event", "surface": surface, "event": {"kind": "interrupted"}})
            write_msg({"type": "run_done", "id": rid})
            return
        write_msg({"type": "event", "surface": surface, "event": {"kind": "listening_done", "text": text}})
        if text:
            await _stream_agent(text, rid, cancel, surface)
        else:
            write_msg({"type": "run_done", "id": rid})

    def _preempt_current():
        run_state["preempt_gen"] += 1
        if run_state["cancel"] is not None:
            run_state["cancel"].set()

    async def _chain_start(prev, start, queued_gen: int) -> None:
        """槽位串行：等上一任务收尾再启动；主循环不在这里阻塞（ping 照答，看门狗不误杀）。

        排队期间又来了更新的请求（preempt_gen 前进）→ 本任务一启动即置 cancel 快速跳过。
        上一任务被抢占后超过 _PREEMPT_GRACE_S 仍不收尾（LLM/TTS hung 等）→ 强制取消，
        槽位必须自愈，否则后续所有请求都静默排队（「点了没反应」）。
        """
        if prev is not None and not prev.done():
            t0 = time.monotonic()
            print("[yibao] 新请求排队，等上一任务收尾…", file=sys.stderr)
            try:
                # shield：wait_for 超时不许连带取消 prev，强制取消由我们自己控制
                await asyncio.wait_for(asyncio.shield(prev), timeout=_PREEMPT_GRACE_S)
            except asyncio.TimeoutError:
                print(f"[yibao] 上一任务 {_PREEMPT_GRACE_S:.0f}s 未收尾，强制取消", file=sys.stderr)
                prev.cancel()
                try:
                    await prev
                except (asyncio.CancelledError, Exception):
                    pass
            except (asyncio.CancelledError, Exception):
                pass  # prev 自身异常/被取消都算已收尾
            print(f"[yibao] 上一任务收尾完成（{time.monotonic() - t0:.1f}s）", file=sys.stderr)
        cancel = asyncio.Event()
        if run_state["preempt_gen"] > queued_gen:
            cancel.set()
        run_state["cancel"] = cancel
        run_state["task"] = asyncio.current_task()
        try:
            await start(cancel)
        except Exception as e:  # 兜底：任务未预期的异常不能毒死槽位
            print(f"[yibao] 任务异常收尾：{type(e).__name__}: {e}", file=sys.stderr)

    while True:
        msg = await queue.get()
        if msg is None:
            # stdin 关闭（壳退出）：不再接新活。给在跑任务 5s 自然收尾；
            # 超时说明它卡死了（确认未答/hung）→ 取消 + 2s 清场 → 强 cancel。
            # 不能无限等：否则大脑变孤儿占着 qdrant 锁/麦
            # （2026-07-19 实测孤儿 brain 存活 3 小时，新 brain 被迫记忆降级）。
            task = run_state["task"]
            if task is not None and not task.done():
                done, _ = await asyncio.wait({task}, timeout=5)
                if not done:
                    if run_state["cancel"] is not None:
                        run_state["cancel"].set()
                    done, _ = await asyncio.wait({task}, timeout=2)
                    if not done:
                        task.cancel()
            # 并发的只读面板调用都是快查询，给 3s 收尾；超时直接取消（进程要退了）
            if readonly_tasks:
                _, pending_ro = await asyncio.wait(readonly_tasks, timeout=3)
                for t in pending_ro:
                    t.cancel()
            tick_task.cancel()
            reminder_task.cancel()
            return
        rtype = msg.get("type")
        if rtype in ("run", "voice_start"):
            if rtype == "voice_start" and voice is None:
                # 语音不可用（未启用/初始化失败）：不许静默吞掉——前端会永远卡「聆听中」
                rid = msg.get("id")
                print("[yibao] voice_start 收到但语音栈不可用", file=sys.stderr)
                write_msg({"type": "event", "event": {"kind": "error", "text": "语音不可用：麦克风初始化失败或被禁用"}})
                write_msg({"type": "run_done", "id": rid})
                continue
            _preempt_current()
            prev = run_state["task"]
            surface = str(msg.get("surface") or "pet")  # 会话分流：随 run 贯穿事件流与历史
            if rtype == "run":
                text, rid = msg.get("text", ""), msg.get("id")
                start = lambda c, t=text, r=rid, s=surface: _drive_run(t, r, c, s)
                print(f"[yibao] run 受理 rid={rid} surface={surface}：{text[:30]!r}", file=sys.stderr)
            elif voice is not None:
                rid = msg.get("id")
                start = lambda c, r=rid, s=surface: _drive_voice_start(r, c, s)
                print(f"[yibao] voice_start 受理 rid={rid} surface={surface}", file=sys.stderr)
            else:
                continue
            run_state["task"] = asyncio.ensure_future(
                _chain_start(prev, start, run_state["preempt_gen"])
            )
        elif rtype == "panel_action":
            if _is_readonly_direct(msg, agent):
                # L0 只读直调：独立任务并发跑，不占槽位、不抢占在跑的 run（编辑器/面板加载数据不该踩对话）
                async def _ro(m=msg):
                    try:
                        await handle_panel_action(m, agent, write_msg, run_text=_readonly_no_run)
                    except Exception as e:
                        print(f"[yibao] 只读面板调用异常：{type(e).__name__}: {e}", file=sys.stderr)

                t = asyncio.ensure_future(_ro())
                readonly_tasks.add(t)
                t.add_done_callback(readonly_tasks.discard)
                continue
            # 面板写操作/意图方法：与 run 同槽位（抢占 + 链式排队，主循环不阻塞）
            _preempt_current()
            prev = run_state["task"]
            surface = str(msg.get("surface") or "pet")
            start = lambda c, m=msg, s=surface: handle_panel_action(
                m, agent, write_msg, run_text=lambda text, rid: _stream_agent(text, rid, c, s)
            )
            run_state["task"] = asyncio.ensure_future(
                _chain_start(prev, start, run_state["preempt_gen"])
            )
        elif rtype == "interrupt":
            _preempt_current()
        elif rtype == "panel_context":
            # 壳上面板焦点变化：存下来，下次 run 注入 LLM 上下文
            _FOCUS["value"] = msg.get("focus")
        elif rtype == "confirm":
            fut = pending_confirm["future"]
            if fut is not None and not fut.done():
                fut.set_result(bool(msg.get("approved", False)))
            else:
                # confirmer 还没注册（消息先于 run 任务到达）→ 缓存，由 confirmer 兑现
                pending_confirm["early"] = bool(msg.get("approved", False))
        elif rtype == "check_permissions":
            write_msg({"type": "permissions", "permissions": _permissions_status()})
        elif rtype == "prompt_permission":
            which = msg.get("which")
            if which == "ax":
                permissions.prompt_ax()
            elif which == "screen":
                permissions.prompt_screen()
            write_msg({"type": "permissions", "permissions": _permissions_status()})


def _line_reader() -> ReadMsg:
    def _r() -> dict | None:
        line = sys.stdin.readline()
        if not line:
            return None
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None
    return _r


def _line_writer() -> WriteMsg:
    lock = threading.Lock()  # pong 由读线程直发，与主循环消息共享 stdout，防行交错

    def _w(msg: dict) -> None:
        with lock:
            sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
            sys.stdout.flush()

    return _w


def _build_voice_or_none():
    if not (voice_enabled() and sys.platform == "darwin"):
        return None
    try:
        from .voice import build_voice

        return build_voice(
            stt_model_dir(),
            vad_model_path(),
            tts_voice(),
            min_silence=vad_min_silence(),
            max_seconds=vad_max_seconds(),
        )
    except Exception as e:
        print(f"[yibao] 语音不可用，已禁用：{e}", file=sys.stderr)
        return None


def _watch_parent() -> None:
    """父进程存活看门狗（守护线程）：ppid 变化（壳死后被 reparent 到 launchd）→ 自我了断。

    覆盖壳被 kill -9/崩溃时 stdin EOF 之外的遗漏路径——孤儿 brain 会长期占着 qdrant 锁，
    新 brain 被迫记忆降级（2026-07-21 实测两个昨日孤儿并存）。os._exit 保证循环卡死时也能死。
    """
    parent = os.getppid()
    while True:
        time.sleep(10)
        if os.getppid() != parent:
            print("[yibao] 父进程已退出（ppid 变化），自我了断", file=sys.stderr)
            os._exit(0)


def main() -> int:
    reader, writer = _line_reader(), _line_writer()
    voice = _build_voice_or_none()
    threading.Thread(target=_watch_parent, daemon=True).start()
    # 数据目录分离：仓库时代的用户数据一次性迁走（sidecar/ → 应用数据目录）
    from . import config as _cfg

    _cfg.migrate_legacy_data(os.path.join(os.path.dirname(__file__), "..", ".."))
    os.makedirs(_cfg.data_dir(), exist_ok=True)  # sqlite/qdrant 不会自建父目录
    # 单实例锁 + 孤儿回收：壳被强杀时旧大脑可能活着独占 qdrant 锁，
    # 新大脑取锁前先把它们收掉；锁 fd 活到进程结束（OS 级，死即释）
    from .instance import ensure_single_instance

    try:
        _instance_lock_fd = ensure_single_instance(os.path.join(_cfg.data_dir(), "brain.lock"))
    except Exception as e:
        print(f"[yibao] 大脑单实例锁获取失败：{e}", file=sys.stderr)
        return 1
    asyncio.run(
        serve_async(
            reader,
            writer,
            use_real=True,
            db_path=_cfg.audit_db_path(),
            voice=voice,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
