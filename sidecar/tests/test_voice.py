"""Plan 4a 语音 + 4b 流式打断：serve voice_start 端到端（FakeVoice + FakeProvider）。不碰真麦克风/sherpa/edge-tts。"""
import asyncio

from yibao_brain.llm import FakeProvider, ToolCall
from yibao_brain.server import build_loop, serve, serve_async
from fakes import FakeVoice


class _TwoStep:
    def __init__(self, first, second):
        self._f, self._s, self._n = first, second, 0

    def chat(self, messages, tools=None):
        self._n += 1
        return self._f.chat(messages, tools) if self._n == 1 else self._s.chat(messages, tools)


def _reader(msgs):
    it = iter(msgs + [None])
    return lambda: next(it)


def test_serve_voice_start_speaks_final_reply(tmp_path):
    provider = _TwoStep(
        first=FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="echo", params={"text": "hi"})]),
        second=FakeProvider(text="你好，我是译宝"),
    )
    loop = build_loop(_reader([]), use_real=False, db_path=str(tmp_path / "a.db"), provider=provider)
    voice = FakeVoice("你好")
    out = []
    serve(loop, _reader([{"id": 1, "type": "voice_start"}]), lambda m: out.append(m), voice=voice)

    kinds = [m["event"]["kind"] for m in out if m["type"] == "event"]
    assert "listening" in kinds
    assert "listening_done" in kinds
    # listening_done 带识别出的文字
    assert any(
        m["type"] == "event" and m["event"]["kind"] == "listening_done" and m["event"]["text"] == "你好"
        for m in out
    )
    assert "final_reply" in kinds
    assert "speaking" in kinds  # final_reply 后触发
    assert voice.speak_calls == ["你好，我是译宝"]


def test_serve_voice_start_empty_text_skips_run(tmp_path):
    # STT 返空 → 不进 run，直接 run_done
    provider = _TwoStep(FakeProvider(text="x"), FakeProvider(text="y"))
    loop = build_loop(_reader([]), use_real=False, db_path=str(tmp_path / "a.db"), provider=provider)
    voice = FakeVoice("")
    out = []
    serve(loop, _reader([{"id": 1, "type": "voice_start"}]), lambda m: out.append(m), voice=voice)

    kinds = [m["event"]["kind"] for m in out if m["type"] == "event"]
    assert "listening" in kinds
    assert "listening_done" in kinds
    assert "final_reply" not in kinds
    assert voice.speak_calls == []


# ---------- Plan 4b：speak_stream 流式 + 打断 ----------


async def _async_gen(items):
    for it in items:
        yield it


def test_speak_stream_plays_all_chunks():
    voice = FakeVoice()
    cancel = asyncio.Event()

    async def _go():
        await voice.speak_stream(_async_gen(["你好", "，我是", "译宝"]), cancel)

    asyncio.run(_go())
    assert voice.stream_chunks == ["你好", "，我是", "译宝"]
    assert not voice.stream_interrupted


def test_speak_stream_cancel_before_first_chunk():
    voice = FakeVoice()
    cancel = asyncio.Event()
    cancel.set()

    async def _go():
        await voice.speak_stream(_async_gen(["你好", "译宝"]), cancel)

    asyncio.run(_go())
    assert voice.stream_interrupted
    assert voice.stream_chunks == []


def test_serve_async_voice_streams_and_speaks(tmp_path):
    # serve_async 端到端：voice_start → listen → 流式 run → speak_stream 收全 → speaking/speaking_done
    provider = FakeProvider(chunks=["你好", "，我是", "译宝"])
    voice = FakeVoice("你好")
    out = []

    async def _go():
        await serve_async(
            _reader([{"id": 1, "type": "voice_start"}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=provider,
            voice=voice,
        )

    asyncio.run(_go())
    kinds = [m["event"]["kind"] for m in out if m["type"] == "event"]
    assert "listening" in kinds
    assert "listening_done" in kinds
    assert "final_reply_chunk" in kinds
    assert "speaking" in kinds
    assert "speaking_done" in kinds  # 未被打断 → 正常收尾
    assert voice.stream_chunks == ["你好", "，我是", "译宝"]
    assert out[-1] == {"type": "run_done", "id": 1}


def test_serve_async_voice_interrupt_stops_speaking(tmp_path):
    # 读线程延迟投递 interrupt（播放中途），验证 cancel 一路传到 speak_stream：
    # LLM 快速吐完 3 chunk → 进入播放（每句 50ms，约 0~0.15s）；interrupt 在 ~0.08s 落下
    # → speak_stream 中途见 cancel → stream_interrupted，且无 speaking_done。
    import time

    def _delayed_reader(specs):
        it = iter(specs)

        def _r():
            try:
                msg, delay = next(it)
            except StopIteration:
                return None
            if delay:
                time.sleep(delay)
            return msg

        return _r

    provider = FakeProvider(chunks=["你好", "，我是", "译宝"])
    voice = FakeVoice("你好", stream_delay=0.05)
    out = []

    async def _go():
        await serve_async(
            _delayed_reader(
                [
                    ({"id": 1, "type": "voice_start"}, 0.0),
                    ({"type": "interrupt"}, 0.08),
                ]
            ),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=provider,
            voice=voice,
        )

    asyncio.run(_go())
    kinds = [m["event"]["kind"] for m in out if m["type"] == "event"]
    assert voice.stream_interrupted
    assert "speaking_done" not in kinds  # 被打断，无正常收尾
