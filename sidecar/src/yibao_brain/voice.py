"""语音能力（Plan 4a 最小版 + Plan 4b 流式/打断 + Plan 5 体验修复）：录音+VAD→STT，TTS→播放。

speak_stream（5）：按句切分 → 生产者/消费者管道 —— 播当前句的同时预合成下一句
（旧实现句间串行：合成整句→播完→再合成下一句，句间有完整网络延迟，听着一顿一顿）。
cancel 命中即停（"三连取消"之一：停 TTS）。
组件 factory 注入：真实现用 sherpa-onnx/sounddevice/edge-tts/miniaudio；
测试用 tests/fakes.py 的 Fake*。
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import threading
import time
import warnings
from collections.abc import AsyncIterator
from pathlib import Path

from .config import (
    cosyvoice_cloud_key, cosyvoice_cloud_model, cosyvoice_cloud_voice,
    cosyvoice_model_path, cosyvoice_prompt_audio, cosyvoice_prompt_text,
    cosyvoice_voice, tts_provider, tts_voice,
)

# sounddevice 在 NumPy 2.5+ 下每次播放刷 4 行 DeprecationWarning（库内旧用法，非我们能修），压住
_SD_WARN_MSG = "Setting the shape on a NumPy array.*"
warnings.filterwarnings("ignore", message=_SD_WARN_MSG, category=DeprecationWarning)


def _silence_sd_warnings() -> None:
    """调用点补压一次：模块级过滤可能被后到的第三方 reset/simplefilter 盖掉（幂等，不重复堆过滤器）。"""
    for f in warnings.filters:
        if f[1] is not None and f[1].pattern == _SD_WARN_MSG:
            return
    warnings.filterwarnings("ignore", message=_SD_WARN_MSG, category=DeprecationWarning)


class VoiceCapability:
    """聚合 Recognizer/Recorder/Speaker。listen()=录音→STT 返文字；speak/speak_stream=TTS→播放。"""

    def __init__(self, recognizer, recorder, speaker):
        self.recognizer = recognizer
        self.recorder = recorder
        self.speaker = speaker

    def listen(self) -> str:
        pcm = self.recorder.record_until_silence()
        return self.recognizer.transcribe(pcm)

    def stop_listen(self) -> None:
        """请求停止进行中的录音（interrupt 通路）；recorder 不支持则静默忽略。"""
        stop = getattr(self.recorder, "stop", None)
        if callable(stop):
            stop()

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


class LazySherpaRecognizer:
    """首次 transcribe 才加载 STT 模型：大脑启动不背模型加载（秒级）开销。

    加载发生在 server 的 executor 线程（listen 经 run_in_executor 调用），
    不压事件循环——看门狗 ping 不受影响。加载失败原样抛出（下次重试）。
    """

    def __init__(self, model_dir: str):
        self._model_dir = model_dir
        self._rec: SherpaRecognizer | None = None
        self._lock = threading.Lock()

    def transcribe(self, pcm) -> str:
        if self._rec is None:
            with self._lock:
                if self._rec is None:
                    print("[yibao] 首次语音输入，加载 STT 模型…", file=sys.stderr)
                    self._rec = SherpaRecognizer(self._model_dir)
        return self._rec.transcribe(pcm)


class SounddeviceRecorder:
    """sounddevice 录音 + Silero VAD：说完一句（静音 min_silence）自动停，返回该段 PCM。

    min_silence 默认 1.2s：说话中途的自然停顿（<1.2s）不会被误判为说完
    （0.9s 对「边想边说」的停顿仍偏短，二期上调）。
    采集走回调+队列（不是阻塞 s.read）：旧实现设备停发帧时 read 永不返回，
    录音循环与 stop() 信号一起卡死（「一直聆听中、无法取消」的根因）。
    """

    def __init__(self, vad_model: str, max_seconds: int = 30, min_silence: float = 1.2, no_frame_timeout: float = 3.0):
        self._vad_model = vad_model
        self._max = max_seconds
        self._min_silence = min_silence
        self._no_frame_timeout = no_frame_timeout  # 开局这么久一帧都没有 = 设备被占用/掉线
        self._stop = threading.Event()

    def stop(self) -> None:
        """外部打断（interrupt）：录音循环下一拍退出，返回空段（= 未识别到语音）。"""
        self._stop.set()

    def record_until_silence(self):
        import queue

        import numpy as np
        import sherpa_onnx
        import sounddevice as sd

        _silence_sd_warnings()
        self._stop.clear()
        cfg = sherpa_onnx.VadModelConfig()
        cfg.silero_vad.model = self._vad_model
        cfg.silero_vad.min_silence_duration = self._min_silence
        cfg.sample_rate = 16000
        vad = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=self._max)
        window = cfg.silero_vad.window_size  # Silero 每次喂的样本数（512）

        SR = 16000
        q: queue.Queue = queue.Queue()
        stats = {"frames": 0}

        def _on_audio(indata, frames, time_info, status):
            try:
                stats["frames"] += frames
                q.put_nowait(indata.reshape(-1).copy())
            except Exception:  # 回调里任何异常都会让 CoreAudio 停流，宁丢帧不炸流
                pass

        buf = np.array([], dtype=np.float32)
        total = 0
        t0 = time.monotonic()
        speech_logged = False
        print("[yibao] 录音开始", file=sys.stderr)
        with sd.InputStream(
            channels=1, dtype="float32", samplerate=SR, blocksize=int(0.1 * SR), callback=_on_audio
        ):
            while total < SR * self._max and not self._stop.is_set():
                try:
                    samples = q.get(timeout=0.1)  # 带超时取：stop 信号 100ms 内必被看到
                except queue.Empty:
                    if stats["frames"] == 0 and time.monotonic() - t0 > self._no_frame_timeout:
                        print("[yibao] 麦克风一直无音频帧（被占用/掉线？），放弃录音", file=sys.stderr)
                        break
                    continue
                if not len(samples):
                    continue
                buf = np.concatenate([buf, samples])
                total += len(samples)
                while len(buf) >= window:
                    vad.accept_waveform(buf[:window])
                    buf = buf[window:]
                if not speech_logged and vad.is_speech_detected():
                    speech_logged = True
                    print("[yibao] VAD 检测到语音", file=sys.stderr)
                while not vad.empty():
                    # VAD 切出一段完整语音（说完一句）→ 返回
                    seg = np.array(vad.front.samples, dtype=np.float32)
                    vad.pop()
                    print(f"[yibao] 识别到完整语句（{time.monotonic() - t0:.1f}s），结束录音", file=sys.stderr)
                    return seg
        why = "被打断" if self._stop.is_set() else "超时/无语音"
        print(f"[yibao] 录音结束：{why}（{time.monotonic() - t0:.1f}s，{stats['frames']} 帧）", file=sys.stderr)
        return np.zeros(SR, dtype=np.float32)  # 超时/被打断：无语音


class _PersistentPlayer:
    """常驻输出流播放器：杜绝每次开/关流产生的设备级爆音。

    背景：旧实现每句一个 OutputStream（开→播→关）。macOS 上每次开关 CoreAudio
    输出流都伴随 IO 线程启停 + 采样率协商（TTS 24k vs 设备默认 44.1k/48k），
    设备层直接"啪"——这是数字信号淡入淡出消不掉的（fade 只作用于样本值，
    设备启停的 pop 发生在 DAC/驱动层）。speak_stream 按句切分、长回复每 2-3 秒
    一句，每句一开一关就"总是"爆音。

    本播放器首次播放懒建**单个** OutputStream 后常驻：
    - 句间/会话间不关流，队列空时输出静音待机（无设备开关，无 pop）；
    - 打断：清未播段 + 对当前残留 ~30ms 淡出到 0，随后静音待机**也不关流**
      （下次播放直接复用，连重建流的 pop 都省掉）；
    - 段播完：async 侧经 asyncio.Event 收到完成信号（逐段 await，保持
      speak_stream 的句间节奏与 cancel 语义）。
    """

    def __init__(self, samplerate: int = 24000):
        import queue

        self._sr = samplerate
        self._stream = None  # 常驻 sd.OutputStream（async 线程独占访问）
        self._q: queue.Queue = queue.Queue()  # 段队列（音频回调线程消费）
        self._cur = None  # 当前正在播的段（float32 1D）
        self._cur_idx = 0
        self._seg_done: asyncio.Event | None = None  # 当前段播完信号
        self._loop: asyncio.AbstractEventLoop | None = None
        self._cancel = None  # 当前段的 cancel（asyncio.Event.is_set 线程安全）

    async def play(self, pcm, cancel) -> None:
        """塞一段 float32 24k PCM，等它播完返回。句首尾淡入淡出防数字层 click。"""
        import numpy as np
        import sounddevice as sd

        _silence_sd_warnings()
        pcm = np.asarray(pcm, dtype=np.float32)
        if pcm.size == 0:
            return
        pcm = _fade_edges(pcm, self._sr, fade_sec=0.015)

        self._cancel = cancel
        self._loop = asyncio.get_running_loop()
        evt = asyncio.Event()
        self._seg_done = evt
        self._q.put(pcm)

        if self._stream is None:
            self._stream = sd.OutputStream(
                samplerate=self._sr,
                channels=1,
                dtype="float32",
                callback=self._callback,
                blocksize=1024,  # ~43ms/帧：回调频率与延迟的平衡
                latency="low",
            )
            self._stream.start()
        try:
            await asyncio.wait_for(evt.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            # 兜底：回调长期不结束（设备异常/掉线），回收流防卡死；下次 play 重建
            s, self._stream = self._stream, None
            if s is not None:
                try:
                    s.close()
                except Exception:
                    pass
            raise

    def _callback(self, outdata, frames, _time_info, _status):
        import queue

        import numpy as np

        flat = outdata.reshape(-1)  # channels=1：(frames,1) → (frames,)
        cancel = self._cancel
        if cancel is not None and cancel.is_set():
            # 打断：清未播段 + 当前残留一帧内淡出到 0 → 通知结束 → 静音待机（不关流）
            while True:
                try:
                    self._q.get_nowait()
                except queue.Empty:
                    break
            cur, idx = self._cur, self._cur_idx
            fade_len = 0
            if cur is not None and idx < len(cur):
                fade_len = min(len(cur) - idx, frames, int(self._sr * 0.03))
                if fade_len > 0:
                    seg = cur[idx : idx + fade_len]
                    ramp = np.linspace(1.0, 0.0, fade_len, dtype=np.float32)
                    flat[:fade_len] = seg * ramp
            flat[fade_len:] = 0
            self._cur = None
            self._cur_idx = 0
            self._finish_segment()
            return

        # 当前段播完 → 通知 async 侧 → 取下一段 / 待机静音
        if self._cur is None or self._cur_idx >= len(self._cur):
            if self._cur is not None:
                self._finish_segment()
            try:
                self._cur = self._q.get_nowait()
                self._cur_idx = 0
            except queue.Empty:
                flat[:] = 0  # 待机：流保持打开，输出静音
                return
        cur = self._cur
        idx = self._cur_idx
        n = min(frames, len(cur) - idx)
        flat[:n] = cur[idx : idx + n]
        if n < frames:
            flat[n:] = 0
        self._cur_idx = idx + n

    def _finish_segment(self) -> None:
        """当前段播完/被打断：唤醒 async 侧 play 的等待。回调线程调用，经 loop 投递。"""
        evt, self._seg_done = self._seg_done, None
        if evt is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(evt.set)


class StreamingPcmSpeaker:
    """TTS provider 基类：共享流式管道（按句切分→预取下一句→播放→cancel）。

    子类实现 _synth_pcm(一句文本) -> float32 24k PCM | None。
    speak（同步，4a）：整段合成→阻塞播放。
    speak_stream（4b）：边收文本增量边按句播报；cancel.is_set() 立即停（清队列 + stop 播放）。
    生产者/消费者管道：合成下一句与播放当前句并行，句间不再有完整网络延迟。

    播放走 _PersistentPlayer（常驻单流）：句子之间不关流，杜绝 macOS 设备层
    开关流爆音；打断淡出后静音待机，下次直接复用。
    """
    name = "base"

    def __init__(self):
        self._player = _PersistentPlayer()

    def available(self) -> bool:
        return True

    def speak(self, text: str) -> None:
        asyncio.run(self._speak_one(text, _NeverCancel()))

    async def speak_stream(self, text_iter: AsyncIterator[str], cancel) -> None:
        queue: asyncio.Queue = asyncio.Queue()  # 句子 PCM 队列；None=结束哨兵
        synth_error: list[BaseException] = []

        async def produce() -> None:
            try:
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
                        pcm = await self._synth_pcm(sentence)
                        if pcm is not None:
                            await queue.put(pcm)
                tail = buf.strip()
                if tail and not cancel.is_set():
                    pcm = await self._synth_pcm(tail)
                    if pcm is not None:
                        await queue.put(pcm)
            except asyncio.CancelledError:
                raise  # 正常取消（打断命中合成中），不是合成错误，必须向上传
            except BaseException as e:
                synth_error.append(e)
            finally:
                await queue.put(None)

        async def consume() -> None:
            while True:
                pcm = await queue.get()
                if pcm is None or cancel.is_set():
                    return
                await self._play_pcm(pcm, cancel)

        producer = asyncio.create_task(produce())
        try:
            await consume()
        finally:
            if not producer.done():
                producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)
        if synth_error:
            raise synth_error[0]

    async def _synth_pcm(self, text: str):
        raise NotImplementedError

    async def _play_pcm(self, pcm, cancel) -> None:
        """播放一段 PCM：走常驻单流（_PersistentPlayer），句间不关流。

        - 首尾淡入淡出：消除数字层开关流 click；
        - 打断：对残留快速淡出后静音待机，流保持打开（无设备层 pop）。
        """
        await self._player.play(pcm, cancel)

    async def _speak_one(self, text: str, cancel) -> None:
        if cancel.is_set():
            return
        pcm = await self._synth_pcm(text)
        if pcm is None or cancel.is_set():
            return
        await self._play_pcm(pcm, cancel)


class EdgeTtsSpeaker(StreamingPcmSpeaker):
    """edge-tts 合成（zh-CN-XiaoxiaoNeural）→ miniaudio 解码 → sounddevice 播放。"""

    name = "edge"

    def __init__(self, voice: str = "zh-CN-XiaoxiaoNeural"):
        super().__init__()
        self._voice = voice

    async def _synth_pcm(self, text: str):
        """一句文本 → edge-tts 合成 mp3 → 解码 float32 PCM。

        返回 None 的两种情况（都跳过该句、不杀整段播报）：
        - 无可播内容（空串 / 纯标点——edge-tts 对「？」这类会 NoAudioReceived）
        - edge-tts 单句合成失败（NoAudioReceived 等），记 stderr 留痕
        """
        text = _speech_text(text)
        if not text or not re.search(r"\w", text):
            return None
        try:
            mp3 = await self._fetch_mp3(text)
        except Exception as e:
            print(f"[yibao] 句子合成失败（已跳过）：{text!r} {e}", file=sys.stderr)
            return None
        pcm = _decode_mp3(mp3)
        return pcm if len(pcm) else None

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

_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_LIST_RE = re.compile(r"(?m)^\s*(?:[-*+]|\d+[.、)])\s+")
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF☀-➿⬀-⯿\uFE0F\u20E3]"
)


def _speech_text(text: str) -> str:
    """LLM 回复（Markdown）→ 可播文本：去标记符号、emoji、列表/标题符，链接只念文字。"""
    t = _MD_LINK_RE.sub(r"\1", text)          # [文字](url) → 文字
    t = _MD_LIST_RE.sub("", t)                # 行首「- 」「1. 」等列表标记
    t = re.sub(r"[\d#*]\uFE0F\u20E3", "", t)  # 键帽序列（1️⃣）整体删
    t = re.sub(r"[`*#>~]", "", t)             # 粗斜体/代码/标题/引用/删除线符号
    t = _EMOJI_RE.sub("", t)                  # emoji（念着很奇怪）
    return re.sub(r"\s+", " ", t).strip()


def _take_sentence(buf: str, max_len: int = 80, min_soft: int = 12):
    """从 buf 头部切一句（到终止标点）；无标点但超 max_len 则按最近逗号/空格强切。

    软切（二期）：终止标点还没出现时，buf 已够 min_soft 且遇到中等停顿（，；：、），
    先切下来开播——首句不再等完整句号，首音明显提前；终止标点优先（整句完整不硬拆）。

    返回 (sentence, rest)；无可切返回 (None, buf)。
    """
    m = _SENT_RE.search(buf)
    if m:
        return buf[: m.end()], buf[m.end():]
    if len(buf) >= min_soft:
        cut = max(buf.rfind("，"), buf.rfind("；"), buf.rfind("："), buf.rfind("、"))
        if cut >= min_soft - 1:
            return buf[: cut + 1], buf[cut + 1:]
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


def _fade_edges(pcm, sr: int, fade_sec: float = 0.015):
    """首尾各 fade_sec 线性淡入淡出（零交叉），返回新数组：开关流不产生 click。

    音频过短时 fade 长度按样本数折半，首尾区间不相交。
    """
    import numpy as np

    n = pcm.size
    if n == 0:
        return pcm
    half = max(1, n // 2)
    fade = max(1, min(int(sr * fade_sec), half))
    out = np.array(pcm, dtype=np.float32, copy=True)
    out[:fade] *= np.linspace(0.0, 1.0, fade, dtype=np.float32)
    out[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
    return out


def _can_import(name: str) -> bool:
    """能否 import 某模块（惰性依赖探测）。"""
    try:
        __import__(name)
        return True
    except Exception:
        return False


def _pcm_bytes_to_float32(pcm_bytes: bytes):
    """云 provider 约定交 float32 little-endian PCM 字节 → numpy float32（24k mono）。"""
    import numpy as np

    if not pcm_bytes:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(pcm_bytes, dtype=np.float32)


class CosyVoiceCloudSpeaker(StreamingPcmSpeaker):
    """阿里云百炼 CosyVoice 云 TTS（DashScope）。惰性 import dashscope。

    client_factory 注入用于测试；默认 _DashScopeClient（真 dashscope，需 key，真机验收）。
    client 协议：async synth(text, model, voice, key) -> float32 PCM 字节。
    """
    name = "cosyvoice_cloud"

    def __init__(self, client_factory=None, *, key=None, model=None, voice=None):
        super().__init__()
        self._client_factory = client_factory
        self._key = key if key is not None else cosyvoice_cloud_key()
        self._model = model or cosyvoice_cloud_model()
        self._voice = voice or cosyvoice_cloud_voice()
        self._client = None

    def available(self) -> bool:
        if self._client_factory is not None:
            return True
        return bool(self._key) and _can_import("dashscope")

    def _get_client(self):
        if self._client is None:
            self._client = self._client_factory() if self._client_factory else _DashScopeClient(self._key)
        return self._client

    async def _synth_pcm(self, text: str):
        text = _speech_text(text)
        if not text or not re.search(r"\w", text):
            return None
        try:
            pcm_bytes = await self._get_client().synth(text, self._model, self._voice, self._key)
        except Exception as e:
            print(f"[yibao] 云 TTS 合成失败（已跳过）：{text!r} {e}", file=sys.stderr)
            return None
        pcm = _pcm_bytes_to_float32(pcm_bytes)
        return pcm if len(pcm) else None


class _DashScopeRenderer:
    """dashscope SpeechSynthesizer 回调实现：把 PCM chunk 推进队列（best-effort，真机验收调）。"""
    def __init__(self, q):
        self._q = q

    def on_open(self): ...
    def on_complete(self): self._q.put(None)
    def on_error(self, message): self._q.put(None)
    def on_close(self): self._q.put(None)
    def on_event(self, message): ...
    def on_data(self, data: bytes) -> bool:
        self._q.put(data)
        return True


class _DashScopeClient:
    """阿里云 dashscope CosyVoice 封装（真机验收用）。

    synth 返回 float32 PCM 字节。按 dashscope.audio.tts_v2 文档 best-effort 实现，
    回调/参数细节需配真 key 跑通后微调；失败抛异常由 _synth_pcm 捕获跳过。
    """
    def __init__(self, key: str):
        self._key = key

    async def synth(self, text: str, model: str, voice: str, key: str) -> bytes:
        import asyncio
        from queue import Queue

        import numpy as np
        import dashscope
        from dashscope.audio.tts_v2 import AudioFormat, SpeechSynthesizer

        dashscope.api_key = key or self._key
        q: Queue = Queue()
        ss = SpeechSynthesizer(model=model, voice=voice or None,
                               format=AudioFormat.PCM_24000HZ_MONO, callback=_DashScopeRenderer(q))
        ss.streaming_call(text)
        ss.streaming_complete()
        chunks: list[bytes] = []
        while True:
            chunk = await asyncio.to_thread(q.get, timeout=15)
            if chunk is None:
                break
            chunks.append(chunk)
        arr = np.frombuffer(b"".join(chunks), dtype=np.int16).astype(np.float32) / 32768.0
        return arr.tobytes()


class CosyVoiceSpeaker(StreamingPcmSpeaker):
    """CosyVoice2 本地推理（PyTorch）。惰性 import cosyvoice。

    有 prompt_audio → 零样本克隆（专属音色）；否则 inference_sft 预置音色。
    client_factory 注入测试；默认 _CosyVoice2Client（真模型，需下载，真机验收）。
    client 协议：inference_sft(text, voice, stream) / inference_zero_shot(text, prompt_text, prompt_audio, stream)
    → 迭代 (sample_rate, int16 numpy)。
    """
    name = "cosyvoice"

    def __init__(self, client_factory=None, *, model_path=None, voice=None,
                 prompt_audio=None, prompt_text=None):
        super().__init__()
        self._client_factory = client_factory
        self._model_path = model_path if model_path is not None else cosyvoice_model_path()
        self._voice = voice or cosyvoice_voice()
        self._prompt_audio = prompt_audio if prompt_audio is not None else cosyvoice_prompt_audio()
        self._prompt_text = prompt_text if prompt_text is not None else cosyvoice_prompt_text()
        self._client = None

    def available(self) -> bool:
        if self._client_factory is not None:
            return True
        return bool(self._model_path) and os.path.isdir(self._model_path) and _can_import("cosyvoice")

    def _get_client(self):
        if self._client is None:
            self._client = self._client_factory() if self._client_factory else _CosyVoice2Client(self._model_path)
        return self._client

    async def _synth_pcm(self, text: str):
        text = _speech_text(text)
        if not text or not re.search(r"\w", text):
            return None
        try:
            import numpy as np

            client = self._get_client()
            if self._prompt_audio:
                chunks = client.inference_zero_shot(text, self._prompt_text, self._prompt_audio, stream=True)
            else:
                chunks = client.inference_sft(text, self._voice, stream=True)
            pcms = [np.asarray(a, dtype=np.float32) / 32768.0 for _sr, a in chunks]  # int16 → float32
            pcm = np.concatenate(pcms) if pcms else np.zeros(0, dtype=np.float32)
        except Exception as e:
            print(f"[yibao] 本地 TTS 合成失败（已跳过）：{text!r} {e}", file=sys.stderr)
            return None
        return pcm if len(pcm) else None


class _CosyVoice2Client:
    """官方 CosyVoice2 薄封装（真机验收用）。流式吐 (sr, int16 numpy)，收集为 list。"""
    def __init__(self, model_path: str):
        from cosyvoice.cli.cosyvoice import CosyVoice2  # 惰性；未装由 available() 拦

        self._model = CosyVoice2(model_path)

    def inference_sft(self, text, voice, stream=True):
        return list(self._model.inference_sft(text, voice, stream=stream))

    def inference_zero_shot(self, text, prompt_text, prompt_audio, stream=True):
        return list(self._model.inference_zero_shot(text, prompt_text, prompt_audio, stream=stream))


def build_speaker(*, edge=None, cosyvoice=None, cosyvoice_cloud=None, provider=None):
    """按 config（或注入 provider）选 TTS provider；不可用回退 edge。

    注入的 *_factory 仅用于测试；默认按 tts_provider() 实例化真实 provider
    （cosyvoice / cosyvoice_cloud 类见下方；lambda 延迟解析，定义顺序无关）。
    """
    provider = provider or tts_provider()
    factories = {
        "edge": edge or (lambda: EdgeTtsSpeaker(tts_voice())),
        "cosyvoice": cosyvoice or (lambda: CosyVoiceSpeaker()),
        "cosyvoice_cloud": cosyvoice_cloud or (lambda: CosyVoiceCloudSpeaker()),
    }
    chosen = factories.get(provider, factories["edge"])()
    if chosen.available():
        return chosen
    print(f"[yibao] TTS provider {provider} 不可用，回退 edge", file=sys.stderr)
    return factories["edge"]()


def build_voice(
    model_dir: str,
    vad_model: str,
    voice_name: str,
    min_silence: float = 1.2,
    max_seconds: int = 30,
) -> VoiceCapability:
    """生产装配：sherpa STT（懒加载）+ sounddevice 录 + 按 config 选 TTS provider 播。"""
    return VoiceCapability(
        LazySherpaRecognizer(model_dir),
        SounddeviceRecorder(vad_model=vad_model, max_seconds=max_seconds, min_silence=min_silence),
        build_speaker(),
    )
