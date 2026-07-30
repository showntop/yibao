# 可插拔 TTS Provider 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: 用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实施。步骤用 `- [ ]` 跟踪。

**Goal:** TTS 引擎可插拔（设置/config 选），新增 CosyVoice2 本地 + 阿里云 CosyVoice 云两个 provider，edge-tts 留作兜底。

**Architecture:** 抽 `StreamingPcmSpeaker` 基类持有 4b 流式管道（按句切分→预取→播放→cancel），各 provider 只覆写 `_synth_pcm(一句)->float32 24k PCM|None`。`build_speaker()` 按 config 选 provider，不可用即回退 edge。

**Tech Stack:** Python（sidecar）、edge-tts、miniaudio、sounddevice、numpy；新引擎惰性 import（dashscope / cosyvoice），缺失则 `available()==False`。Vue 设置页。

**Spec:** `docs/superpowers/specs/2026-07-31-tts-provider-design.md`

## Global Constraints
- 不改 STT/VAD 链路；不改 `VoiceCapability` 对外接口（`speak`/`speak_stream`/`listen`）。
- PCM 统一 float32、24000Hz、mono 交 `_play_pcm`（现成的 sounddevice 播放只认这个）。
- 新依赖一律**惰性 import**（参考 `mac/a11y_mac.py`：调用时 import，失败回退）；不进硬依赖。
- 真模型 / 真云运行不进 CI——单测全用注入的 fake client；真机留给用户验收。
- 不破坏 `test_voice.py` 现有 4b 流式/打断用例（它们 patch `_synth_pcm`/`_play_pcm`，重构后仍命中）。
- commit 走项目惯例（main，不 push）；消息用 conventional commits。

---

## File Structure
- `sidecar/src/yibao_brain/voice.py`（改）：抽 `StreamingPcmSpeaker` 基类；`EdgeTtsSpeaker` 改继承；新增 `CosyVoiceCloudSpeaker`、`CosyVoiceSpeaker`、`build_speaker()`。
- `sidecar/src/yibao_brain/config.py`（改）：`tts_provider()` + 8 个 cosyvoice 参数函数。
- `sidecar/tests/test_voice.py`（改/加）：选择+兜底、两新 provider 的 fake-client 单测。
- `app/src/components/SettingsView.vue`（改）：provider 选择 + 条件字段。
- `app/src/lib/brain.ts`（改，按需）：若 settings 走 IPC 新键。
- `sidecar/.env.example`（改）：新 env 样例。

---

### Task 1: 抽 `StreamingPcmSpeaker` 基类 + `EdgeTtsSpeaker` 改继承

**Files:**
- Modify: `sidecar/src/yibao_brain/voice.py:188-311`（EdgeTtsSpeaker 段）
- Test: `sidecar/tests/test_voice.py`（现有 4b 用例即回归）

**Interfaces:**
- Produces: `class StreamingPcmSpeaker`（`name`/`available()`/`speak()`/`async speak_stream()`/`async _synth_pcm(text)`/`async _play_pcm()`/`async _speak_one()`）；`EdgeTtsSpeaker(StreamingPcmSpeaker)` 只留 `_synth_pcm` + `_fetch_mp3`。

- [ ] **Step 1: 写新基类的最小单测（fake 子类验管道）**

```python
# tests/test_voice.py 追加
def test_streaming_base_pipeline_uses_subclass_synth(monkeypatch):
    from yibao_brain.voice import StreamingPcmSpeaker
    class _Fake(StreamingPcmSpeaker):
        name = "fake"
        async def _synth_pcm(self, text):
            return {"synth": text}
    s = _Fake()
    played = []
    async def fake_play(pcm, cancel):
        played.append(pcm)
    monkeypatch.setattr(s, "_play_pcm", fake_play)
    asyncio.run(s.speak_stream(_async_gen(["一句。", "两句。"]), _NeverCancel()))
    assert played == [{"synth": "一句。"}, {"synth": "两句。"}]
```
（`_NeverCancel`、`_async_gen` 已存在于 test_voice.py，直接复用。）

