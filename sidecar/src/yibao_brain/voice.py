"""语音能力（Plan 4a 最小版 + Plan 4b 流式/打断 + Plan 5 体验修复）：录音+VAD→STT，TTS→播放。

speak_stream（5）：按句切分 → 生产者/消费者管道 —— 播当前句的同时预合成下一句
（旧实现句间串行：合成整句→播完→再合成下一句，句间有完整网络延迟，听着一顿一顿）。
cancel 命中即停（"三连取消"之一：停 TTS）。
组件 factory 注入：真实现用 sherpa-onnx/sounddevice/edge-tts/miniaudio；
测试用 tests/fakes.py 的 Fake*。
"""
from __future__ import annotations

from .log import log
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
                    log("首次语音输入，加载 STT 模型…")
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
        log("录音开始")
        with sd.InputStream(
            channels=1, dtype="float32", samplerate=SR, blocksize=int(0.1 * SR), callback=_on_audio
        ):
            while total < SR * self._max and not self._stop.is_set():
                try:
                    samples = q.get(timeout=0.1)  # 带超时取：stop 信号 100ms 内必被看到
                except queue.Empty:
                    if stats["frames"] == 0 and time.monotonic() - t0 > self._no_frame_timeout:
                        log("麦克风一直无音频帧（被占用/掉线？），放弃录音")
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
                    log("VAD 检测到语音")
                while not vad.empty():
                    # VAD 切出一段完整语音（说完一句）→ 返回
                    seg = np.array(vad.front.samples, dtype=np.float32)
                    vad.pop()
                    log(f"识别到完整语句（{time.monotonic() - t0:.1f}s），结束录音")
                    return seg
        why = "被打断" if self._stop.is_set() else "超时/无语音"
        log(f"录音结束：{why}（{time.monotonic() - t0:.1f}s，{stats['frames']} 帧）")
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

    async def play(self, pcm, cancel, *, fade_in: bool = True, fade_out: bool = True) -> None:
        """塞一段 float32 24k PCM，等它播完返回。句首尾淡入淡出防数字层 click。

        fade_in/fade_out 供流式分段播放关掉段间衰减：句首段淡入、句尾段淡出、
        句中段原样直通（每段都淡入淡出会叠出一串 30ms 音量坑，听着扑扑响）。
        """
        import numpy as np
        import sounddevice as sd

        _silence_sd_warnings()
        pcm = np.asarray(pcm, dtype=np.float32)
        if pcm.size == 0:
            return
        pcm = _fade_edges(pcm, self._sr, fade_sec=0.015, fade_in=fade_in, fade_out=fade_out)

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

    子类实现 _synth_pcm(一句文本) -> float32 24k PCM | None；
    需要边收边播的（edge）覆盖 _synth_pcm_stream(一句文本) -> PCM 片段流。
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
        queue: asyncio.Queue = asyncio.Queue()  # (pcm, fade_in, fade_out) 片段队列；None=结束哨兵
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
                        await self._produce_sentence(sentence, queue, cancel)
                tail = buf.strip()
                if tail and not cancel.is_set():
                    await self._produce_sentence(tail, queue, cancel)
            except asyncio.CancelledError:
                raise  # 正常取消（打断命中合成中），不是合成错误，必须向上传
            except BaseException as e:
                synth_error.append(e)
            finally:
                await queue.put(None)

        async def consume() -> None:
            while True:
                item = await queue.get()
                if item is None or cancel.is_set():
                    return
                pcm, fade_in, fade_out = item
                await self._play_pcm(pcm, cancel, fade_in=fade_in, fade_out=fade_out)

        producer = asyncio.create_task(produce())
        try:
            await consume()
        finally:
            if not producer.done():
                producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)
        if synth_error:
            raise synth_error[0]

    async def _produce_sentence(self, sentence: str, queue: asyncio.Queue, cancel) -> None:
        """一句文本 → 若干 PCM 片段入队：(pcm, fade_in, fade_out)。

        流式合成（edge 边收边解）句内逐段产出：只在句首段淡入、句尾段淡出，
        段间不衰减——每段都淡入淡出会叠出一串 30ms 音量坑（扑扑声）。
        句尾标记靠一段前瞻（hold 住上一段、下一段到了再发），代价约一个解码段时长。
        """
        prev = None
        first = True
        async for pcm in self._synth_pcm_stream(sentence):
            if cancel.is_set():
                return
            if prev is not None:
                await queue.put((prev, first, False))
                first = False
            prev = pcm
        if prev is not None:
            await queue.put((prev, first, True))

    async def _synth_pcm_stream(self, text: str):
        """一句文本 → PCM 片段流。默认整句一次性产出；edge 覆盖为边收边解逐段产出。"""
        pcm = await self._synth_pcm(text)
        if pcm is not None:
            yield pcm

    async def _synth_pcm(self, text: str):
        raise NotImplementedError

    async def _play_pcm(self, pcm, cancel, *, fade_in: bool = True, fade_out: bool = True) -> None:
        """播放一段 PCM：走常驻单流（_PersistentPlayer），句间不关流。

        - 首尾淡入淡出：消除数字层开关流 click；流式分段时由 fade_in/fade_out
          关掉段间衰减（只留句首淡入、句尾淡出）；
        - 打断：对残留快速淡出后静音待机，流保持打开（无设备层 pop）。
        """
        await self._player.play(pcm, cancel, fade_in=fade_in, fade_out=fade_out)

    async def _speak_one(self, text: str, cancel) -> None:
        if cancel.is_set():
            return
        pcm = await self._synth_pcm(text)
        if pcm is None or cancel.is_set():
            return
        await self._play_pcm(pcm, cancel)


