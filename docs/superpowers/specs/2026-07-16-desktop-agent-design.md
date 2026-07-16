# 设计文档：本地常驻 AI 桌面 Agent 客户端（代号 Yibao）

- **状态**：草案 v1（待用户审阅）
- **日期**：2026-07-16
- **作者**：dennyxiao + Claude（结对设计）
- **关联调研**：`docs/research/2026-07-16-landscape-research.md`

> 一句话：桌面上常驻一个能说话的 AI 形象，快捷键唤起语音/文字输入，它看懂屏幕、按授权操作你电脑上的任意软件，并在使用中自进化（越用越懂你、越长技能）。

---

## 0. 目标与非目标

**目标**
- 24 小时常驻桌面（托盘 + 置顶透明形象窗），低占用。
- 快捷键唤起，支持文字与语音输入（语音本地转写）。
- 能"看懂"屏幕并操作任意软件（通用桌面助手）。
- 风险分级授权：低风险自动、高风险确认。
- 自进化：分四档逐步落地（记住你 → 长技能 → 会反思 → 能自学）。

**非目标（v1）**
- 不做持续环境感知/主动结对观看（v1 只按需看屏）。
- 不做精致 Live2D/VRM 形象（v1 轻量形象）。
- 不做多设备/手机。
- 不做技能市场。

---

## 1. 关键决策汇总

| 维度 | 决定 |
|---|---|
| 目标平台 | 跨平台，Windows + macOS 起步 |
| 桌面壳 | **Tauri (Rust) + Vue**（自研，借鉴 AIRI / BongoCat） |
| 大脑运行时 | **独立 Python sidecar**（agent 编排 + 感知 + 执行 + 记忆） |
| 分工理由 | 生态对位（Python 占 AI/自动化、Rust 占常驻壳）+ 故障隔离（壳长生不老、大脑可崩可重启） |
| 操作范式 | **技能优先混合**：a11y 优先 + 云端 computer-use 兜底 |
| 大脑模型 | provider 抽象：默认 **GLM computer-use（国内直连）**，可选 **Claude computer-use（代理、最强）**；本地 Ollama 走轻活 |
| 授权 | 风险分级（L0–L4） |
| 形象 | v1 轻量动态角色（状态机）；v2 升 Live2D |
| 输入 | 全局快捷键 → 文字 / 语音（本地 STT） |
| 自进化 | ①mem0 ②SKILL.md 技能库 ③反思 ④code-exec 自学，分阶段 |
| 语音 | STT=Sherpa-ONNX+Paraformer；TTS=edge-tts(v1)→CosyVoice2/IndexTTS(v2)；打断=Silero VAD |

---

## 2. MVP 范围（v1）

**做**
- Tauri 壳：托盘常驻、开机自启、全局快捷键、置顶透明形象窗（鼠标穿透）。
- 轻量形象：状态驱动（待机/听/思考/说话/工作）。
- 输入：快捷键 → 文字框；快捷键 → 按住说话（本地 STT）→ 文字。
- 大脑：云端 computer-use API（GLM 默认 / Claude 可选）做理解 + 规划。
- 执行：Python sidecar，a11y 优先 + 云端像素 computer-use 兜底。
- 风险分级授权（L0–L4）。
- 记忆（自进化第①档）：mem0。
- TTS：语音播报回复。

**不做**（后续阶段）：持续结对观看 ｜ 技能库自动积累 ｜ 反思 ｜ 自学 ｜ Live2D ｜ 多设备。

---

## 3. 进程架构

```
┌──────────────────────────────────────────────────────┐
│ Tauri Shell (Rust)  —— 必须"长生不老"                  │
│ · 系统托盘 / 开机自启 / 全局快捷键 / 单实例             │
│ · 置顶透明窗 (always-on-top + click-through)          │
│ · 权限引导 (Mac 辅助功能+屏幕录制 / Win UIA 授权)      │
│ · STT/TTS 宿主 (调用本地 sherpa / edge-tts)           │
│ · IPC 桥 + sidecar 进程守护 (拉起/重启/健康检查)       │
│   └─ Vue 前端：形象渲染 + 状态机 + 对话气泡            │
└─────────────────────┬────────────────────────────────┘
                      │ IPC (本地 socket / stdio JSON-RPC)
┌─────────────────────▼────────────────────────────────┐
│ Python Sidecar (agent runtime)  —— 允许崩溃，可被重启   │
│ · Agent Loop（LLM 编排 + tool-use）                    │
│ · 技能/工具层（执行抽象，技能优先）                     │
│ · 感知：mss 截图 + a11y 树(pywinauto / pyobjc AX)       │
│ · 执行：pyautogui / pynput 注入                        │
│ · 记忆：mem0                                            │
│ · 风险分级 + 授权闸门                                   │
│ · 云端 LLM / computer-use 客户端                        │
└──────────────────────────────────────────────────────┘
```