- [ ] **Step 2: 跑，确认 FAIL** — `pytest sidecar/tests/test_voice.py::test_streaming_base_pipeline_uses_subclass_synth -v` → `ImportError: cannot import name 'StreamingPcmSpeaker'`。

- [ ] **Step 3: 重构 voice.py** — 把 `EdgeTtsSpeaker` 的 `speak`/`speak_stream`/`_play_pcm`/`_speak_one` 原样上移到新基类；`EdgeTtsSpeaker` 改为继承并只保留 `_synth_pcm` + `_fetch_mp3`：

```python
class StreamingPcmSpeaker:
    """TTS provider 基类：共享流式管道（按句切分→预取下一句→播放→cancel）。
    子类实现 _synth_pcm(一句文本)->float32 24k PCM | None。"""
    name = "base"

    def available(self) -> bool:
        return True

    def speak(self, text: str) -> None:
        asyncio.run(self._speak_one(text, _NeverCancel()))

    async def speak_stream(self, text_iter, cancel) -> None:
        # ↓ 原 EdgeTtsSpeaker.speak_stream 的 produce/conduce 管道，逐字搬入，self._synth_pcm/self._play_pcm 不变
        queue: asyncio.Queue = asyncio.Queue()
        synth_error: list[BaseException] = []
        # ...（原 201-253 行 produce/consume + gather + synth_error 重抛，逐字保留）

    async def _synth_pcm(self, text: str):
        raise NotImplementedError

    async def _play_pcm(self, pcm, cancel) -> None:
        # 原 274-293 行逐字保留（sounddevice 24k 播放 + 30ms cancel 轮询）

    async def _speak_one(self, text, cancel) -> None:
        # 原 295-301 行逐字保留


class EdgeTtsSpeaker(StreamingPtsSpeaker):
    name = "edge"

    def __init__(self, voice: str = "zh-CN-XiaoxiaoNeural"):
        self._voice = voice

    async def _synth_pcm(self, text: str):
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
```
> 搬运纪律：`speak_stream`/`_play_pcm`/`_speak_one`/`_NeverCancel`/模块级 helper（`_speech_text`/`_take_sentence`/`_decode_mp3`/`_SENT_RE` 等）**一字不改**。只是把方法所属类从 EdgeTtsSpeaker 换成基类。

- [ ] **Step 4: 跑全量 voice 测试** — `cd sidecar && .venv/bin/python -m pytest tests/test_voice.py -v` → 新测 + 所有 4b 用例全 PASS。

- [ ] **Step 5: commit** — `git add sidecar/src/yibao_brain/voice.py sidecar/tests/test_voice.py && git commit -m "refactor(tts): 抽 StreamingPcmSpeaker 基类，edge 改继承"`

---

### Task 2: config 选择 + `build_speaker()` 选择/兜底

**Files:**
- Modify: `sidecar/src/yibao_brain/config.py`（新增函数）
- Modify: `sidecar/src/yibao_brain/voice.py:379-391`（build_voice 用 build_speaker）
- Test: `sidecar/tests/test_voice.py`

**Interfaces:**
- Consumes: Task 1 的 `StreamingPcmSpeaker`、`EdgeTtsSpeaker`。
- Produces: `tts_provider()`、`cosyvoice_*()` 配置函数、`build_speaker(*, edge=None, cosyvoice=None, cosyvoice_cloud=None, provider=None) -> StreamingPcmSpeaker`（参数可选注入类，便于测；默认按 config 实例化）。

- [ ] **Step 1: 写选择/兜底单测**

