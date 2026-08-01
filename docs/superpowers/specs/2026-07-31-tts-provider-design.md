# 可插拔 TTS Provider（CosyVoice2 本地 + 云 + edge-tts 兜底）设计

日期：2026-07-31  关联：[[v1-status]]（语音子项目，Plan 4a/4b 之后）

## 1. 目标 / 非目标

**目标**
- TTS 引擎**可插拔、可在设置里选**：同一接口下挂多个 provider，config 选激活项。
- 本期交付三个 provider：
  1. `edge` — 现有 edge-tts（zh-CN-XiaoxiaoNeural），**保留为兜底**。
  2. `cosyvoice` — CosyVoice2 **本地**推理（真离线、可零样本声音克隆=专属音色）。
  3. `cosyvoice_cloud` — 阿里云百炼 CosyVoice **云 API**（DashScope WebSocket）。
- 流式 / 按句预取 / 可打断管道**全 provider 复用**（沿用 4b 的 `speak_stream`）。
- 选中 provider 不可用（模型未装 / 无 key）时**自动回退 edge**，永不哑。

**非目标**
- 不降 TTFT：首字延迟瓶颈在 LLM（DeepSeek ~1s+RTT），TTS 管道开销≈0；本项只换合成引擎。
- 不改 STT（sherpa-onnx Paraformer）/ VAD（Silero）链路。
- 不做声音克隆的训练/微调，只用零样本（本地）或声音复刻（云）。

## 2. 架构：TTSProvider 抽象

现有 `EdgeTtsSpeaker` 已是事实 provider（`speak` + `async speak_stream`），`VoiceCapability` 只调这两个方法。把它形式化为 Protocol，各引擎实现同接口：

```python
# voice.py
class TTSProvider(Protocol):
    name: str
    def available(self) -> bool: ...                 # 能否在此环境运行（模型/key 在不在）
    def speak(self, text: str) -> None: ...          # 阻塞整段合成+播放（非流式路径）
    async def speak_stream(self, text_iter: AsyncIterator[str], cancel) -> None: ...
```

- `EdgeTtsSpeaker`、`CosyVoiceSpeaker`、`CosyVoiceCloudSpeaker` 三实现。
- `VoiceCapability.__init__(recognizer, recorder, speaker)` 不变（speaker 即任一 provider）。

## 3. Provider 细节

### 3.1 edge（现有，重构）
- 改实现 `TTSProvider`；`available()` 恒 True（云始终可用前提）；逻辑不动。
- 作为兜底：其它 provider `available()==False` 或运行期抛错时用它。

### 3.2 cosyvoice（本地，新）
- **依赖**：官方 CosyVoice2 repo（PyTorch）+ 模型 `FunAudioLLM/CosyVoice2-0.5B`。**惰性 import**（同 `mac/a11y` 模式：仅调用时 import torch/cosyvoice，缺失则 `available()` 返 False）。可选装 `uv sync --extra tts-local`。
- **加载**：`from cosyvoice.cli.cosyvoice import CosyVoice2`；`CosyVoice2(model_path)` 单例（重，进程内缓存）。
- **合成**：
  - 有参考音频（专属音色）→ `inference_zero_shot(text, prompt_text, prompt_audio_16k, stream=True)`。
  - 无参考 → `inference_sft(text, voice, stream=True)`（用预置音色）。
  - 迭代产出 `(sample_rate=24000, numpy_pcm_int16)`。
- **播放**：PCM 直喂 `miniaudio`/`sounddevice`（24k，float32），**无需 mp3 解码**（比 edge 少一步）；按句切分 + 预取下一句的管道与 edge 同构。
- **available()**：`import cosyvoice` 成功 且 `model_path` 存在。
- **风险**：macOS arm64（CPU/MPS）能否跑、首包/吞吐可不可接受**未验证** → 实施第 0 步可行性 spike；不达标则该 provider 标 unavailable、自动回退，不阻塞交付。

### 3.3 cosyvoice_cloud（云，新）
- **依赖**：`dashscope` Python SDK（惰性 import）；需阿里云百炼 API key。
- **协议**：DashScope 实时 TTS WebSocket；CosyVoice 与 Qwen-Audio-TTS 同协议，换 `model`/`voice` 即切。默认 `model=cosyvoice-v1`，`voice` 可配。
- **合成**：SDK 流式回调吐 PCM（24k）→ 同 edge 的播放管道。
- **单次文本上限 2000 字符** → `speak_stream` 按句切分本就粒度更细，溢出处再二次切。
- **available()**：`import dashscope` 成功 且 `cloud_key` 非空。
- **声音复刻（专属音色，云版）**：可选——用户在百炼控制台复刻一个 voice id，配进 `voice` 字段即可（本期不做自动复刻流程，只消费 voice id）。

