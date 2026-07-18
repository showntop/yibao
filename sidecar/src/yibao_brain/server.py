"""stdio 行分隔 JSON 服务：把 AgentLoop 接到桌面壳（Phase B 的 Tauri 侧）。

协议（脑→壳）：hello（启动握手，含权限状态）、pong、permissions、event、run_done。
协议（壳→脑）：run、confirm、voice_start、interrupt、ping、check_permissions、prompt_permission。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from collections.abc import Callable

from . import permissions
from .audit import AuditLog
from .config import a11y_enabled, computer_use_enabled, history_path, llm_api_key, screenshot_dir, stt_model_dir, tts_voice, vad_max_seconds, vad_min_silence, vad_model_path, voice_enabled
from .history import ConversationHistory
from .ipc import Event, RiskLevel
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

    if use_real and not skills_factory:
        _load_plugins_safe(reg, memory, prov, host)

    def default_confirmer(action) -> bool:
        # 由 serve 在 confirmation_needed 事件之后触发；阻塞读壳的回答
        ans = read_msg() or {}
        return bool(ans.get("approved", False))

    # 会话历史：仅真实模式默认落盘（fake/测试模式不污染本地文件）
    hist = history_file or (history_path() if use_real else None)

    return AgentLoop(
        provider=prov,
        skills=reg,
        classifier=RiskClassifier(),
        gate=Gate(GatePolicy(auto_below_or_equal=RiskLevel.L1_LOW)),  # L2+ 走确认
        memory=memory,
        log=AuditLog(db_path),
        confirmer=confirmer or default_confirmer,
        host=host,
        history=ConversationHistory(hist) if hist else None,
    )


def _load_plugins_safe(reg, memory, prov, host) -> None:
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
            host_available=host is not None,
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
    def emit(event: Event) -> None:
        write_msg({"type": "event", "event": event.model_dump(mode="json")})

    rid = msg.get("id")
    try:
        method = str(msg.get("method", ""))
        params = msg.get("params") or {}
        api = get_api(method)
        if api is None:  # 白名单外：拒绝执行
            emit(Event(kind="error", text=f"面板方法未在白名单：{method}"))
            write_msg({"type": "run_done", "id": rid})
            return
        if not api.direct:
            await run_text(_render_intent(api, params), rid)
            return

        action = agent.invoker.propose(ToolCall(id=f"pa_{rid}", skill_id=api.handler, params=params))
        if api.risk is not None:
            action.risk = max(action.risk, api.risk)  # api.toml 只许收紧，不许放宽
        decision = agent.invoker.decide(action)
        if decision == Decision.DENY:
            emit(Event(kind="error", text=f"策略禁止执行 {api.handler}（风险过高）"))
            write_msg({"type": "run_done", "id": rid})
            return
        if decision == Decision.CONFIRM:
            emit(Event(kind="confirmation_needed", action=action, confirmation_id=action.id))
            if not await agent.invoker.confirm(action):  # 等壳 confirm 消息（复用单槽确认流）
                emit(Event(kind="error", text=f"用户拒绝执行 {api.handler}"))
                write_msg({"type": "run_done", "id": rid})
                return
        result = await _offload(agent.invoker.execute, action, params)  # 与 arun 一致挪线程池
        emit(Event(kind="action_result", action=action, result=result))
        if result.success and api.refresh is not None:
            # 声明式刷新：删除类操作后跟一次查询，面板拿新数据而不是操作回执
            await _emit_refresh_panel(agent, emit, api.refresh)
        else:
            payload = panel_payload(result)
            if payload is not None:
                emit(Event(kind="panel", payload=payload))
        write_msg({"type": "run_done", "id": rid})
    except Exception as e:  # 兜底：任何意外都要给壳一个交代，别让面板卡死
        emit(Event(kind="error", text=f"面板操作失败：{e}"))
        write_msg({"type": "run_done", "id": rid})


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
    queue: asyncio.Queue = asyncio.Queue()
    # 确认单槽 + 早到缓存：confirm 可能先于 confirmer 注册 future 到达
    # （读线程瞬时投递 run+confirm，主循环先处理 confirm），直接丢会死锁。
    pending_confirm: dict = {"future": None, "early": None}
    run_state: dict = {"task": None, "cancel": None}

    async def confirmer(action) -> bool:
        # 早到的 confirm 直接兑现
        if pending_confirm["early"] is not None:
            approved = pending_confirm["early"]
            pending_confirm["early"] = None
            return bool(approved)
        # 单槽 future：收到任意 confirm 消息即兑现（v1 run 串行，确认也串行）
        fut = ai_loop.create_future()
        pending_confirm["future"] = fut
        try:
            return await fut
        finally:
            if pending_confirm["future"] is fut:
                pending_confirm["future"] = None

    agent = build_loop(
        read_msg, use_real, db_path, provider, skills_factory, confirmer=confirmer
    )
    # 启动握手：壳靠它确认大脑上线（守护重启后也靠它判断已恢复）
    write_msg({"type": "hello", "version": 1, "permissions": _permissions_status()})

    def _reader():
        while True:
            msg = read_msg()
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

    async def _pump_tts(tts_q: asyncio.Queue, cancel: asyncio.Event):
        if voice is None:
            return
        try:
            await voice.speak_stream(_tts_chunks(tts_q), cancel)
        except asyncio.CancelledError:
            return  # 打断命中合成/播放的正常取消，不是播报失败
        except Exception as e:
            write_msg({"type": "event", "event": {"kind": "error", "text": f"语音播报失败：{e}"}})
            return
        if not cancel.is_set():
            write_msg({"type": "event", "event": {"kind": "speaking_done"}})

    async def _stream_agent(text: str, rid, cancel: asyncio.Event):
        tts_q: asyncio.Queue | None = asyncio.Queue() if voice is not None else None
        tts_task = asyncio.create_task(_pump_tts(tts_q, cancel)) if tts_q is not None else None
        started_speaking = False
        try:
            async for event in agent.arun(text, cancel):
                write_msg({"type": "event", "event": event.model_dump(mode="json")})
                if (
                    tts_q is not None
                    and event.kind == "final_reply_chunk"
                    and event.text
                ):
                    if not started_speaking:
                        started_speaking = True
                        write_msg({"type": "event", "event": {"kind": "speaking"}})
                    await tts_q.put(event.text)
        except Exception as e:
            # arun 抛异常（如 provider 400）→ 发 error + 停 TTS，别让前端卡死
            cancel.set()
            write_msg({"type": "event", "event": {"kind": "error", "text": f"大脑出错：{e}"}})
        finally:
            if tts_q is not None:
                await tts_q.put(None)  # 收尾哨兵，唤醒可能在 get() 上等待的 _pump_tts
            if tts_task is not None:
                await tts_task
            write_msg({"type": "run_done", "id": rid})

    async def _drive_run(text: str, rid, cancel: asyncio.Event):
        await _stream_agent(text, rid, cancel)

    async def _drive_voice_start(rid, cancel: asyncio.Event):
        write_msg({"type": "event", "event": {"kind": "listening"}})
        try:
            text = await ai_loop.run_in_executor(None, voice.listen)
        except Exception as e:
            write_msg({"type": "event", "event": {"kind": "error", "text": f"语音识别失败：{e}"}})
            write_msg({"type": "run_done", "id": rid})
            return
        write_msg({"type": "event", "event": {"kind": "listening_done", "text": text}})
        if text:
            await _stream_agent(text, rid, cancel)
        else:
            write_msg({"type": "run_done", "id": rid})

    def _preempt_current():
        if run_state["cancel"] is not None:
            run_state["cancel"].set()

    async def _join_current():
        task = run_state["task"]
        if task is not None and not task.done():
            await task

    while True:
        msg = await queue.get()
        if msg is None:
            # stdin 关闭：不再接新活，让在跑的 run 自然结束再退出
            await _join_current()
            return
        rtype = msg.get("type")
        if rtype in ("run", "voice_start"):
            _preempt_current()
            await _join_current()
            cancel = asyncio.Event()
            run_state["cancel"] = cancel
            if rtype == "run":
                run_state["task"] = asyncio.ensure_future(
                    _drive_run(msg.get("text", ""), msg.get("id"), cancel)
                )
            elif voice is not None:
                run_state["task"] = asyncio.ensure_future(_drive_voice_start(msg.get("id"), cancel))
        elif rtype == "panel_action":
            # 面板直调/意图方法：与 run 同槽位（抢占并等待在跑的任务结束）
            _preempt_current()
            await _join_current()
            cancel = asyncio.Event()
            run_state["cancel"] = cancel
            run_state["task"] = asyncio.ensure_future(
                handle_panel_action(
                    msg, agent, write_msg,
                    run_text=lambda text, rid, c=cancel: _stream_agent(text, rid, c),
                )
            )
        elif rtype == "interrupt":
            _preempt_current()
        elif rtype == "confirm":
            fut = pending_confirm["future"]
            if fut is not None and not fut.done():
                fut.set_result(bool(msg.get("approved", False)))
            else:
                # confirmer 还没注册（消息先于 run 任务到达）→ 缓存，由 confirmer 兑现
                pending_confirm["early"] = bool(msg.get("approved", False))
        elif rtype == "ping":
            # 壳侧看门狗心跳：run 进行中主循环也停在 queue.get()，总能即时应答
            write_msg({"type": "pong"})
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
    def _w(msg: dict) -> None:
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


def main() -> int:
    reader, writer = _line_reader(), _line_writer()
    voice = _build_voice_or_none()
    # 数据目录分离：仓库时代的用户数据一次性迁走（sidecar/ → 应用数据目录）
    from . import config as _cfg

    _cfg.migrate_legacy_data(os.path.join(os.path.dirname(__file__), "..", ".."))
    os.makedirs(_cfg.data_dir(), exist_ok=True)  # sqlite/qdrant 不会自建父目录
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