class EdgeTtsSpeaker(StreamingPcmSpeaker):
    """edge-tts 合成 → miniaudio 流式解码 → sounddevice 播放。

    边收边播（延迟优化）：edge-tts 流式吐 mp3 chunk，旧实现攒满整段才解码播放
    （起音 = 整段收完 1.22s + 解码 0.13s ≈ 1.35s）；现经字节管道边收边解边播
    （dr_mp3 流式读、不挑字节边界），首 chunk 到达即起播。
    连接复用（延迟优化）：同音色连续句子共享一个 keep-alive 连接池，
    省掉每句 ~0.5s 的 TCP+TLS+WS 建连税；池失效自动丢池重建重试一次。
    """

    name = "edge"

    def __init__(self, voice: str = "zh-CN-XiaoxiaoNeural"):
        super().__init__()
        self._voice = voice
        self._connector = None       # 共享连接池（_pooled_connector_class() 实例，惰性建）
        self._connector_loop = None  # 池所属事件循环（跨循环必须重建）

    async def _synth_pcm(self, text: str):
        """整句一次性合成（speak 同步路径）：复用流式管道，收齐片段拼接。"""
        import numpy as np

        pieces = [np.asarray(p, dtype=np.float32) async for p in self._synth_pcm_stream(text)]
        if not pieces:
            return None
        pcm = np.concatenate(pieces)
        return pcm if len(pcm) else None

    async def _synth_pcm_stream(self, text: str):
        """一句文本 → PCM 片段流（边收边解边产）。

        跳过该句、不杀整段播报的情况（同旧 _synth_pcm 约定）：
        - 无可播内容（空串 / 纯标点——edge-tts 对「？」这类会 NoAudioReceived），不发请求
        - 单句合成失败（记 stderr 留痕）
        未产出任何音频就失败 → 疑似连接池失效：丢池重建重试一次（重连兜底）；
        已产出片段后失败不重试（重试会把已播片段再念一遍）。
        """
        text = _speech_text(text)
        if not text or not re.search(r"\w", text):
            return
        for attempt in (0, 1):
            produced = False
            try:
                async for pcm in self._stream_sentence_pcm(text):
                    if len(pcm):
                        produced = True
                        yield pcm
                return
            except asyncio.CancelledError:
                raise  # 正常取消（打断命中合成中），必须向上传
            except Exception as e:
                if _is_no_audio_error(e):
                    log(f"句子无可播音频（已跳过）：{text!r}")
                    return
                if not produced and attempt == 0:
                    log(f"合成失败，重建连接重试：{text!r} {e}")
                    self._reset_connector()
                    continue
                log(f"句子合成失败（已跳过）：{text!r} {e}")
                return

    async def _stream_sentence_pcm(self, text: str):
        """edge-tts 边收边解：抓取协程把 mp3 chunk 喂进字节管道，
        解码线程（miniaudio stream_any，dr_mp3 按帧流式解）把 PCM 片段推进 asyncio 队列。

        打断/收尾：管道 close 让解码线程读到 EOF 退出，抓取任务取消；
        抓取失败在解码排空后透传异常（触发上层重连/跳过语义）。
        """
        loop = asyncio.get_running_loop()
        src = _Mp3PipeSource()
        pcm_q: asyncio.Queue = asyncio.Queue()
        self._start_decoder(src, pcm_q, loop)
        fetch = asyncio.create_task(self._feed_mp3(text, src))
        try:
            while True:
                item = await pcm_q.get()
                if item is None:
                    break
                if isinstance(item, BaseException):
                    # 解码失败时优先透传抓取异常（NoAudioReceived 等语义给上层判别）
                    if fetch.done() and not fetch.cancelled() and fetch.exception() is not None:
                        raise fetch.exception()
                    raise item
                yield item
        finally:
            src.close()  # 打断：解码线程不再干等字节，当 EOF 收尾
            if not fetch.done():
                fetch.cancel()
            await asyncio.gather(fetch, return_exceptions=True)
        if not fetch.cancelled() and fetch.exception() is not None:
            raise fetch.exception()

    def _start_decoder(self, src: "_Mp3PipeSource", pcm_q: asyncio.Queue, loop) -> None:
        """起解码线程（独立成方法便于测试替换）。"""
        threading.Thread(target=_decode_mp3_pipe, args=(src, pcm_q, loop), daemon=True).start()

    async def _feed_mp3(self, text: str, src: "_Mp3PipeSource") -> None:
        """抓 mp3 chunk 喂管道（独立任务：与解码/播放并行）；收尾必喂 EOF 放解码线程出来。"""
        try:
            async for chunk in self._fetch_mp3_chunks(text):
                src.feed(chunk)
        finally:
            src.feed_eof()

    async def _fetch_mp3_chunks(self, text: str):
        """edge-tts 流式抓 mp3：逐 audio chunk yield。连接走共享池（见 _get_connector）。"""
        import edge_tts

        com = edge_tts.Communicate(text, self._voice, connector=self._get_connector())
        async for piece in com.stream():
            if piece["type"] == "audio":
                yield piece["data"]

    def _get_connector(self):
        """共享 TLS 连接池（惰性建，随事件循环走）：edge-tts 每句新建的 ClientSession
        从池里拿 ws 关闭后回收的 keep-alive 连接，省掉每句 ~0.5s TCP+TLS 建连税。"""
        loop = asyncio.get_running_loop()
        c = self._connector
        if c is None or c.closed or self._connector_loop is not loop:
            self._connector = _pooled_connector_class()(limit=4, keepalive_timeout=60, ttl_dns_cache=300)
            self._connector_loop = loop
        return self._connector

    def _reset_connector(self) -> None:
        """连接池失效兜底：丢弃旧池（连接被服务端掐/网络抖动），下次合成重建。

        旧池不显式 close：它可能正被进行中的请求引用；闲置连接由 keepalive_timeout 自然回收。
        """
        self._connector = None


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


