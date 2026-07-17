"""语音能力（Plan 4a 最小版 + Plan 4b 流式/打断）：录音+VAD→STT，TTS→播放。

speak_stream（4b）：按句切分流式文本 → edge-tts stream 取 mp3 → miniaudio 解码
→ sounddevice 非阻塞播放 + 30ms 轮询 cancel，命中即 stop（"三连取消"之一：停 TTS）。
组件 factory 注入：真实现用 sherpa-onnx/sounddevice/edge-tts/miniaudio；
测试用 tests/fakes.py 的 Fake*。
"""
from __future__ import annotations

import re
from collections.abc import AsyncIterator
from pathlib import Path


class VoiceCapability:
    """聚合 Recognizer/Recorder/Speaker。listen()=录音→STT 返文字；speak/speak_stream=TTS→播放。"""

    def __init__(self, recognizer, recorder, speaker):
        self.recognizer = recognizer
        self.recorder = recorder
        self.speaker = speaker

    def listen(self) -> str:
        pcm = self.recorder.record_until_silence()
        return self.recognizer.transcribe(pcm)

    def speak(self, text: str) -> None:
        if text:
            self.speaker.speak(text)

    async def speak_stream(self, text_iter: AsyncIterator[str], cancel) -> None:
        await self.speaker.speak_stream(text_iter, cancel)


class SherpaRecognizer:
    """sherpa-onnx 非流式 Paraformer 中文（model.int8.onnx + tokens.txt）。"""

    def __init__(self, model_dir: str):
        import sherpa_onnx

        d = Path(model_dir)
        self._rec = sherpa_onnx.OfflineRecognizer.from_paraformer(
            paraformer=str(d / "model.int8.onnx"),
            tokens=str(d / "tokens.txt"),
            num_threads=2,
            sample_rate=16000,
            feature_dim=80,
            decoding_method="greedy_search",
            debug=False,
        )

    def transcribe(self, pcm) -> str:
        stream = self._rec.create_stream()
        stream.accept_waveform(16000, pcm)
        self._rec.decode_stream(stream)
        return (stream.result.text or "").strip()


class SounddeviceRecorder:
    """sounddevice 录音 + Silero VAD：说完一句（静音 min_silence）自动停，返回该段 PCM。"""

    def __init__(self, vad_model: str, max_seconds: int = 10, min_silence: float = 0.5):
        self._vad_model = vad_model
        self._max = max_seconds
        self._min_silence = min_silence

    def record_until_silence(self):
        import numpy as np
        import sherpa_onnx
        import sounddevice as sd

        cfg = sherpa_onnx.VadModelConfig()
        cfg.silero_vad.model = self._vad_model
        cfg.silero_vad.min_silence_duration = self._min_silence
        cfg.sample_rate = 16000
        vad = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=self._max)
        window = cfg.silero_vad.window_size  # Silero 每次喂的样本数（512）

        SR = 16000
        buf = np.array([], dtype=np.float32)
        total = 0
        with sd.InputStream(channels=1, dtype="float32", samplerate=SR) as s:
            while total < SR * self._max:
                samples, _ = s.read(int(0.1 * SR))  # 每 100ms 读一批
                if len(samples):
                    buf = np.concatenate([buf, samples.reshape(-1)])
                    total += len(samples)
                    while len(buf) >= window:
                        vad.accept_waveform(buf[:window])
                        buf = buf[window:]
                    while not vad.empty():
                        # VAD 切出一段完整语音（说完一句）→ 返回
                        seg = np.array(vad.front.samples, dtype=np.float32)
                        vad.pop()
                        return seg
        return np.zeros(SR, dtype=np.float32)  # 超时无语音


class EdgeTtsSpeaker:
    """edge-tts 合成（zh-CN-XiaoxiaoNeural）→ miniaudio 解码 → sounddevice 播放。

    speak（同步，4a）：整段合成→阻塞播放。
    speak_stream（4b）：按句流式合成→边收边播，cancel 命中立即 stop。
    """

    def __init__(self, voice: str = "zh-CN-XiaoxiaoNeural"):
        self._voice = voice

    def speak(self, text: str) -> None:
        import asyncio

        asyncio.run(self._speak_one(text, _NeverCancel()))

    async def speak_stream(self, text_iter: AsyncIterator[str], cancel) -> None:
        """边收 LLM 文本增量边按句播报；cancel.is_set() 立即停（清队列 + stop 播放）。"""
        buf = ""
        async for delta in text_iter:
            if cancel.is_set():
                return
            buf += delta
            while True:  # 把已凑齐的整句全部冲掉
                sentence, rest = _take_sentence(buf)
                if sentence is None:
                    break
                buf = rest
                if cancel.is_set():
                    return
                await self._speak_one(sentence, cancel)
                if cancel.is_set():
                    return
        tail = buf.strip()
        if tail and not cancel.is_set():
            await self._speak_one(tail, cancel)

    async def _speak_one(self, text: str, cancel) -> None:
        import asyncio

        if not text or not text.strip():
            return
        mp3 = await self._fetch_mp3(text)
        if cancel.is_set():
            return
        pcm = _decode_mp3(mp3)
        if len(pcm) == 0:
            return
        import sounddevice as sd

        sd.play(pcm, samplerate=24000)
        try:
            while True:
                stream = sd.get_stream()
                if stream is None or not stream.active:
                    break
                if cancel.is_set():
                    sd.stop()
                    return
                await asyncio.sleep(0.03)
        finally:
            try:
                sd.stop()
            except Exception:
                pass

    async def _fetch_mp3(self, text: str) -> bytes:
        import edge_tts

        com = edge_tts.Communicate(text, self._voice)
        chunks: list[bytes] = []
        async for piece in com.stream():
            if piece["type"] == "audio":
                chunks.append(piece["data"])
        return b"".join(chunks)


class _NeverCancel:
    """speak()（同步路径）用的占位 cancel，永不触发。"""

    def is_set(self) -> bool:
        return False


_SENT_RE = re.compile(r"[。！？!?…\n]")


def _take_sentence(buf: str, max_len: int = 80):
    """从 buf 头部切一句（到终止标点）；无标点但超 max_len 则按最近逗号/空格强切。

    返回 (sentence, rest)；无可切返回 (None, buf)。
    """
    m = _SENT_RE.search(buf)
    if m:
        return buf[: m.end()], buf[m.end():]
    if len(buf) >= max_len:
        cut = max(buf.rfind("，"), buf.rfind("、"), buf.rfind(" "))
        if cut <= 0:
            cut = max_len - 1
        return buf[: cut + 1], buf[cut + 1:]
    return None, buf


def _decode_mp3(mp3_bytes: bytes):
    """miniaudio 解码 mp3 字节 → float32 mono 24k PCM（numpy 数组）。"""
    import miniaudio
    import numpy as np

    if not mp3_bytes:
        return np.zeros(0, dtype=np.float32)
    dec = miniaudio.decode(
        mp3_bytes,
        output_format=miniaudio.SampleFormat.FLOAT32,
        nchannels=1,
        sample_rate=24000,
    )
    return np.frombuffer(dec.samples, dtype=np.float32)


def build_voice(model_dir: str, vad_model: str, voice_name: str) -> VoiceCapability:
    """生产装配：sherpa STT + sounddevice 录 + edge-tts 播。"""
    return VoiceCapability(
        SherpaRecognizer(model_dir),
        SounddeviceRecorder(vad_model=vad_model),
        EdgeTtsSpeaker(voice_name),
    )
