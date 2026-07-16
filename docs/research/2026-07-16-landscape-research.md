# 赛道调研汇总：本地常驻 AI 桌面 Agent（2026-07-16）

> 本文件汇总设计前的全部调研结论，作为选型依据与后续参考。分四大块：A 现有产品/项目全景、B「操作电脑」范式横评、C 形象+语音栈、D 自进化机制。star/日期为 2026-07 近似值，正式选型前建议复核最新仓库。

---

## A. 现有产品 / 项目全景（一句话定位）

| 项目/产品 | 定位 | 与我们的关系 |
|---|---|---|
| **AIRI**（moeru-ai，~7.2k★） | 自托管 AI 陪伴/VTuber 平台（灵感 Neuro-sama），Live2D+VRM，CUDA/Metal/WebGPU 本地推理 | 形象+语音+人格+本地推理做得完整，**但不操作电脑、不看桌面工作流**——只陪伴。我们的差异点正是补这半边 |
| **Open-LLM-VTuber**（~13k★） | 最完整开源方案：实时语音/打断、视觉、情绪映射、AI 主动发言，**Electron 透明桌宠模式**，LLM/ASR/TTS 全可插拔，已集成 Letta+MCP | **avatar/voice 半边的架构蓝本**（借鉴不 fork） |
| **BongoCat**（~22k★，Tauri+Vue+Rust，MIT） | 现象级跨平台桌宠 | **Tauri 桌宠外壳最佳实践模板**（透明/置顶/穿透/键鼠同步） |
| 微软 Copilot（Avatar/Portraits） | 桌面助手推形象 | 商业参照 |
| 字节扣子 Coze / 豆包 | 桌面职场 Agent + 操控电脑 | 国内商业参照 |
| 仓鼠元元 / Vpet(Steam) | 国内 AI 办公桌宠 / 虚拟桌宠模拟器 | 形态参照 |

> 行业面：2025 被称"AI 玩具/桌宠元年"，纯角色聊天商业化退烧，**桌宠/硬件被视为中国 AI 陪伴下半场最好的载体**。

---

## B. 「操作电脑」范式横评

三种范式**互补不替代**：

| 范式 | 代表 | 思路 | 优势 | 劣势 |
|---|---|---|---|---|
| **可访问性树/UIA** | **UFO/UFO²**(微软,9.1k★,MIT,Win-only,活跃) | 读控件属性，vision 仅 fallback | 快、稳、几乎不点歪；WAA 领先 Claude/Operator 10%+；PiP 隔离虚拟桌面；speculative multi-action 省 51% LLM | 平台锁死（Win=UIA / Mac=AX / Linux=AT-SPI）；非 a11y 的自绘 UI 失效 |
| **像素/视觉** | **OmniParser**(微软,~24k★) | YOLO 检测+Florence-2 caption→喂 VLM | 通用，任何软件/设备 | 慢(0.6–0.8s/图)、需 GPU、专业应用 >60% 点歪(ScreenSpot Pro 39.6%)；**检测权重 AGPL-3.0（商业传染）** |
| **代码生成** | OS-Copilot/FRIDAY(已停更)→**OS-Symphony**(ACL2026,OSWorld 65.8% SOTA) | 写并执行 Python/bash/AppleScript | 适合文件/脚本类任务 | GUI 操作弱 |

**computer-use 封装/产品**：
- **Anthropic Computer Use**：pixel-level，官方自述会"hallucinate coordinates"、慢；跑在容器内 Linux 虚拟桌面。
- **OpenAI Operator/CUA**（GA，gpt-5.5/5.6）：趋势是 **code-execution harness**（淡化裸像素点击）；OSWorld ~38–61%。
- **QwenLM/open-computer-use**：**accessibility-API hybrid，Mac/Win/Linux 全平台**——与我们执行层同构，可当参考/基座。
- **browser-use**(80k★) / **stagehand**(22k★)：浏览器场景 DOM/a11y hybrid 最成熟。
- **agent.exe**(corbt)：不维护 PoC；**E2B open-computer-use**：云沙箱（非控本机）。