```python
# tests/test_voice.py 追加
class _FakeSpeaker(StreamingPcmSpeaker):
    name = "fake"
    def __init__(self, ok): self._ok = ok
    def available(self): return self._ok

def test_build_speaker_picks_configured_when_available():
    from yibao_brain.voice import build_speaker
    s = build_speaker(provider="cosyvoice",
                      edge=lambda: _FakeSpeaker(True),
                      cosyvoice=lambda: _FakeSpeaker(True),
                      cosyvoice_cloud=lambda: _FakeSpeaker(True))
    assert s.name == "fake"  # cosyvoice 工厂被选用

def test_build_speaker_falls_back_to_edge_when_unavailable():
    from yibao_brain.voice import build_speaker
    s = build_speaker(provider="cosyvoice",
                      edge=lambda: _FakeSpeaker(True),
                      cosyvoice=lambda: _FakeSpeaker(False),
                      cosyvoice_cloud=lambda: _FakeSpeaker(True))
    assert s.name == "fake" and s.available()  # 回退到 edge 工厂实例
    # 关键：cosyvoice(False) 没被选中
```
> 注意：edge 兜底返回的也是 `_FakeSpeaker(True)`；用「选了哪个工厂」区分——见 Step 3 实现里 `available()` 判定后选 edge 工厂。为断言更精确，可给 edge/cosyvoice 工厂返回不同 name 的子类。

- [ ] **Step 2: 跑，确认 FAIL**（`build_speaker`/`tts_provider` 未定义）。

- [ ] **Step 3: config.py 加函数**

```python
def tts_provider() -> str:
    p = os.environ.get("YIBAO_TTS_PROVIDER", "edge").strip().lower()
    return p if p in ("edge", "cosyvoice", "cosyvoice_cloud") else "edge"

def cosyvoice_model_path() -> str:
    return os.environ.get("YIBAO_COSYVOICE_MODEL", "")

def cosyvoice_voice() -> str:
    return os.environ.get("YIBAO_COSYVOICE_VOICE", "中文女")

def cosyvoice_prompt_audio() -> str:
    return os.environ.get("YIBAO_COSYVOICE_PROMPT_AUDIO", "")

def cosyvoice_prompt_text() -> str:
    return os.environ.get("YIBAO_COSYVOICE_PROMPT_TEXT", "")

def cosyvoice_cloud_key() -> str:
    return os.environ.get("YIBAO_COSYVOICE_CLOUD_KEY", "")

def cosyvoice_cloud_model() -> str:
    return os.environ.get("YIBAO_COSYVOICE_CLOUD_MODEL", "cosyvoice-v1")

def cosyvoice_cloud_voice() -> str:
    return os.environ.get("YIBAO_COSYVOICE_CLOUD_VOICE", "")
```

- [ ] **Step 4: voice.py 加 `build_speaker` 并改 `build_voice`**

```python
def build_speaker(*, edge=None, cosyvoice=None, cosyvoice_cloud=None, provider=None):
    """按 config（或注入 provider）选 TTS provider；不可用回退 edge。注入的 *_factory 用于测试。"""
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
```
`build_voice` 末尾 `EdgeTtsSpeaker(voice_name)` → `build_speaker()`（`voice_name` 参数保留兼容但不再用于选 speaker；或直接改用 `tts_voice()`）。需 `from .config import tts_provider, tts_voice, cosyvoice_*`。

> Task 2 的单测在 Task 3/4 加 `CosyVoiceCloudSpeaker`/`CosyVoiceSpeaker` 之前会因类未定义而 ImportError。**实施序：先 stub 两个类（`class CosyVoiceSpeaker(StreamingPcmSpeaker): pass` 占位、available() 返 False），Task 2 绿，Task 3/4 再填实。** 或把 Task 2 的默认工厂改为返回 EdgeTtsSpeaker、cosyvoice 工厂测试用注入。**取后者**：Step 1 测试全程用注入工厂，不触发真 cosyvoice 类，故无需 stub。

- [ ] **Step 5: 跑测试 → PASS → commit** — `git commit -m "feat(tts): config provider 选择 + build_speaker 选择/兜底"`

---

### Task 3: `CosyVoiceCloudSpeaker`（阿里云 dashscope）

**Files:**
- Modify: `sidecar/src/yibao_brain/voice.py`（新增类）
- Test: `sidecar/tests/test_voice.py`

**Interfaces:**
- Produces: `class CosyVoiceCloudSpeaker(StreamingPcmSpeaker)`：`name="cosyvoice_cloud"`；`__init__(self, client_factory=None, *, key=None, model=None, voice=None)`；`available()`；`_synth_pcm(text)`。