## 4. 配置 / 选择 / 兜底

新增 `config.py`（沿用 `_env` / settings 双轨）：
- `tts_provider()` → `edge | cosyvoice | cosyvoice_cloud`，默认 `edge`。
- 本地：`cosyvoice_model_path()`、`cosyvoice_voice()`（预置音色）、`cosyvoice_prompt_audio()`（克隆参考音频路径）、`cosyvoice_prompt_text()`（参考音频台词）。
- 云：`cosyvoice_cloud_key()`、`cosyvoice_cloud_model()`（默认 `cosyvoice-v1`）、`cosyvoice_cloud_voice()`。

`build_voice(...)` 选择逻辑：
```
provider = tts_provider()
speaker = {
  "cosyvoice": CosyVoiceSpeaker, "cosyvoice_cloud": CosyVoiceCloudSpeaker, "edge": EdgeTtsSpeaker
}[provider]()
if not speaker.available():
    print(stderr, f"[yibao] TTS {provider} 不可用，回退 edge")
    speaker = EdgeTtsSpeaker()
```
运行期 provider 抛错（如云断连）：**首包前**失败 → 回退 edge 重合成本次（best-effort，记 stderr）；**已开播后**失败 → 记 stderr 并停（无法收回已播音频），与现有错误处理一致。

## 5. 设置 UI

`SettingsView.vue` 现有权限区下方加「语音」区：
- provider 下拉（edge / 本地 CosyVoice2 / 云 CosyVoice）。
- 条件字段：本地→参考音频路径（留空=预置音色）；云→voice（留空=默认）。
- key/model 等敏感或高级项走 `.env`，不进 UI。
- 改动落 settings.json + 通知 sidecar 重载（复用现有 settings IPC）。

## 6. 测试

- `test_voice.py`：
  - `TTSProvider` 选择 + 兜底：各 provider `available()` 真/假组合 → 选中或回退 edge（用 fake provider 注入，不触真模型/网）。
  - `CosyVoiceSpeaker` / `CosyVoiceCloudSpeaker`：client_factory 注入 fake，断言流式 PCM→播放管道、按句切分、cancel 命中即停（复用 4b 的 fake 范式）。
  - 单次 >2000 字截断（云）。
- `EdgeTtsSpeaker` 重构后现有 4b 用例全绿（回归）。
- **真模型 / 真云运行不在 CI**：留作用户验收（本地装模型、云配 key）。

## 7. 风险 / 验收

| 风险 | 处置 |
|---|---|
| 本地 CosyVoice2 在 mac arm64 跑不动/太慢 | 第 0 步 spike；不达标则 provider 标 unavailable、自动回退 edge，交付不阻塞 |
| dashscope 依赖 / 阿里 key | 惰性 import + `available()` 守卫；无 key→回退 |
| CosyVoice2 API 随版本变动 | 惰性 import + 运行期 try/except，失败回退 edge |
| PCM 采样率/格式不匹配 | 统一在 provider 内规整到 24k float32 再交管道 |

**验收（用户）**：① 设置切到云 → 真合成播报；② 本地装好模型后切本地 → 离线合成 + 参考音频克隆生效；③ 切不存在的 provider → 自动回退 edge 不哑；④ 流式/打断不退化。

## 8. 实施顺序（writing-plans 细化）
1. 抽 `TTSProvider` + 重构 `EdgeTtsSpeaker` 实现 it（回归绿）。
2. config（provider 选择 + 三组参数）+ `build_voice` 选择/兜底 + 单测。
3. `CosyVoiceCloudSpeaker`（dashscope，可全程 mock 测）。
4. `CosyVoiceSpeaker`（本地，惰性 import + fake client 测；真机 spike）。
5. 设置 UI provider 选择。
6. 文档 / .env.example 更新。

---

## 验收记录（2026-08-01，v1.1 收口迭代）

- **验收工具**：`sidecar/scripts/eval_tts.py`（3 句 × provider，成功率/起音延迟/（本地）峰值内存，含预热剔除冷启动）。
- **edge**：3/3 合成成功；实测延迟 ~1.0-1.6s（本机到 Bing 端点首字节地板 ~0.93s：建连 0.56s + 服务端 0.37s + 整段接收/解码）。阈值定 **<2.0s**（网络抖动防翻绿），2026-08-01 复跑 PASS（0.98/0.97/1.52s）。
- **cosyvoice_cloud / cosyvoice 本地**：代码齐备但**未配置未验收**（dashscope key / 本地模型+依赖缺失，eval 输出 SKIP）。
- **backlog**：边收边播（起音 ~0.95s）与 websocket 连接复用（省 0.56s/句）。详见基线报告 §10。