**IPC 契约（要点）**：壳 ↔ 脑 之间走 JSON-RPC。壳→脑：用户输入、配置、授权结果、截屏/执行请求回调。脑→壳：形象状态变更、对话文本、TTS 文本、高风险确认请求、日志/进度。协议带版本号。

**故障隔离**：脑进程崩溃/超时，壳保持形象在线并自动重启脑，重启后恢复未完成任务（持久化任务队列）。

---

## 4. 核心回路（agent loop）

```
输入(文字 / 语音→文字)
 → 意图理解 + 规划(云端 LLM, tool-use)
 → 逐步执行，每步：
     感知(截图 / a11y) → 决策(选技能 ｜ 走 computer-use 兜底)
       → 风险判定 → (L3+? 弹窗授权) → 执行 → 结果回传
 → 循环至任务完成 或 需用户介入
 → 形象状态机联动(思考/工作/说话) + 记忆写入
```

---

## 5. 执行层（技能优先混合）

- **技能 = 确定性可复用操作**（打开应用、在某 app 内查找/点击/填写…），底层走 **a11y API**：
  - Windows：`pywinauto` / `uiautomation`（读 UIA 控件树，直接拿 bbox，不点歪）
  - macOS：`pyobjc`（AXUIElement / ApplicationServices）
- **通用兜底技能 = 云端 computer-use**：无匹配技能/未知 UI 时，截图→云端模型→坐标/动作→`pyautogui`/`pynput` 注入。慢、贵、可能点歪 → 强制走风险分级。
- **技能格式**：采用 Anthropic Skills 的 `SKILL.md` 开放标准（指令 + 可执行脚本 + 资源），为 v2 自动积累铺路。
- **（v2 可选）本地 grounding**：OS-Atlas / ShowUI（Apache-2.0）替代部分云端调用，省钱 + 隐私。**不用 OmniParser**（检测权重 AGPL-3.0，商业传染）。

---

## 6. 风险分级授权

| 等级 | 示例 | 处理 |
|---|---|---|
| L0 只读 | 截图、读窗口列表、查询状态 | 自动 |
| L1 低风险 | 点击/输入/滚动/导航/切窗 | 自动（用户可调） |
| L2 中风险 | 关闭应用、批量操作、改文档内容 | 通知 + 可撤销日志 |
| L3 高风险 | 删文件、发消息/邮件、付款、改系统设置、装软件 | **弹窗确认** |
| L4 极高 | 格式化、提权、批量删除、外发敏感数据 | 弹窗 + 二次确认，可整体禁用 |

- 每个技能/动作声明其风险等级（元数据）。
- 所有执行写入**可审计操作日志**（含截图快照），支持回看与（尽力）撤销。

---

## 7. 形象

- **v1 轻量**：Vue + CSS/SVG 或简单 sprite，状态机驱动（待机 / 听 / 思考 / 说话 / 工作）。置顶、透明、鼠标穿透（本体可交互，靠动态切换 `setIgnoreMouseEvents`）。
- **v2 升级**：Live2D Cubism（口型/表情/说话动画最成熟，素材最多），直接套 **Open-LLM-VTuber** 的情绪映射 + 口型集成蓝本。

---

## 8. 输入与语音

- **唤起**：全局快捷键 → 弹出轻量输入条，可切"文字"/"按住说话"。
- **STT**（本地）：Sherpa-ONNX + Paraformer（纯 CPU、真流式、中文一流）。
- **TTS**：v1 用 edge-tts（零成本起步，许可偏灰，仅 MVP）；v2 换 CosyVoice 2 / IndexTTS-2（Apache-2.0、可克隆角色音、流式 ~150ms）。
- **打断**：Silero VAD + 可取消管道——用户开口即"三连取消"（停 TTS + 取消 LLM 生成 + 清 TTS 队列）。目标 TTFT < 400ms。

---

## 9. 自进化（分阶段 + 开放标准）

| 档 | 做法 | 阶段 |
|---|---|---|
| ① 记住你 | **mem0**（记忆抽取 + 检索） | **v1** |
| ② 长技能 | **SKILL.md 技能库 + Voyager 式语义检索**（技能=代码+说明，验证后入库，按需复用） | v2 |
| ③ 会反思 | Reflexion 式反思循环（自研：max 轮数 + episodic memory + 回归检查） | v3 |
| ④ 能自学 | code execution 工具 + 成功做法自动沉淀为 Skill | v3 |