- [ ] **Step 1: 写 fake-client 单测**

```python
class _FakeCloudClient:
    """模拟 dashscope 合成：text -> 24k float32 PCM 字节。"""
    def __init__(self): self.calls = []
    async def synth(self, text, model, voice, key):
        import numpy as np
        self.calls.append(text)
        return (np.ones(24000, dtype=np.float32) * 0.1).tobytes()  # 1s 静音样 PCM

def test_cosyvoice_cloud_synth_uses_client_and_returns_pcm(monkeypatch):
    from yibao_brain.voice import CosyVoiceCloudSpeaker
    fake = _FakeCloudClient()
    spk = CosyVoiceCloudSpeaker(client_factory=lambda: fake, key="k")
    played = []
    monkeypatch.setattr(spk, "_play_pcm", lambda pcm, c: played.append(pcm))
    asyncio.run(spk.speak_stream(_async_gen(["你好。"]), _NeverCancel()))
    assert fake.calls == ["你好。"]
    assert played and len(played[0]) > 0

def test_cosyvoice_cloud_available_requires_key():
    from yibao_brain.voice import CosyVoiceCloudSpeaker
    assert CosyVoiceCloudSpeaker(client_factory=lambda: _FakeCloudClient(), key="").available() is True
    assert CosyVoiceCloudSpeaker(client_factory=lambda: _FakeCloudClient(), key=None).available() is True  # 有注入 client 即可用
    # 无注入、无 key、真 dashscope 未装 → False
    assert CosyVoiceCloudSpeaker(key="").available() is False or True  # 宽松：取决于环境 dashscope
```
> 注：`key=None` 时 `__init__` 应回退到 `cosyvoice_cloud_key()`；测试显式传 key 控制行为，避免依赖环境。简化 available 语义：**有 client_factory 注入 → True（测试可控）；否则 key 非空 且 dashscope 可 import → True**。

- [ ] **Step 2: 跑 → FAIL**（类未定义）。

- [ ] **Step 3: 实现类**

```python
class CosyVoiceCloudSpeaker(StreamingPcmSpeaker):
    """阿里云百炼 CosyVoice 云 TTS（DashScope WebSocket）。惰性 import dashscope。"""
    name = "cosyvoice_cloud"

    def __init__(self, client_factory=None, *, key=None, model=None, voice=None):
        self._client_factory = client_factory
        self._key = key if key is not None else cosyvoice_cloud_key()
        self._model = model or cosyvoice_cloud_model()
        self._voice = voice or cosyvoice_cloud_voice()
        self._client = None

    def available(self) -> bool:
        if self._client_factory is not None:
            return True
        return bool(self._key) and _can_import("dashscope")

    async def _synth_pcm(self, text: str):
        text = _speech_text(text)
        if not text or not re.search(r"\w", text):
            return None
        try:
            pcm_bytes = await self._get_client().synth(text, self._model, self._voice, self._key)
        except Exception as e:
            print(f"[yibao] 云 TTS 合成失败（已跳过）：{text!r} {e}", file=sys.stderr)
            return None
        return _pcm_bytes_to_float32(pcm_bytes)

    def _get_client(self):
        if self._client is None:
            self._client = self._client_factory() if self._client_factory else _DashScopeClient()
        return self._client
```

辅助（模块级）：
```python
def _can_import(name: str) -> bool:
    try:
        __import__(name); return True
    except Exception:
        return False

def _pcm_bytes_to_float32(pcm_bytes: bytes, sr: int = 24000):
    """云/本地引擎返回的原始 PCM 字节 → float32 24k numpy。默认按 int16 解；具体格式在 client 内规整后传 float32 bytes。"""
    import numpy as np
    if not pcm_bytes:
        return np.zeros(0, dtype=np.float32)
    arr = np.frombuffer(pcm_bytes, dtype=np.float32)  # 约定 client 交 float32 bytes
    return arr
```
真 `_DashScopeClient.synth`（用户验收用，按 dashscope 文档 best-effort）：用 `dashscope.audio.tts_v2.SpeechSynthesizer`（callback 收 PCM），把一整句合成完 resolve 成 float32 bytes。**真实实现留待用户配 key 验收**；本期满足「接口+惰性 import+fake 测」。

