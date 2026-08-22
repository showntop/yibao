"""voice 域（R-13 第二步拆分序 3）：TTS 播报泵与连续语音会话驱动。

从 server.serve_async 原样搬来（2026-08-22）；共享状态（voice/write_msg/ai_loop）经
RuntimeCtx 注入。run 流（_stream_agent/_drive_run）暂留 serve_async——本域经
ctx.stream_agent 回调它，serve_async 经局部名绑回 tts_lock/_pump_tts/_drive_voice_start，
_stream_agent 与 handler 层原引用零改动。改行为请另开 commit。
"""
from __future__ import annotations

import asyncio
import time

from ..log import log
from ..transport import (
    _VOICE_SESSION_BYE,
    _VOICE_SESSION_HINT,
    _VOICE_SESSION_MAX_EMPTY,
    _run_done_msg,
    is_exit_phrase as _is_exit_phrase,
)


class VoiceDomain:
    """voice 域函数束：TTS 全局播报锁 + 播报泵 + 连续语音会话。"""

    def __init__(self, ctx) -> None:
        self.ctx = ctx
        # TTS 全局播报锁（spec §D）：物理声道只有一条，播报保持全局串行、不 per-会话化。
        # 持锁者独占播放器（打断三连取消只作用本槽，A 的打断掐不掉 B 正在播的音）；
        # 抢不到锁的 run 静默不播（文字流式照出）+ notice，不排队。
        self.tts_lock = asyncio.Lock()

    async def _tts_chunks(self, tts_q: asyncio.Queue):
        while True:
            item = await tts_q.get()
            if item is None:
                return
            yield item

    async def pump_tts(self, tts_q: asyncio.Queue, cancel: asyncio.Event, surface: str = "pet", conversation_id: str = ""):
        ctx = self.ctx
        voice = ctx.voice
        if voice is None:
            return
        try:
            await voice.speak_stream(self._tts_chunks(tts_q), cancel)
        except asyncio.CancelledError:
            return  # 打断命中合成/播放的正常取消，不是播报失败
        except Exception as e:
            ctx.write_msg({"type": "event", "surface": surface, "conversation_id": conversation_id, "event": {"kind": "error", "text": f"语音播报失败：{e}"}})
            return
        if not cancel.is_set():
            ctx.write_msg({"type": "event", "surface": surface, "conversation_id": conversation_id, "event": {"kind": "speaking_done"}})

    async def drive_voice_start(self, rid, cancel: asyncio.Event, surface: str = "pet", conversation_id: str = "", continuous: bool = False):
        # 连续对话（长按团子进入）：答完接着听。退出：退出语 / 连续两次没听清 / 打断。
        ctx = self.ctx
        voice = ctx.voice
        write_msg = ctx.write_msg
        tts_lock = self.tts_lock

        def _vev(event: dict) -> None:
            write_msg({"type": "event", "surface": surface, "conversation_id": conversation_id, "event": event})

        def _done() -> None:
            write_msg(_run_done_msg(rid, conversation_id))

        if continuous:
            _vev({"kind": "notice", "text": _VOICE_SESSION_HINT})
        empties = 0
        while True:
            _vev({"kind": "listening"})

            async def _watch_cancel():
                await cancel.wait()
                voice.stop_listen()  # 打断（interrupt）→ 录音循环下一拍退出

            watcher = asyncio.ensure_future(_watch_cancel())
            t0 = time.monotonic()
            try:
                text = await ctx.ai_loop.run_in_executor(None, voice.listen)
            except Exception as e:
                _vev({"kind": "error", "text": f"语音识别失败：{e}"})
                _done()
                return
            finally:
                watcher.cancel()
            log(f"聆听结束（{time.monotonic() - t0:.1f}s）：{text[:30]!r}")
            if cancel.is_set():  # 聆听被打断：不走 listening_done（避免误进 think 态）
                _vev({"kind": "interrupted"})
                _done()
                return
            _vev({"kind": "listening_done", "text": text})
            if not text:
                if continuous:
                    empties += 1
                    if empties < _VOICE_SESSION_MAX_EMPTY:
                        continue  # 没听清：会话中不打岔，直接再听一轮
                    _vev({"kind": "notice", "text": "一会儿没说话，先退下啦，叫我随时来～"})
                    _done()
                    return
                _done()
                return
            empties = 0
            if continuous and _is_exit_phrase(text):
                # 退出语：固定告别（不过 LLM，确定性收尾）
                _vev({"kind": "final_reply", "text": _VOICE_SESSION_BYE})
                if tts_lock.locked():
                    # 另一会话正在播报（spec §D 单声道）：告别静默不抢声道、不排队
                    _done()
                    return
                _vev({"kind": "speaking"})

                async def _bye():
                    yield _VOICE_SESSION_BYE

                await tts_lock.acquire()
                try:
                    await voice.speak_stream(_bye(), cancel)
                except Exception as e:
                    log(f"会话告别播报失败：{e}")
                finally:
                    tts_lock.release()
                if cancel.is_set():
                    _vev({"kind": "interrupted"})
                else:
                    _vev({"kind": "speaking_done"})
                _done()
                return
            # 连续会话的 run_done 由本函数在会话结束时发（每轮结束就发会让前端以为请求完结）
            await ctx.stream_agent(text, rid, cancel, surface, conversation_id, emit_done=not continuous)
            if not continuous:
                return
            if cancel.is_set():
                _done()
                return
            # 答完接着听下一轮
