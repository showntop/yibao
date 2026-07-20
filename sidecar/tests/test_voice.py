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


def test_serve_async_voice_interrupt_cancels_listening(tmp_path):
    # 聆听中 interrupt → stop_listen 打断录音，回 interrupted（而非 listening_done 误进 think 态）
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

    provider = FakeProvider(chunks=["不该出现"])
    voice = FakeVoice(listen_block=True)  # listen 挂起，直到 stop_listen
    out = []

    async def _go():
        await serve_async(
            _delayed_reader(
                [
                    ({"id": 1, "type": "voice_start"}, 0.0),
                    ({"type": "interrupt"}, 0.1),
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
    assert voice.listen_stopped
    assert "interrupted" in kinds
    assert "listening_done" not in kinds
    assert "final_reply_chunk" not in kinds  # 没进 agent 生成
    assert out[-1] == {"type": "run_done", "id": 1}


# ---------- Plan 5 修复：TTS 合成/播放管道化 + VAD 阈值可配 ----------


def test_speaker_stream_pipelines_synth_and_play(monkeypatch):
    """边播边预取：第二句的合成应在第一句播放结束前就开始（句间不再有完整合成延迟）。"""
    import time

    from yibao_brain.voice import EdgeTtsSpeaker

    speaker = EdgeTtsSpeaker()
    timeline: dict[str, float] = {}

    async def fake_synth(text):
        timeline["synth_start_" + text] = time.monotonic()
        await asyncio.sleep(0.05)  # 模拟网络合成延迟
        return f"pcm:{text}"

    played: list[str] = []

    async def fake_play(pcm, cancel):
        played.append(pcm)
        await asyncio.sleep(0.1)  # 模拟播放耗时
        timeline["play_end_" + pcm] = time.monotonic()

    monkeypatch.setattr(speaker, "_synth_pcm", fake_synth)
    monkeypatch.setattr(speaker, "_play_pcm", fake_play)

    cancel = asyncio.Event()
    asyncio.run(speaker.speak_stream(_async_gen(["第一句。", "第二句。"]), cancel))

    assert played == ["pcm:第一句。", "pcm:第二句。"]
    # 管道化标志：第二句合成开始早于第一句播放结束（重叠）
    assert timeline["synth_start_第二句。"] < timeline["play_end_pcm:第一句。"]


def test_speaker_stream_plays_tail(monkeypatch):
    """无终止标点的残句也要播报。"""
    from yibao_brain.voice import EdgeTtsSpeaker

    speaker = EdgeTtsSpeaker()
    played: list[str] = []

    async def fake_synth(text):
        return f"pcm:{text}"

    async def fake_play(pcm, cancel):
        played.append(pcm)

    monkeypatch.setattr(speaker, "_synth_pcm", fake_synth)
    monkeypatch.setattr(speaker, "_play_pcm", fake_play)

    cancel = asyncio.Event()
    asyncio.run(speaker.speak_stream(_async_gen(["完整句。", "残句没标点"]), cancel))
    assert played == ["pcm:完整句。", "pcm:残句没标点"]


def test_speaker_stream_cancel_stops_early(monkeypatch):
    """打断后不再播后续句子。"""
    from yibao_brain.voice import EdgeTtsSpeaker

    speaker = EdgeTtsSpeaker()
    played: list[str] = []

    async def fake_synth(text):
        return f"pcm:{text}"

    async def fake_play(pcm, cancel):
        played.append(pcm)
        cancel.set()  # 第一句播完即打断

    monkeypatch.setattr(speaker, "_synth_pcm", fake_synth)
    monkeypatch.setattr(speaker, "_play_pcm", fake_play)

    cancel = asyncio.Event()
    asyncio.run(speaker.speak_stream(_async_gen(["一。", "二。", "三。"]), cancel))
    assert played == ["pcm:一。"]


def test_vad_config_defaults_and_env(monkeypatch):
    from yibao_brain import config

    monkeypatch.delenv("YIBAO_VAD_MIN_SILENCE", raising=False)
    monkeypatch.delenv("YIBAO_VAD_MAX_SECONDS", raising=False)
    assert config.vad_min_silence() == 0.9
    assert config.vad_max_seconds() == 30
    monkeypatch.setenv("YIBAO_VAD_MIN_SILENCE", "1.2")
    monkeypatch.setenv("YIBAO_VAD_MAX_SECONDS", "20")
    assert config.vad_min_silence() == 1.2
    assert config.vad_max_seconds() == 20


def test_play_pcm_no_name_error(monkeypatch):
    """回归：_play_pcm 内 asyncio.sleep 曾 NameError（import 漏在拆分中丢失）。
    用假 sounddevice 打穿真实 _play_pcm。"""
    import sys
    import types

    from yibao_brain.voice import EdgeTtsSpeaker

    stream = types.SimpleNamespace(active=True)
    polls = {"n": 0}

    def _get_stream():
        polls["n"] += 1
        if polls["n"] > 1:
            stream.active = False  # 第二次轮询视为播完
        return stream

    fake_sd = types.SimpleNamespace(
        play=lambda pcm, samplerate: None,
        get_stream=_get_stream,
        stop=lambda: None,
    )
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

    speaker = EdgeTtsSpeaker()
    cancel = asyncio.Event()
    asyncio.run(speaker._play_pcm([0.0] * 10, cancel))  # NameError 即失败


def test_synth_pcm_skips_punctuation_only_sentence(monkeypatch):
    """纯标点句（如「？」）edge-tts 会 NoAudioReceived：直接判为不可播，不发请求。"""
    from yibao_brain.voice import EdgeTtsSpeaker

    speaker = EdgeTtsSpeaker()
    calls = []

    async def fake_fetch(text):
        calls.append(text)
        return b"mp3"

    monkeypatch.setattr(speaker, "_fetch_mp3", fake_fetch)
    assert asyncio.run(speaker._synth_pcm("？")) is None
    assert asyncio.run(speaker._synth_pcm("…")) is None
    assert calls == []  # 纯标点根本没发请求


def test_speak_stream_no_audio_sentence_skipped(monkeypatch):
    """单句 NoAudioReceived 跳过该句、不杀整段播报（其余句照播）。"""
    from edge_tts.exceptions import NoAudioReceived

    from yibao_brain.voice import EdgeTtsSpeaker

    speaker = EdgeTtsSpeaker()
    played: list[str] = []

    async def fake_fetch(text):
        if text == "嗯？":
            raise NoAudioReceived("No audio was received.")
        return f"pcm:{text}".encode()

    async def fake_play(pcm, cancel):
        played.append(pcm)

    monkeypatch.setattr(speaker, "_fetch_mp3", fake_fetch)
    monkeypatch.setattr(speaker, "_play_pcm", fake_play)
    monkeypatch.setattr("yibao_brain.voice._decode_mp3", lambda b: b)  # 跳过真解码

    cancel = asyncio.Event()
    asyncio.run(speaker.speak_stream(_async_gen(["嗯？", "记好了。"]), cancel))
    assert played == ["pcm:记好了。".encode()]  # 炸的那句被跳过，播报没死


def test_speak_stream_cancel_during_synth_does_not_raise(monkeypatch):
    """打断命中合成中：edge-tts websocket 读被 cancel → CancelledError 是正常取消，
    不是合成错误，不许进 synth_error 重新抛出（曾一路炸穿 serve_async → 大脑 exit 1）。"""
    from yibao_brain.voice import EdgeTtsSpeaker

    speaker = EdgeTtsSpeaker()
    cancel = asyncio.Event()

    async def fake_synth(text):
        cancel.set()
        raise asyncio.CancelledError  # 模拟 producer.cancel() 击中合成中

    monkeypatch.setattr(speaker, "_synth_pcm", fake_synth)
    asyncio.run(speaker.speak_stream(_async_gen(["你好。"]), cancel))  # 不抛即过


def test_speech_text_strips_markdown_and_emoji():
    """TTS 播报前清洗 Markdown 与 emoji：星号/标题/链接/列表/代码/表情都不念出来。"""
    from yibao_brain.voice import _speech_text

    assert _speech_text("**记好了**，这是重点") == "记好了，这是重点"
    assert _speech_text("好嘞 ✅ 已记下！") == "好嘞 已记下！"
    assert _speech_text("看[这个链接](https://x.com)吧") == "看这个链接吧"
    assert _speech_text("# 标题\n- 第一条\n1. 第二条") == "标题 第一条 第二条"
    assert _speech_text("用 `notes.keep` 就行") == "用 notes.keep 就行"
    assert _speech_text("1️⃣ 第一步") == "第一步"
    # 清洗后无可播字符 → 空串（调用方跳过合成）
    assert _speech_text("**？**") == "？"
    assert _speech_text("✨🎉") == ""


def test_synth_pcm_speaks_cleaned_text(monkeypatch):
    """端到端到 _fetch_mp3：合成用的是清洗后的文本。"""
    from yibao_brain.voice import EdgeTtsSpeaker

    speaker = EdgeTtsSpeaker()
    got = []

    async def fake_fetch(text):
        got.append(text)
        return b"mp3"

    monkeypatch.setattr(speaker, "_fetch_mp3", fake_fetch)
    monkeypatch.setattr("yibao_brain.voice._decode_mp3", lambda b: [0.0])
    asyncio.run(speaker._synth_pcm("**记好了** ✅"))
    assert got == ["记好了"]


def test_lazy_recognizer_loads_on_first_transcribe(monkeypatch):
    """懒加载：构造不加载模型（启动提速），首次 transcribe 才加载且只加载一次。"""
    from yibao_brain import voice as voice_mod

    constructions = []

    class _FakeRec:
        def __init__(self, model_dir):
            constructions.append(model_dir)

        def transcribe(self, pcm):
            return "识别文字"

    monkeypatch.setattr(voice_mod, "SherpaRecognizer", _FakeRec)
    rec = voice_mod.LazySherpaRecognizer("/models/x")
    assert constructions == []  # 构造即返回，启动不背模型开销
    assert rec.transcribe(None) == "识别文字"
    assert rec.transcribe(None) == "识别文字"
    assert constructions == ["/models/x"]  # 只加载一次
