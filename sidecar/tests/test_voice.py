"""Plan 4a 语音 + 4b 流式打断：serve voice_start 端到端（FakeVoice + FakeProvider）。不碰真麦克风/sherpa/edge-tts。"""
import asyncio

from yibao_brain.llm import FakeProvider, ToolCall
from yibao_brain.server import build_loop, serve, serve_async
from yibao_brain.voice import StreamingPcmSpeaker
from fakes import FakeVoice


class _TwoStep:
    def __init__(self, first, second):
        self._f, self._s, self._n = first, second, 0

    def chat(self, messages, tools=None):
        self._n += 1
        return self._f.chat(messages, tools) if self._n == 1 else self._s.chat(messages, tools)

    async def astream(self, messages, tools=None):
        self._n += 1
        src = self._f if self._n == 1 else self._s
        async for d in src.astream(messages, tools):
            yield d


def _reader(msgs):
    it = iter(msgs + [None])
    return lambda: next(it)


def test_serve_voice_start_speaks_final_reply(tmp_path):
    provider = _TwoStep(
        first=FakeProvider(tool_calls=[ToolCall(id="t1", tool_id="echo", params={"text": "hi"})]),
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
    assert "interrupted" in kinds  # TTS 阶段打断也要回 idle，否则停止按钮停不住


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

    from yibao_brain.voice import StreamingPcmSpeaker

    timeline: dict[str, float] = {}

    class _Fake(StreamingPcmSpeaker):
        async def _synth_pcm(self, text):
            timeline["synth_start_" + text] = time.monotonic()
            await asyncio.sleep(0.05)  # 模拟网络合成延迟
            return f"pcm:{text}"

    speaker = _Fake()
    played: list[str] = []

    async def fake_play(pcm, cancel, **_kw):
        played.append(pcm)
        await asyncio.sleep(0.1)  # 模拟播放耗时
        timeline["play_end_" + pcm] = time.monotonic()

    monkeypatch.setattr(speaker, "_play_pcm", fake_play)

    cancel = asyncio.Event()
    asyncio.run(speaker.speak_stream(_async_gen(["第一句。", "第二句。"]), cancel))

    assert played == ["pcm:第一句。", "pcm:第二句。"]
    # 管道化标志：第二句合成开始早于第一句播放结束（重叠）
    assert timeline["synth_start_第二句。"] < timeline["play_end_pcm:第一句。"]


def test_speaker_stream_plays_tail(monkeypatch):
    """无终止标点的残句也要播报。"""
    from yibao_brain.voice import StreamingPcmSpeaker

    class _Fake(StreamingPcmSpeaker):
        async def _synth_pcm(self, text):
            return f"pcm:{text}"

    speaker = _Fake()
    played: list[str] = []

    async def fake_play(pcm, cancel, **_kw):
        played.append(pcm)

    monkeypatch.setattr(speaker, "_play_pcm", fake_play)

    cancel = asyncio.Event()
    asyncio.run(speaker.speak_stream(_async_gen(["完整句。", "残句没标点"]), cancel))
    assert played == ["pcm:完整句。", "pcm:残句没标点"]


def test_speaker_stream_cancel_stops_early(monkeypatch):
    """打断后不再播后续句子。"""
    from yibao_brain.voice import StreamingPcmSpeaker

    class _Fake(StreamingPcmSpeaker):
        async def _synth_pcm(self, text):
            return f"pcm:{text}"

    speaker = _Fake()
    played: list[str] = []

    async def fake_play(pcm, cancel, **_kw):
        played.append(pcm)
        cancel.set()  # 第一句播完即打断

    monkeypatch.setattr(speaker, "_play_pcm", fake_play)

    cancel = asyncio.Event()
    asyncio.run(speaker.speak_stream(_async_gen(["一。", "二。", "三。"]), cancel))
    assert played == ["pcm:一。"]


def test_vad_config_defaults_and_env(monkeypatch):
    from yibao_brain import config

    monkeypatch.delenv("YIBAO_VAD_MIN_SILENCE", raising=False)
    monkeypatch.delenv("YIBAO_VAD_MAX_SECONDS", raising=False)
    assert config.vad_min_silence() == 1.2  # 二期默认上调（0.9 抢话）
    assert config.vad_max_seconds() == 30
    monkeypatch.setenv("YIBAO_VAD_MIN_SILENCE", "1.5")
    monkeypatch.setenv("YIBAO_VAD_MAX_SECONDS", "20")
    assert config.vad_min_silence() == 1.5
    assert config.vad_max_seconds() == 20


def test_play_pcm_no_name_error(monkeypatch):
    """回归：_play_pcm 内曾因 import 丢失 NameError。用假 sounddevice 打穿真实 _play_pcm。

    新实现走常驻 OutputStream：fake 在 start() 里逐帧喂 callback（每帧 1 样本），
    播完 10 样本后回调通知 async 侧收尾（不关流，待机静音）。
    """
    import sys
    import types

    import numpy as np

    from yibao_brain.voice import EdgeTtsSpeaker

    class _FakeStream:
        def __init__(self, **kw):
            self._cb = kw["callback"]

        def start(self):
            outdata = np.zeros((1, 1), dtype=np.float32)
            for _ in range(16):  # 帧数 > 样本数，覆盖播完路径（之后待机静音）
                self._cb(outdata, 1, None, None)

        def close(self):
            pass

    fake_sd = types.SimpleNamespace(OutputStream=_FakeStream)
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

    speaker = EdgeTtsSpeaker()
    cancel = asyncio.Event()
    asyncio.run(speaker._play_pcm([0.0] * 10, cancel))  # NameError 即失败


def test_synth_pcm_skips_punctuation_only_sentence(monkeypatch):
    """纯标点句（如「？」）edge-tts 会 NoAudioReceived：直接判为不可播，不发请求。"""
    from yibao_brain.voice import EdgeTtsSpeaker

    speaker = EdgeTtsSpeaker()
    calls = []

    async def fake_stream(text):
        calls.append(text)
        yield b"pcm"

    monkeypatch.setattr(speaker, "_stream_sentence_pcm", fake_stream)
    assert asyncio.run(speaker._synth_pcm("？")) is None
    assert asyncio.run(speaker._synth_pcm("…")) is None
    assert calls == []  # 纯标点根本没发请求


def test_speak_stream_no_audio_sentence_skipped(monkeypatch):
    """单句 NoAudioReceived 跳过该句、不杀整段播报（其余句照播），且不触发重连重试。"""
    from edge_tts.exceptions import NoAudioReceived

    from yibao_brain.voice import EdgeTtsSpeaker

    speaker = EdgeTtsSpeaker()
    played: list[str] = []
    calls: list[str] = []

    async def fake_stream(text):
        calls.append(text)
        if text == "嗯？":
            raise NoAudioReceived("No audio was received.")
        yield f"pcm:{text}"

    async def fake_play(pcm, cancel, **_kw):
        played.append(pcm)

    monkeypatch.setattr(speaker, "_stream_sentence_pcm", fake_stream)
    monkeypatch.setattr(speaker, "_play_pcm", fake_play)

    cancel = asyncio.Event()
    asyncio.run(speaker.speak_stream(_async_gen(["嗯？", "记好了。"]), cancel))
    assert played == ["pcm:记好了。"]  # 炸的那句被跳过，播报没死
    assert calls == ["嗯？", "记好了。"]  # NoAudioReceived 不重试


def test_speak_stream_cancel_during_synth_does_not_raise(monkeypatch):
    """打断命中合成中：edge-tts websocket 读被 cancel → CancelledError 是正常取消，
    不是合成错误，不许进 synth_error 重新抛出（曾一路炸穿 serve_async → 大脑 exit 1）。"""
    from yibao_brain.voice import EdgeTtsSpeaker

    speaker = EdgeTtsSpeaker()
    cancel = asyncio.Event()

    async def fake_stream(text):
        cancel.set()
        raise asyncio.CancelledError  # 模拟 producer.cancel() 击中合成中
        yield  # pragma: no cover - 让它是 async generator

    monkeypatch.setattr(speaker, "_synth_pcm_stream", fake_stream)
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
    """端到端到合成入口：合成用的是清洗后的文本，且整句路径收齐流式片段拼接。"""
    import numpy as np

    from yibao_brain.voice import EdgeTtsSpeaker

    speaker = EdgeTtsSpeaker()
    got = []

    async def fake_stream(text):
        got.append(text)
        yield np.zeros(1200, dtype=np.float32)
        yield np.zeros(1200, dtype=np.float32)

    monkeypatch.setattr(speaker, "_stream_sentence_pcm", fake_stream)
    pcm = asyncio.run(speaker._synth_pcm("**记好了** ✅"))
    assert got == ["记好了"]
    assert pcm is not None and len(pcm) == 2400  # 两段拼成一句


def test_serve_async_voice_start_without_voice_emits_error(tmp_path):
    """语音栈不可用（voice=None）时 voice_start 不许静默吞掉——否则前端永远卡「聆听中」。"""
    provider = FakeProvider(text="x")
    out = []

    async def _go():
        await serve_async(
            _reader([{"id": 1, "type": "voice_start"}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=provider,
            voice=None,
        )

    asyncio.run(_go())
    kinds = [m["event"]["kind"] for m in out if m["type"] == "event"]
    assert "error" in kinds
    assert "listening" not in kinds
    assert out[-1] == {"type": "run_done", "id": 1}


# ---------- 录音卡死回归：SounddeviceRecorder 回调+队列采集 ----------


class _SilentStream:
    """开流但永不投递音频帧（模拟设备被占用/掉线/权限缺失）。"""

    def __init__(self, **_kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def _fake_sherpa():
    """最小 sherpa_onnx 替身：VAD 永不检测到语音、永不出段（脱离真实模型文件）。"""
    import types

    cfg = types.SimpleNamespace(silero_vad=types.SimpleNamespace(window_size=512), sample_rate=0)

    class _Vad:
        def __init__(self, _cfg, buffer_size_in_seconds):
            pass

        def accept_waveform(self, _samples):
            pass

        def is_speech_detected(self):
            return False

        def empty(self):
            return True

    return types.SimpleNamespace(VadModelConfig=lambda: cfg, VoiceActivityDetector=_Vad)


def _patch_audio_stack(monkeypatch):
    import sys
    import types

    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(InputStream=_SilentStream))
    monkeypatch.setitem(sys.modules, "sherpa_onnx", _fake_sherpa())


def test_recorder_stop_unblocks_when_device_silent(monkeypatch):
    """回归：旧实现用阻塞 s.read 采集——设备停发帧时录音循环永不退出，
    stop() 也没机会被看到（「一直聆听中、无法取消」的根因）。
    回调+队列超时改造后：一帧没有也要能随时停下。"""
    import threading
    import time

    from yibao_brain.voice import SounddeviceRecorder

    _patch_audio_stack(monkeypatch)
    rec = SounddeviceRecorder(vad_model="x", max_seconds=60, no_frame_timeout=60)  # 关掉无帧超时，专测 stop
    out = []
    t = threading.Thread(target=lambda: out.append(rec.record_until_silence()))
    t.start()
    time.sleep(0.3)
    t0 = time.monotonic()
    rec.stop()
    t.join(timeout=3)
    assert not t.is_alive(), "stop() 后录音仍未退出（阻塞读回归）"
    assert time.monotonic() - t0 < 1
    assert len(out) == 1 and not out[0].any()


def test_recorder_no_frame_timeout(monkeypatch):
    """设备一直不投递帧：no_frame_timeout 后自动放弃，不许无限卡「聆听中」。"""
    import time

    from yibao_brain.voice import SounddeviceRecorder

    _patch_audio_stack(monkeypatch)
    rec = SounddeviceRecorder(vad_model="x", max_seconds=60, no_frame_timeout=0.3)
    t0 = time.monotonic()
    pcm = rec.record_until_silence()
    assert time.monotonic() - t0 < 3
    assert not pcm.any()


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


# ---------- 语音二期①：连续对话（voice_start continuous） ----------


def _continuous_run(tmp_path, voice, provider):
    """喂一条 continuous voice_start，收集全部输出消息。"""
    out = []

    async def _go():
        await serve_async(
            _reader([{"id": 1, "type": "voice_start", "continuous": True}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=provider,
            voice=voice,
        )

    asyncio.run(_go())
    return out


def _kinds(out):
    return [m["event"]["kind"] for m in out if m["type"] == "event"]


def test_voice_continuous_round_then_exit_phrase(tmp_path):
    """连续会话：第一轮正常问答（LLM 跑），第二轮退出语 → 固定告别收尾，run_done 只发一次。"""
    provider = FakeProvider(chunks=["你", "好呀"])
    voice = FakeVoice(texts=["你好", "退出"])
    out = _continuous_run(tmp_path, voice, provider)

    kinds = _kinds(out)
    assert kinds.count("listening") == 2  # 两轮聆听
    assert "notice" in kinds  # 会话开场提示
    assert voice.listen_calls == 2
    # 第一轮 LLM 回复 + 第二轮固定告别
    finals = [m["event"].get("text") for m in out if m["type"] == "event" and m["event"]["kind"] == "final_reply"]
    assert finals[-1] == "好的，先聊到这儿，叫我随时来～"
    assert any(m["type"] == "event" and m["event"]["kind"] == "final_reply_chunk" for m in out)
    assert voice.stream_chunks == ["好的，先聊到这儿，叫我随时来～"]  # 告别走了 TTS
    assert [m for m in out if m["type"] == "run_done"] == [{"type": "run_done", "id": 1}]  # 全程一次
    assert out[-1] == {"type": "run_done", "id": 1}


def test_voice_continuous_empty_listens_auto_exit(tmp_path):
    """连续两次没听清 → 自动退（麦克风不空转），LLM 一次都不跑。"""
    provider = FakeProvider(text="不该出现")
    voice = FakeVoice(texts=["", ""])
    out = _continuous_run(tmp_path, voice, provider)

    kinds = _kinds(out)
    assert kinds.count("listening") == 2
    assert "final_reply" not in kinds
    notices = [m["event"].get("text") for m in out if m["type"] == "event" and m["event"]["kind"] == "notice"]
    assert any("先退下啦" in (t or "") for t in notices)
    assert [m for m in out if m["type"] == "run_done"] == [{"type": "run_done", "id": 1}]


def test_voice_continuous_run_done_only_at_session_end(tmp_path):
    """问答一轮后两次没听清退出：每轮结束不单独发 run_done（前端会以为请求完结）。"""
    provider = FakeProvider(chunks=["答", "案"])
    voice = FakeVoice(texts=["问题", "", ""])
    out = _continuous_run(tmp_path, voice, provider)

    assert _kinds(out).count("listening") == 3  # 问答一轮 + 空两轮
    run_dones = [i for i, m in enumerate(out) if m["type"] == "run_done"]
    assert len(run_dones) == 1 and run_dones[0] == len(out) - 1  # 只在最后


def test_is_exit_phrase():
    from yibao_brain.server import _is_exit_phrase

    assert _is_exit_phrase("退出")
    assert _is_exit_phrase("退出。")
    assert _is_exit_phrase("谢谢！")
    assert _is_exit_phrase("先这样")
    assert not _is_exit_phrase("先这样了谢谢")  # 混合句不拦，交给 LLM
    assert not _is_exit_phrase("退出了")  # 只整句命中，防误杀
    assert not _is_exit_phrase("好的")


def test_voice_one_shot_unaffected(tmp_path):
    """单轮语音（无 continuous）：行为照旧——run 完即 run_done，不进入第二轮聆听。"""
    provider = FakeProvider(chunks=["你", "好"])
    voice = FakeVoice(texts=["你好", "不该被听到"])
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
    assert _kinds(out).count("listening") == 1
    assert voice.listen_calls == 1
    assert out[-1] == {"type": "run_done", "id": 1}


# ---------- 语音二期③：子句级软切（首句更早出声） ----------


def test_take_sentence_soft_cut_before_terminator():
    """终止标点未出现、够长且遇中等停顿 → 先切下来开播（首句不等完整句号）。"""
    from yibao_brain.voice import _take_sentence

    s, rest = _take_sentence("好的，我来看一下这个问题，然后回答你")
    assert s == "好的，我来看一下这个问题，"
    assert rest == "然后回答你"


def test_take_sentence_terminator_still_wins():
    """终止标点已在缓冲里：整句完整，不被软切碎拆。"""
    from yibao_brain.voice import _take_sentence

    s, rest = _take_sentence("你好，世界。下一句")
    assert s == "你好，世界。"
    assert rest == "下一句"


def test_take_sentence_soft_cut_too_short_waits():
    """软切点太靠前（切碎片不值当）→ 不切，等更多内容。"""
    from yibao_brain.voice import _take_sentence

    assert _take_sentence("嗯，好的，") == (None, "嗯，好的，")
    assert _take_sentence("你好世界") == (None, "你好世界")


def test_take_sentence_force_cut_unchanged():
    """无标点长文照旧按 max_len 强切。"""
    from yibao_brain.voice import _take_sentence

    buf = "啊" * 90
    s, rest = _take_sentence(buf)
    assert s is not None and len(s) <= 90 and rest == buf[len(s):]


# ---------- 可插拔 TTS provider：StreamingPcmSpeaker 基类 ----------
class _NoCancel:
    def is_set(self):
        return False


def test_streaming_base_pipeline_uses_subclass_synth(monkeypatch):
    """基类 speak_stream 管道调用子类 _synth_pcm，逐句播放。"""
    from yibao_brain.voice import StreamingPcmSpeaker

    class _Fake(StreamingPcmSpeaker):
        name = "fake"

        async def _synth_pcm(self, text):
            return f"<{text}>"

    s = _Fake()
    played = []

    async def fake_play(pcm, cancel, **_kw):
        played.append(pcm)

    monkeypatch.setattr(s, "_play_pcm", fake_play)
    asyncio.run(s.speak_stream(_async_gen(["一句。", "两句。"]), _NoCancel()))
    assert played == ["<一句。>", "<两句。>"]


# ---------- build_speaker 选择 + 兜底 ----------
class _FakeProvider(StreamingPcmSpeaker):
    def __init__(self, tag, ok):
        self.name = tag
        self._ok = ok

    def available(self):
        return self._ok


def test_tts_provider_reads_settings_then_env(monkeypatch):
    """env 优先于 settings.json；非法值回退 edge。"""
    from yibao_brain import config

    monkeypatch.delenv("YIBAO_TTS_PROVIDER", raising=False)
    monkeypatch.setattr(config, "load_settings", lambda: {"tts.provider": "cosyvoice_cloud"})
    assert config.tts_provider() == "cosyvoice_cloud"
    monkeypatch.setenv("YIBAO_TTS_PROVIDER", "cosyvoice")  # env 优先
    assert config.tts_provider() == "cosyvoice"
    monkeypatch.delenv("YIBAO_TTS_PROVIDER", raising=False)
    monkeypatch.setattr(config, "load_settings", lambda: {"tts.provider": "bogus"})  # 非法→edge
    assert config.tts_provider() == "edge"
    from yibao_brain.voice import build_speaker

    s = build_speaker(provider="cosyvoice",
                      edge=lambda: _FakeProvider("edge", True),
                      cosyvoice=lambda: _FakeProvider("cv", True),
                      cosyvoice_cloud=lambda: _FakeProvider("cvc", True))
    assert s.name == "cv"


def test_build_speaker_falls_back_to_edge_when_unavailable():
    from yibao_brain.voice import build_speaker

    s = build_speaker(provider="cosyvoice",
                      edge=lambda: _FakeProvider("edge", True),
                      cosyvoice=lambda: _FakeProvider("cv", False),
                      cosyvoice_cloud=lambda: _FakeProvider("cvc", True))
    assert s.name == "edge"


# ---------- CosyVoice 云 provider（fake client）----------
class _FakeCloudClient:
    def __init__(self):
        self.calls = []

    async def synth(self, text, model, voice, key):
        import numpy as np

        self.calls.append(text)
        return (np.ones(24000, dtype=np.float32) * 0.1).tobytes()


def test_cosyvoice_cloud_synth_uses_client_and_returns_pcm(monkeypatch):
    from yibao_brain.voice import CosyVoiceCloudSpeaker

    fake = _FakeCloudClient()
    spk = CosyVoiceCloudSpeaker(client_factory=lambda: fake, key="k")
    assert spk.available() is True
    played = []

    async def fake_play(pcm, cancel, **_kw):
        played.append(pcm)

    monkeypatch.setattr(spk, "_play_pcm", fake_play)
    asyncio.run(spk.speak_stream(_async_gen(["你好。"]), _NoCancel()))
    assert fake.calls == ["你好。"]
    assert played and len(played[0]) > 0


def test_cosyvoice_cloud_skip_when_synth_raises(monkeypatch):
    from yibao_brain.voice import CosyVoiceCloudSpeaker

    class _Boom:
        async def synth(self, *a, **k):
            raise RuntimeError("net down")

    spk = CosyVoiceCloudSpeaker(client_factory=lambda: _Boom(), key="k")
    played = []
    monkeypatch.setattr(spk, "_play_pcm", lambda pcm, c, **_kw: played.append(pcm))
    asyncio.run(spk.speak_stream(_async_gen(["你好。"]), _NoCancel()))  # 合成抛错 → 跳过该句，不杀整段
    assert played == []


# ---------- CosyVoice 本地 provider（fake client）----------
class _FakeLocalClient:
    def __init__(self):
        self.sft = []
        self.clone = []

    def inference_sft(self, text, voice, stream=True):
        import numpy as np

        self.sft.append((text, voice))
        return [(24000, np.ones(2400, dtype=np.int16))]

    def inference_zero_shot(self, text, prompt_text, prompt_audio, stream=True):
        import numpy as np

        self.clone.append((text, prompt_text, prompt_audio))
        return [(24000, np.ones(2400, dtype=np.int16))]


def test_cosyvoice_local_sft_when_no_prompt(monkeypatch):
    from yibao_brain.voice import CosyVoiceSpeaker

    fake = _FakeLocalClient()
    spk = CosyVoiceSpeaker(client_factory=lambda: fake)
    assert spk.available() is True
    played = []

    async def _play(pcm, c, **_kw):
        played.append(pcm)

    monkeypatch.setattr(spk, "_play_pcm", _play)
    asyncio.run(spk.speak_stream(_async_gen(["你好。"]), _NoCancel()))
    assert fake.sft and not fake.clone
    assert played


def test_cosyvoice_local_clones_when_prompt_audio(monkeypatch):
    from yibao_brain.voice import CosyVoiceSpeaker

    fake = _FakeLocalClient()
    spk = CosyVoiceSpeaker(client_factory=lambda: fake, prompt_audio="/x.wav", prompt_text="示例台词")

    async def _play(pcm, c, **_kw):
        ...

    monkeypatch.setattr(spk, "_play_pcm", _play)
    asyncio.run(spk.speak_stream(_async_gen(["你好。"]), _NoCancel()))
    assert fake.clone and not fake.sft
    assert fake.clone[0][1] == "示例台词"


# ---------- 爆音修复：首尾淡入淡出 ----------


def test_fade_edges_ramps_ends_to_zero():
    """首尾淡入淡出：两端样本归零、fade 中点半幅、中段原样（防开关流 click）。"""
    import numpy as np

    from yibao_brain.voice import _fade_edges

    pcm = np.full(2400, 0.8, dtype=np.float32)  # 24k 下 100ms 恒 0.8 信号
    out = _fade_edges(pcm, 24000, fade_sec=0.01)  # 首尾各 10ms = 240 样本
    assert out[0] == 0.0 and out[-1] == 0.0
    assert abs(out[239] - 0.8) < 1e-6  # 淡入完成处恢复到原电平（linspace 含端点）
    assert abs(out[1200] - 0.8) < 1e-6  # 中段不动
    assert out is not pcm  # 返回新数组，不就地改原 PCM


def test_fade_edges_short_audio_no_overlap():
    """极短音频：fade 长度按样本折半，首尾不重叠也不越界（不炸、仍归零）。"""
    import numpy as np

    from yibao_brain.voice import _fade_edges

    pcm = np.ones(8, dtype=np.float32)
    out = _fade_edges(pcm, 24000, fade_sec=0.015)
    assert out[0] == 0.0 and out[-1] == 0.0
    assert len(out) == 8


def test_fade_edges_empty_pcm():
    """空 PCM 原样返回（合成失败兜底路径不炸）。"""
    import numpy as np

    from yibao_brain.voice import _fade_edges

    pcm = np.zeros(0, dtype=np.float32)
    assert _fade_edges(pcm, 24000) is pcm


# ---------- 爆音修复二：常驻单流（杜绝设备级开关流 pop）----------


def _patch_fake_sounddevice(monkeypatch):
    """注册一个由后台线程持续驱动回调的假 sounddevice，记录 start/close 次数。"""
    import sys
    import threading
    import time
    import types

    import numpy as np

    starts = {"n": 0}
    closes = {"n": 0}

    class _FakeStream:
        def __init__(self, **kw):
            self._cb = kw["callback"]
            self._stop = False

        def start(self):
            starts["n"] += 1

            def _drive():
                outdata = np.zeros((32, 1), dtype=np.float32)
                while not self._stop:
                    self._cb(outdata, 32, None, None)
                    time.sleep(0.002)

            threading.Thread(target=_drive, daemon=True).start()

        def close(self):
            closes["n"] += 1
            self._stop = True

    fake_sd = types.SimpleNamespace(OutputStream=_FakeStream)
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    return starts, closes


def test_persistent_player_reuses_single_stream(monkeypatch):
    """两次播放复用同一 OutputStream（start 只调一次）——常驻流核心保证，杜绝句间开关流 pop。"""
    import numpy as np

    from yibao_brain.voice import _PersistentPlayer

    starts, closes = _patch_fake_sounddevice(monkeypatch)

    async def _go():
        player = _PersistentPlayer()
        cancel = asyncio.Event()
        pcm = np.full(320, 0.5, dtype=np.float32)
        await player.play(pcm, cancel)  # 第一段：建流
        await player.play(pcm, cancel)  # 第二段：复用同一流，不重建
        assert starts["n"] == 1
        assert closes["n"] == 0
        player._stream.close()  # 收尾停驱动线程（测试环境清理，非业务路径）

    asyncio.run(_go())


def test_persistent_player_interrupt_keeps_stream_open(monkeypatch):
    """打断后静音待机不关流：cancel 命中 play 及时返回，流不被 close（下次直接复用）。"""
    import time

    import numpy as np

    from yibao_brain.voice import _PersistentPlayer

    starts, closes = _patch_fake_sounddevice(monkeypatch)

    async def _go():
        player = _PersistentPlayer()
        pcm = np.full(320, 0.5, dtype=np.float32)
        await player.play(pcm, asyncio.Event())  # 先正常播一段，流已建
        cancel = asyncio.Event()
        cancel.set()
        t0 = time.monotonic()
        await player.play(pcm, cancel)  # cancel 已置位 → 应立即返回
        assert time.monotonic() - t0 < 1.0  # 不阻塞、不超时
        assert starts["n"] == 1  # 不重建流
        assert closes["n"] == 0  # 不关流
        player._stream.close()  # 收尾停驱动线程（测试环境清理，非业务路径）

    asyncio.run(_go())


# ---------- TTS 延迟优化：edge 边收边播（流式解码）+ websocket 连接复用 ----------


def test_produce_sentence_marks_fade_edges_for_streamed_pieces(monkeypatch):
    """流式分段：句首段淡入、句尾段淡出、句中段不衰减（防逐段衰减叠出音量坑）。"""
    from yibao_brain.voice import EdgeTtsSpeaker

    speaker = EdgeTtsSpeaker()
    played = []

    async def fake_stream(text):
        yield "p1"
        yield "p2"
        yield "p3"

    async def fake_play(pcm, cancel, *, fade_in=True, fade_out=True):
        played.append((pcm, fade_in, fade_out))

    monkeypatch.setattr(speaker, "_stream_sentence_pcm", fake_stream)
    monkeypatch.setattr(speaker, "_play_pcm", fake_play)

    cancel = asyncio.Event()
    asyncio.run(speaker.speak_stream(_async_gen(["一句。"]), cancel))
    assert played == [("p1", True, False), ("p2", False, False), ("p3", False, True)]


def test_mp3_pipe_source_reads_across_chunk_boundaries():
    """字节管道：短读（有多少给多少，调用方循环凑）、EOF 收尾、close 放走干等的读（打断路径）。"""
    from yibao_brain.voice import _Mp3PipeSource

    src = _Mp3PipeSource()
    src.feed(b"ab")
    src.feed(b"cdef")
    got = b""
    while len(got) < 6:
        piece = src.read(3)  # 短读：一次可能给不足 3 字节
        assert piece, "数据没收完不该 EOF"
        got += piece
    assert got == b"abcdef"
    src.feed_eof()
    assert src.read(1) == b""  # EOF

    src2 = _Mp3PipeSource()
    src2.close()  # 打断：未喂数据也立即 EOF，解码线程不干等
    assert src2.read(1) == b""


def test_decode_mp3_pipe_pushes_pieces_and_sentinel(monkeypatch):
    """解码线程：miniaudio 流式输出逐段进 asyncio 队列，None 哨兵收尾。"""
    import array
    import sys
    import types

    import numpy as np

    from yibao_brain import voice as voice_mod

    fake_miniaudio = types.SimpleNamespace(
        FileFormat=types.SimpleNamespace(MP3="mp3"),
        SampleFormat=types.SimpleNamespace(FLOAT32="f32"),
        stream_any=lambda src, **kw: iter([array.array("f", [0.1, 0.2]), array.array("f", [0.3])]),
    )
    monkeypatch.setitem(sys.modules, "miniaudio", fake_miniaudio)

    async def _go():
        q: asyncio.Queue = asyncio.Queue()
        voice_mod._decode_mp3_pipe(voice_mod._Mp3PipeSource(), q, asyncio.get_running_loop())
        return [await q.get(), await q.get(), await q.get()]

    items = asyncio.run(_go())
    assert np.allclose(items[0], [0.1, 0.2]) and np.allclose(items[1], [0.3])
    assert items[2] is None


def test_stream_sentence_pcm_feeds_pipe_and_yields_decoded(monkeypatch):
    """边收边解管道：抓取的 mp3 chunk 无损进管道，解码片段按序产出。"""
    import threading

    from yibao_brain.voice import EdgeTtsSpeaker

    speaker = EdgeTtsSpeaker()
    fed: list[str] = []

    async def fake_fetch(text):
        fed.append(text)
        yield b"aa"
        yield b"bb"

    def fake_start_decoder(src, pcm_q, loop):
        def _drain():
            data = b""
            while True:
                chunk = src.read(3)  # 跨 chunk 边界读
                if not chunk:
                    break
                data += chunk
            loop.call_soon_threadsafe(pcm_q.put_nowait, data)
            loop.call_soon_threadsafe(pcm_q.put_nowait, None)

        threading.Thread(target=_drain, daemon=True).start()

    monkeypatch.setattr(speaker, "_fetch_mp3_chunks", fake_fetch)
    monkeypatch.setattr(speaker, "_start_decoder", fake_start_decoder)

    async def _go():
        return [p async for p in speaker._stream_sentence_pcm("你好。")]

    assert asyncio.run(_go()) == [b"aabb"]
    assert fed == ["你好。"]


def test_stream_sentence_pcm_cancel_cleans_up_fetch_and_decoder(monkeypatch):
    """打断（取消命中合成中）：抓取任务被取消、管道 close 放解码线程退出（不泄漏）。"""
    import threading
    import time

    from yibao_brain.voice import EdgeTtsSpeaker

    speaker = EdgeTtsSpeaker()
    flags = {"fetch_cancelled": False, "decoder_exited": False}

    async def fake_fetch(text):
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            flags["fetch_cancelled"] = True
            raise
        yield b"x"  # pragma: no cover

    def fake_start_decoder(src, pcm_q, loop):
        def _drain():
            src.read(4096)  # 干等数据；管道 close 后读到 EOF 返回
            flags["decoder_exited"] = True

        threading.Thread(target=_drain, daemon=True).start()

    monkeypatch.setattr(speaker, "_fetch_mp3_chunks", fake_fetch)
    monkeypatch.setattr(speaker, "_start_decoder", fake_start_decoder)

    async def _go():
        gen = speaker._stream_sentence_pcm("你好。")
        task = asyncio.create_task(gen.__anext__())
        await asyncio.sleep(0.1)  # 让抓取/解码就位
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await gen.aclose()

    asyncio.run(_go())
    assert flags["fetch_cancelled"]
    for _ in range(100):
        if flags["decoder_exited"]:
            break
        time.sleep(0.02)
    assert flags["decoder_exited"]


def test_edge_connector_reused_across_sentences_and_recycled():
    """连接池复用：同循环共享同一池；edge-tts 每句 session 关闭连带调的 close 是空操作
    （池不真关，keep-alive 连接留给下一句）；失效兜底丢池重建。"""
    from yibao_brain.voice import EdgeTtsSpeaker

    speaker = EdgeTtsSpeaker()

    async def _go():
        c1 = speaker._get_connector()
        assert speaker._get_connector() is c1  # 连续句子复用同一池
        await c1.close()  # edge-tts 每句 ClientSession 关闭时的连带调用：不真关
        assert not c1.closed
        assert speaker._get_connector() is c1  # 池还活着，继续用
        speaker._reset_connector()  # 失效兜底：丢池
        c2 = speaker._get_connector()
        assert c2 is not c1 and not c2.closed
        await c2.force_close()  # 真关闭走 force_close（测试清理）
        assert c2.closed

    asyncio.run(_go())


def test_edge_synth_stream_retries_once_with_fresh_pool(monkeypatch):
    """未产出音频就失败：丢池重连重试一次（重连兜底）；一直失败则重试一次后跳过该句。"""
    from yibao_brain.voice import EdgeTtsSpeaker

    speaker = EdgeTtsSpeaker()
    resets = []
    monkeypatch.setattr(speaker, "_reset_connector", lambda: resets.append(1))

    attempts = []

    async def flaky(text):
        attempts.append(text)
        if len(attempts) == 1:
            raise RuntimeError("connection reset by peer")
        yield "pcm"

    monkeypatch.setattr(speaker, "_stream_sentence_pcm", flaky)

    async def _go():
        return [p async for p in speaker._synth_pcm_stream("你好。")]

    assert asyncio.run(_go()) == ["pcm"]
    assert attempts == ["你好。", "你好。"] and resets == [1]

    attempts.clear()

    async def always_fail(text):
        attempts.append(text)
        raise RuntimeError("down")
        yield  # pragma: no cover - 让它是 async generator

    monkeypatch.setattr(speaker, "_stream_sentence_pcm", always_fail)
    assert asyncio.run(_go()) == []  # 重试仍败 → 跳过该句，不无限重试
    assert len(attempts) == 2


def test_edge_synth_stream_no_retry_after_partial_output(monkeypatch):
    """已产出片段后失败不重试（重试会把已播片段再念一遍），保留已产片段、跳过剩余。"""
    from yibao_brain.voice import EdgeTtsSpeaker

    speaker = EdgeTtsSpeaker()

    def _no_reset():
        raise AssertionError("已产出片段后不该重试")

    monkeypatch.setattr(speaker, "_reset_connector", _no_reset)
    attempts = []

    async def partial_then_fail(text):
        attempts.append(text)
        yield "p1"
        raise RuntimeError("mid-stream reset")

    monkeypatch.setattr(speaker, "_stream_sentence_pcm", partial_then_fail)

    async def _go():
        return [p async for p in speaker._synth_pcm_stream("你好。")]

    assert asyncio.run(_go()) == ["p1"]
    assert len(attempts) == 1
