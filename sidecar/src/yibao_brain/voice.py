"""语音能力（Plan 4a 最小版）：录音+VAD→STT，TTS→播放。

非流式、无打断（4b 再上 async 重构 + 流式 + 三连取消）。
组件 factory 注入：真实现用 sherpa-onnx/sounddevice/edge-tts/miniaudio；
测试用 tests/fakes.py 的 Fake*。
"""
from __future__ import annotations

from pathlib import Path


class VoiceCapability:
    """聚合 Recognizer/Recorder/Speaker。listen()=录音→STT 返文字；speak(text)=TTS→播放。"""

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
    """edge-tts 合成（zh-CN-XiaoxiaoNeural）→ miniaudio 解码 → sounddevice 阻塞播放。"""

    def __init__(self, voice: str = "zh-CN-XiaoxiaoNeural"):
        self._voice = voice

    def speak(self, text: str) -> None:
        import asyncio
        import os
        import tempfile

        import edge_tts
        import miniaudio
        import numpy as np
        import sounddevice as sd

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            path = f.name
        try:
            asyncio.run(edge_tts.Communicate(text, self._voice).save(path))
            dec = miniaudio.decode_file(
                path, output_format=miniaudio.SampleFormat.FLOAT32, nchannels=1, sample_rate=24000
            )
            pcm = np.frombuffer(dec.samples, dtype=np.float32)
            if len(pcm):
                sd.play(pcm, samplerate=24000)
                sd.wait()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass


def build_voice(model_dir: str, vad_model: str, voice_name: str) -> VoiceCapability:
    """生产装配：sherpa STT + sounddevice 录 + edge-tts 播。"""
    return VoiceCapability(
        SherpaRecognizer(model_dir),
        SounddeviceRecorder(vad_model=vad_model),
        EdgeTtsSpeaker(voice_name),
    )