**底座全用开放标准**：工具 = MCP；技能 = Anthropic Skills（SKILL.md）；动态解题 = code execution。
**自研差异化**：反思循环、领域 skill 内容、评估与安全治理（只装可信来源、审计生成代码依赖、防恶意 skill）。

---

## 10. 跨平台约束与风险

- **macOS**：透明窗需 `macOSPrivateApi:true` → **不能上 Mac App Store，但可签名 + 公证后站外分发**。需引导用户授予"辅助功能"+"屏幕录制"权限。无边框窗要重写 `canBecomeKey` 否则吃键盘。
- **Windows**：透明窗避免黑/灰底（关 GPU 加速或设透明背景色）；webview 渲染前白闪（先 `show:false`）。
- **Linux**：Wayland 无 always-on-top 标准 API，桌宠普遍失效 → Linux 降级为"普通窗口 + 托盘"或提示切 X11（Linux 非首批目标）。
- **可靠性现实**：即便前沿模型在 OSWorld 也仅 ~38–72%、beta 级 → 风险分级 + 技能优先是刚需，不能盲信全自动。

---

## 11. 复用组件清单

| 用途 | 组件 | 备注 |
|---|---|---|
| 桌宠外壳模板 | **BongoCat**（Tauri+Vue+Rust, MIT） | 透明/置顶/穿透/键鼠同步 |
| 形象+语音蓝本 | **Open-LLM-VTuber** | Live2D+情绪+透明桌宠+可插拔 LLM/ASR/TTS+Letta+MCP |
| VRM 渲染（v2+ 可选） | three-vrm | 3D 形象底座 |
| STT | Sherpa-ONNX + Paraformer | 本地流式，中文 |
| TTS | edge-tts(v1) → CosyVoice 2/IndexTTS(v2) | |
| 桌面自动化(Win) | pywinauto / uiautomation | UIA |
| 桌面自动化(Mac) | pyobjc (AXUIElement) | AX |
| 键鼠注入 / 截图 | pyautogui / pynput / mss | 跨平台 |
| 本地 grounding(v2) | OS-Atlas / ShowUI | Apache-2.0，非 OmniParser |
| 记忆 | mem0(v1) → Letta(v2) | |
| 工具协议 / 技能格式 | MCP / Anthropic Skills(SKILL.md) | 开放标准 |
| 跨平台 a11y 参考 | QwenLM/open-computer-use | Mac/Win/Linux a11y MCP 思路 |

---

## 12. 分阶段路线图

- **v1（MVP）**：Tauri 壳 + 轻量形象 + 全局快捷键输入(文字/语音) + 云端 computer-use 大脑(GLM 默认) + 技能优先执行(a11y + CU 兜底) + 风险分级 + mem0 记忆 + TTS。**证明"桌面上有个能说话的形象、能操作我软件"。**
- **v2**：持续结对观看（watch mode，本地 a11y 感知 + 预算化上云）+ 技能库自动积累（SKILL.md + Voyager 检索）+ Live2D 形象 + 本地 grounding(OS-Atlas/ShowUI) 可选 + Letta 记忆升级。
- **v3**：反思循环（Reflexion 式）+ 自学扩边界（code-exec + 自动沉淀 Skill）+ 主动提醒。
- **v4+**：多设备 / 技能市场（含安全治理）/ 端到端语音。

---

## 13. 错误处理 / 可观测 / 测试

- **错误处理**：脑进程崩溃由壳守护重启并恢复任务队列；每步执行失败可重试（带上一步截图反馈给 LLM）；网络/模型错误降级（切备用 provider 或提示用户）。
- **可观测**：结构化日志 + 操作审计日志（含截图快照）；形象状态机事件流；本地调试面板（查看 agent 回路每步）。
- **测试**：技能层单元测试（a11y mock）；agent 回路用录制的屏幕场景做回放测试；跨平台 CI（Win + Mac）。

---

## 14. 开放问题 / 待定

- 项目正式名（中文名，当前代号 Yibao 来自目录名）。
- v1 默认云端 provider 最终确定（GLM vs 豆包，看 computer-use 能力与价格）。
- 技能首批覆盖哪些应用（建议：浏览器 + 文件管理器 + 系统设置 + 1–2 个常用办公软件）。
- 是否 v1 就内置"可撤销日志"的存储后端（SQLite vs 文件）。