class _Mp3PipeSource:
    """边收边解的 mp3 字节管道：feed 喂 chunk、read 阻塞等字节（miniaudio 流式解码源协议）。

    实现 miniaudio.StreamableSource 的鸭子协议（read/seek/close + error_in_readcallback），
    但不继承它——继承要模块级 import miniaudio，违背惰性加载（大脑启动提速）。
    dr_mp3 按帧扫描流式解码，任意字节边界喂入都能解（实测：分 chunk 喂与整段喂输出逐位一致）。
    """

    error_in_readcallback = None  # miniaudio 读回调协议字段（本实现 read 不抛错，恒 None）

    def __init__(self):
        import queue

        self._q: queue.Queue = queue.Queue()  # bytes 数据 / None=EOF
        self._buf = bytearray()
        self._eof = False
        self.ffi_handle = None  # miniaudio stream_any 会回填

    def feed(self, data: bytes) -> None:
        self._q.put(data)

    def feed_eof(self) -> None:
        self._q.put(None)

    def close(self) -> None:
        """打断/收尾：read 不再干等，按 EOF 处理（幂等）。"""
        self._eof = True
        self._q.put(None)

    def seek(self, offset: int, origin) -> bool:
        return False  # 网络流不可 seek（已给定 MP3 格式，解码器不需要 seek）

    def read(self, num_bytes: int) -> bytes:
        """有多少给多少（至少等 1 字节），不攒满 num_bytes——dr_mp3 一次要 64KB，
        攒满才返回等于把整段 mp3 等完，边收边播就废了。短读对解码器合法（非 EOF），
        它会继续调 read 要后续字节；返回 b"" 才是 EOF。"""
        while not self._eof and not self._buf:
            item = self._q.get()
            if item is None:
                self._eof = True
                break
            self._buf += item
        out = bytes(self._buf[:num_bytes])
        del self._buf[:num_bytes]
        return out