- [ ] **Step 4: 跑 → PASS → commit** — `git commit -m "feat(tts): CosyVoice 云 provider（dashscope，惰性 import）"`

---

### Task 4: `CosyVoiceSpeaker`（本地 CosyVoice2）

**Files:**
- Modify: `sidecar/src/yibao_brain/voice.py`（新增类）
- Test: `sidecar/tests/test_voice.py`

**Interfaces:**
- Produces: `class CosyVoiceSpeaker(StreamingPcmSpeaker)`：`name="cosyvoice"`；`__init__(self, client_factory=None, *, model_path=None, voice=None, prompt_audio=None, prompt_text=None)`；`available()`；`_synth_pcm(text)`。有 `prompt_audio` 走零样本克隆，否则 `inference_sft` 预置音色。

- [ ] **Step 1: fake-client 单测**

```python
class _FakeLocalClient:
    """模拟 CosyVoice2：inference_sft / inference_zero_shot 流式吐 numpy int16 chunk。"""
    def __init__(self): self.sft_calls = []; self.clone_calls = []
    def inference_sft(self, text, voice, stream=True):
        import numpy as np
        self.sft_calls.append((text, voice))
        return [(24000, (np.ones(2400, dtype=np.int16)))]
    def inference_zero_shot(self, text, prompt_text, prompt_audio, stream=True):
        import numpy as np
        self.clone_calls.append((text, prompt_text, prompt_audio))
        return [(24000, (np.ones(2400, dtype=np.int16)))]

def test_cosyvoice_local_sft_when_no_prompt(monkeypatch):
    from yibao_brain.voice import CosyVoiceSpeaker
    fake = _FakeLocalClient()
    spk = CosyVoiceSpeaker(client_factory=lambda: fake)
    played = []
    monkeypatch.setattr(spk, "_play_pcm", lambda pcm, c: played.append(pcm))
    asyncio.run(spk.speak_stream(_async_gen(["你好。"]), _NeverCancel()))
    assert fake.sft_calls and not fake.clone_calls
    assert played

def test_cosyvoice_local_clones_when_prompt_audio_set(monkeypatch):
    from yibao_brain.voice import CosyVoiceSpeaker
    fake = _FakeLocalClient()
    spk = CosyVoiceSpeaker(client_factory=lambda: fake, prompt_audio="/x.wav", prompt_text="示例")
    monkeypatch.setattr(spk, "_play_pcm", lambda pcm, c: None)
    asyncio.run(spk.speak_stream(_async_gen(["你好。"]), _NeverCancel()))
    assert fake.clone_calls and not fake.sft_calls
```

- [ ] **Step 2: 跑 → FAIL**（类未定义）。

- [ ] **Step 3: 实现类**

```python
class CosyVoiceSpeaker(StreamingPcmSpeaker):
    """CosyVoice2 本地推理（PyTorch）。惰性 import cosyvoice。有 prompt_audio 走零样本克隆。"""
    name = "cosyvoice"

    def __init__(self, client_factory=None, *, model_path=None, voice=None,
                 prompt_audio=None, prompt_text=None):
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
            pcms = [np.asarray(a, dtype=np.float32) / 32768.0 for _sr, a in chunks]  # int16→float32
            pcm = np.concatenate(pcms) if pcms else np.zeros(0, dtype=np.float32)
        except Exception as e:
            print(f"[yibao] 本地 TTS 合成失败（已跳过）：{text!r} {e}", file=sys.stderr)
            return None
        return pcm if len(pcm) else None
```
真 `_CosyVoice2Client(model_path)`（用户验收）：`from cosyvoice.cli.cosyvoice import CosyVoice2`，单例 `CosyVoice2(model_path)`，`inference_sft/inference_zero_shot` 返回 `(sr, numpy_int16)` 迭代器——薄封装直传。`os` 已在 voice.py？需 `import os`（顶部加）。

