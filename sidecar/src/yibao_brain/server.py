"""stdio 行分隔 JSON 服务：把 AgentLoop 接到桌面壳（Phase B 的 Tauri 侧）。"""
from __future__ import annotations

import asyncio
import json
import sys
import threading
from collections.abc import Callable

from .audit import AuditLog
from .config import a11y_enabled, glm_api_key, screenshot_dir, stt_model_dir, tts_voice, vad_model_path, voice_enabled
from .ipc import RiskLevel
from .llm import FakeProvider, GLMProvider
from .loop import AgentLoop
from .memory import FakeMemory, Mem0Memory
from .safety import Gate, GatePolicy, RiskClassifier
from .skills import EchoSkill, SkillRegistry
from .skills_real import ComputerUseSkill, register_real_skills

ReadMsg = Callable[[], dict | None]
WriteMsg = Callable[[dict], None]


def build_loop(
    read_msg: ReadMsg,
    use_real: bool,
    db_path: str,
    provider=None,
    skills_factory=None,
    confirmer=None,
) -> AgentLoop:
    real_a11y = use_real and a11y_enabled() and sys.platform == "darwin"
    reg = skills_factory() if skills_factory else SkillRegistry()
    if not skills_factory:
        reg.register(EchoSkill())
        if real_a11y:
            register_real_skills(reg)
            if glm_api_key():
                try:
                    from .llm import ComputerUseClient

                    reg.register(ComputerUseSkill(ComputerUseClient()))
                except Exception as e:
                    print(f"[yibao] computer-use 兜底未启用：{e}", file=sys.stderr)

    if provider is not None:
        prov = provider
    else:
        prov = GLMProvider() if (use_real and glm_api_key()) else FakeProvider(text="(未配置 GLM key，使用 fake 回复)")

    try:
        memory = Mem0Memory() if use_real else FakeMemory()
    except Exception:
        memory = FakeMemory()

    host = None
    if real_a11y:
        try:
            from .mac.host_mac import MacHost

            host = MacHost(screenshot_dir=screenshot_dir())
        except Exception as e:  # pyobjc 未装 / 非 mac → 回退无基座（技能会优雅报错）
            print(f"[yibao] MacHost 不可用，回退无基座：{e}", file=sys.stderr)

    def default_confirmer(action) -> bool:
        # 由 serve 在 confirmation_needed 事件之后触发；阻塞读壳的回答
        ans = read_msg() or {}
        return bool(ans.get("approved", False))

    return AgentLoop(
        provider=prov,
        skills=reg,
        classifier=RiskClassifier(),
        gate=Gate(GatePolicy(auto_below_or_equal=RiskLevel.L1_LOW)),  # L2+ 走确认
        memory=memory,
        log=AuditLog(db_path),
        confirmer=confirmer or default_confirmer,
        host=host,
    )


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
    pending_confirm: dict = {"future": None}
    run_state: dict = {"task": None, "cancel": None}

    async def confirmer(action) -> bool:
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

    def _reader():
        while True:
            msg = read_msg()
            ai_loop.call_soon_threadsafe(queue.put_nowait, msg)
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
        except Exception as e:
            write_msg({"type": "event", "event": {"kind": "error", "text": f"语音播报失败：{e}"}})
            return
        if not cancel.is_set():
            write_msg({"type": "event", "event": {"kind": "speaking_done"}})

    async def _stream_agent(text: str, rid, cancel: asyncio.Event):
        tts_q: asyncio.Queue | None = asyncio.Queue() if voice is not None else None
        tts_task = asyncio.create_task(_pump_tts(tts_q, cancel)) if tts_q is not None else None
        started_speaking = False
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
        elif rtype == "interrupt":
            _preempt_current()
        elif rtype == "confirm":
            fut = pending_confirm["future"]
            if fut is not None and not fut.done():
                fut.set_result(bool(msg.get("approved", False)))


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

        return build_voice(stt_model_dir(), vad_model_path(), tts_voice())
    except Exception as e:
        print(f"[yibao] 语音不可用，已禁用：{e}", file=sys.stderr)
        return None


def main() -> int:
    reader, writer = _line_reader(), _line_writer()
    voice = _build_voice_or_none()
    asyncio.run(
        serve_async(
            reader,
            writer,
            use_real=True,
            db_path="audit.db",
            voice=voice,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