**GUI grounding 开源模型**（纯像素坐标预测，闭环软件无 a11y 时用）：
| 模型 | license | ScreenSpot-v2 | 备注 |
|---|---|---|---|
| **OS-Atlas-7B** | Apache-2.0 | 87.1% | 桌面数据最强，统一动作空间 |
| **ShowUI-2B** | Apache-2.0 | —（v1 75.1%） | 最轻(2B)、最活跃、可本地 |
| SeeClick-9B | Apache 代码/ckpt 受限 | 53.4%(v1) | 鼻祖，已超越 |
| OmniParser | 代码 MIT/**检测权重 AGPL** | ScreenSpot-Pro 39.6% | 商用慎选 |

**可靠性冷水**：通用 CUA 在 OSWorld 50 步任务 ~34.5%、专业应用 grounding >60% 失败；前沿模型 OSWorld ~38–72%、仍 beta 级 → 坐实「风险分级 + 技能优先」。

---

## C. 形象 + 常驻 + 语音栈（最省力又好看的组合）

**形象选型**：**Live2D Cubism 首选**（口型/表情/说话动画最成熟、素材最多、免费 SDK）；要 3D 立体感→VRM(three-vrm)。CSS/SVG 做不出像样说话动画（仅 v1 占位可接受）。

**置顶透明窗踩坑**：
- Tauri v2：`transparent/decorations:false/alwaysOnTop/skipTaskbar` + `noRedirectionBitmap`(避 Win 白闪)。
- **macOS 透明必须 `macOSPrivateApi:true` → 不能上 Mac App Store，但可签名公证站外分发**；无边框窗需重写 `canBecomeKey`。
- **Linux Wayland 无 always-on-top 标准 API → 桌宠普遍失效**（GNOME 基本无解，KDE 可绕）；Linux 降级"普通窗+托盘"或提示切 X11。

**语音（中文一流、全本地、可商用）**：
- **STT**：Sherpa-ONNX + Paraformer（纯 CPU 流式）⭐；SenseVoice 备选。
- **TTS**：**edge-tts**（MVP 零成本，许可偏灰）→ **CosyVoice 2 / IndexTTS-2**（Apache-2.0、角色音克隆、流式 ~150ms）。
- **打断**：Silero VAD + 可取消管道（停 TTS + 取消 LLM + 清队列），TTFT<400ms。
- 端到端语音（Moshi 等）暂不建议（贵或中文弱）。

---

## D. 自进化机制（四档成熟做法 + MVP 取舍）

| 档 | 代表做法 | 成熟度 | MVP 取舍 |
|---|---|---|---|
| ① 记住你 | **mem0**(轻 SDK) / **Letta**(有状态 agent server) / LangGraph memory / Zep | 成熟可直接用 | **mem0** |
| ② 长技能 | **Voyager 式技能库**(代码+描述+语义检索) / **Anthropic Skills**(SKILL.md 开放标准，含可执行代码) | 范式成熟，框架自研 | **借 SKILL.md 格式自建技能库** |
| ③ 会反思 | Reflexion(episodic memory+verbal RL) / Self-Refine / CRITIC | 范式成熟，无标准库 | **自研反思循环**(max 轮数+回归检查) |
| ④ 能自学 | LATM/CRAFT/Toolformer；已被 Code Interpreter/code-exec 工程化；官方路线=agent 自建 Skill | 基座可调，上层治理自研 | **用 code execution + 成功做法沉淀为 Skill** |

**总原则**：底层全用开放标准（工具=MCP、技能=SKILL.md、动态解题=code execution）；上层自研差异化（反思循环、领域 skill、评估与安全治理）。

---

## 关键参考源

- AIRI https://github.com/moeru-ai/airi · Open-LLM-VTuber https://github.com/Open-LLM-VTuber/Open-LLM-VTuber · BongoCat https://github.com/ayangweb/BongoCat
- three-vrm https://github.com/pixiv/three-vrm · Live2D SDK https://www.live2d.com/en/sdk/about/
- Sherpa-ONNX https://github.com/k2-fsa/sherpa-onnx · CosyVoice https://github.com/FunAudioLLM/CosyVoice · IndexTTS https://github.com/index-tts/index-tts · edge-tts https://github.com/rany2/edge-tts · Silero VAD https://github.com/snakers4/silero-vad
- UFO https://github.com/microsoft/UFO · OmniParser https://github.com/microsoft/OmniParser · OS-Symphony https://github.com/OS-Copilot/OS-Symphony · QwenLM/open-computer-use https://github.com/QwenLM/open-computer-use · browser-use https://github.com/browser-use/browser-use
- OS-Atlas https://github.com/OS-Copilot/OS-Atlas · ShowUI https://github.com/showlab/ShowUI
- Voyager https://github.com/MineDojo/Voyager · mem0 https://github.com/mem0ai/mem0 · Letta https://github.com/letta-ai/letta · Reflexion https://arxiv.org/abs/2303.11366
- Anthropic Skills https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills · MCP https://modelcontextprotocol.io
- nut.js（付费化警示）https://nutjs.dev/blog/i-give-up · PyAutoGUI https://github.com/asweigart/pyautogui · pynput https://github.com/moses-palmer/pynput · python-mss https://github.com/BoboTiG/python-mss