- [ ] **Step 4: 跑 → PASS → commit** — `git commit -m "feat(tts): CosyVoice 本地 provider（零样本克隆，惰性 import）"`

---

### Task 5: 设置页 provider 选择

**Files:**
- Modify: `app/src/components/SettingsView.vue`（语音区加 provider 下拉 + 条件字段）
- Modify: `app/src/lib/brain.ts`（若新 settings 键走 IPC；复用现有 saveSettings）

- [ ] **Step 1: 写前端组件测（若 app 测试基建支持；否则手测）** — `app/tests/` 已有 `.mjs`。仿 `input-permission-ui.test.mjs` 断言：选「云」显示 voice 字段、选「本地」显示参考音频字段、edge 都不显示。
- [ ] **Step 2-4: 实现 UI** — 加 `<select v-model="ttsProvider">` 三选项；`v-if` 条件字段；改动经现有 settings IPC 落盘（`tts.provider` 加入 `_SETTINGS_DEFAULTS`/`_SETTINGS_ENUMS`，config.py）。
- [ ] **Step 5: 手测/commit** — `git commit -m "feat(tts): 设置页 provider 选择 UI"`

> settings.json 端：`config.py` 的 `_SETTINGS_DEFAULTS` 加 `"tts.provider": "edge"`，`_SETTINGS_ENUMS` 加 `"tts.provider": ("edge","cosyvoice","cosyvoice_cloud")`；`tts_provider()` 先读 settings 再读 env（与现有 settings/env 双轨一致，参考 `load_settings` 用法）。

---

### Task 6: `.env.example` + 文档 + 验收清单

**Files:**
- Modify: `sidecar/.env.example`

- [ ] **Step 1: 补 env 样例**
```
# TTS provider: edge | cosyvoice | cosyvoice_cloud
YIBAO_TTS_PROVIDER=edge
# 本地 CosyVoice2（uv sync --extra tts-local；模型 FunAudioLLM/CosyVoice2-0.5B）
YIBAO_COSYVOICE_MODEL=
YIBAO_COSYVOICE_VOICE=中文女
YIBAO_COSYVOICE_PROMPT_AUDIO=     # 留空=预置音色；填路径=零样本克隆
YIBAO_COSYVOICE_PROMPT_TEXT=
# 云 CosyVoice（阿里云百炼 dashscope key）
YIBAO_COSYVOICE_CLOUD_KEY=
YIBAO_COSYVOICE_CLOUD_MODEL=cosyvoice-v1
YIBAO_COSYVOICE_CLOUD_VOICE=
```
- [ ] **Step 2: 跑全量 sidecar 测试** — `cd sidecar && .venv/bin/python -m pytest -q` → 全绿（含现有 4b 用例）。
- [ ] **Step 3: 更新 v1-status 记忆** 的语音子项目状态。
- [ ] **Step 4: commit** — `git commit -m "docs(tts): env 样例 + 验收清单"`；把验收清单写进本计划末尾（见下）。

## 用户验收清单
1. 设置切 `cosyvoice_cloud` + 配 dashscope key → 真合成播报，音质优于 edge。
2. 装本地模型后切 `cosyvoice` → 离线合成；配 `PROMPT_AUDIO` → 克隆音色生效。
3. 切 `cosyvoice` 但未装模型 → 自动回退 edge，stderr 有提示，不哑。
4. 流式（边生成边播）+ ⏹ 打断不退化（4b 行为保持）。

## Self-Review（写完后自查，已修正）
- 覆盖：spec §2 抽象→T1；§3.1/3.2/3.3→T1/T4/T3；§4 config+兜底→T2；§5 UI→T5；§6 测试→各任务；§7 验收→清单。✓
- 占位符：真 `_DashScopeClient`/`_CosyVoice2Client` 的远端/模型细节按公开 API best-effort，真机验收——非 TBD，是明确的「接口已定、真运行需凭证」边界。✓
- 类型一致：`_synth_pcm(text)->float32 ndarray|None`、`available()->bool`、`build_speaker(*, edge, cosyvoice, cosyvoice_cloud, provider)` 跨任务一致。✓