def _decode_mp3_pipe(src: _Mp3PipeSource, pcm_q: asyncio.Queue, loop: asyncio.AbstractEventLoop) -> None:
    """解码线程入口：miniaudio 流式解 mp3 管道 → float32 24k PCM 片段进 asyncio 队列。

    None 哨兵收尾；解码异常也进队列（消费侧转为跳过该句）。
    frames_to_read=2048（~85ms/段）：是播放器 1024 帧回调块的整数倍，段间不错位。
    """
    import miniaudio
    import numpy as np

    try:
        stream = miniaudio.stream_any(
            src,
            source_format=miniaudio.FileFormat.MP3,
            output_format=miniaudio.SampleFormat.FLOAT32,
            nchannels=1,
            sample_rate=24000,
            frames_to_read=2048,
        )
        for samples in stream:
            # copy：frombuffer 引用的是 miniaudio 的解码缓冲，下一段会被覆写
            pcm = np.frombuffer(samples, dtype=np.float32).copy()
            loop.call_soon_threadsafe(pcm_q.put_nowait, pcm)
    except Exception as e:
        loop.call_soon_threadsafe(pcm_q.put_nowait, e)
    finally:
        loop.call_soon_threadsafe(pcm_q.put_nowait, None)


def _is_no_audio_error(e: BaseException) -> bool:
    """edge-tts NoAudioReceived 判别（惰性 import：edge-tts 未装时不炸）。"""
    try:
        from edge_tts.exceptions import NoAudioReceived
    except Exception:
        return False
    return isinstance(e, NoAudioReceived)


_pooled_connector_cls = None


def _pooled_connector_class():
    """惰性构造 aiohttp.TCPConnector 子类（类定义依赖 aiohttp，模块级 import 太贵）。

    子类把 close 做成空操作：edge-tts 每句新建 ClientSession，session 关闭会连带
    connector.close()（connector_owner 默认 True）——不拦住的话池每句都被真关、
    复用落空。ws 关闭后底层 TCP+TLS 连接回池，下一句省 ~0.5s 建连税（实测 0.8s→0.4s）。
    真关闭走 force_close（测试清理用；生产上池随 speaker 活到进程退出）。
    """
    global _pooled_connector_cls
    if _pooled_connector_cls is None:
        import aiohttp

        class _PooledConnector(aiohttp.TCPConnector):
            def close(self, *, abort_ssl: bool = False):
                async def _noop() -> None:
                    pass

                return _noop()  # 不关：保住 keep-alive 池（session 关闭时被连带调用）

            async def force_close(self) -> None:
                await super().close()

        _pooled_connector_cls = _PooledConnector
    return _pooled_connector_cls


def _fade_edges(pcm, sr: int, fade_sec: float = 0.015, fade_in: bool = True, fade_out: bool = True):
    """首尾各 fade_sec 线性淡入淡出（零交叉），返回新数组：开关流不产生 click。

    fade_in/fade_out 可分别关：流式合成的句内分段只在句首淡入、句尾淡出，
    段间不衰减（防每段一对 15ms 衰减叠出 30ms 音量坑）。
    音频过短时 fade 长度按样本数折半，首尾区间不相交。
    """
    import numpy as np

    n = pcm.size
    if n == 0:
        return pcm
    if not fade_in and not fade_out:
        return pcm  # 句中段：原样直通，不复制
    half = max(1, n // 2)
    fade = max(1, min(int(sr * fade_sec), half))
    out = np.array(pcm, dtype=np.float32, copy=True)
    if fade_in:
        out[:fade] *= np.linspace(0.0, 1.0, fade, dtype=np.float32)
    if fade_out:
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
            log(f"云 TTS 合成失败（已跳过）：{text!r} {e}")
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
            log(f"本地 TTS 合成失败（已跳过）：{text!r} {e}")
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
    log(f"TTS provider {provider} 不可用，回退 edge")
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
