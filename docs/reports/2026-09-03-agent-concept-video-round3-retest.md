# 第三轮溪场复测：Agent 概念科普视频全流程（含 3 个修复项验收）

> 日期：2026-09-03
> 环境：macOS 原生译宝 debug 构建（含当日修复），主屏「溪场」preset，窗口 1040 × 700
> Case：同前两轮——面向第一次接触 Agent 的普通职场人，60 秒中文竖屏科普视频
> 方法：KimiCU 后台无障碍操作 + 本地持久化数据直查（work_graph.db / zimeiti data.db / ffprobe）
> 前序：[08-31 首轮审计](2026-08-31-agent-concept-video-agent-os-review.md)、[09-01 溪场复测](2026-09-01-agent-concept-video-field-mode-review.md)、[通用架构规格](../design/2026-09-01-agent-os-generalized-architecture.md)
> 边界：本轮新增的截图证据在 `assets/2026-09-03-agent-video-round3/`；复测期间用户在本机同时使用 Chrome，输入注入两次被完全遮挡中断（环境噪声，非产品问题）。

## 〇、本轮前置修复（复测对象的一部分）

复测前先修了上一轮遗留的三个小缺口，均已带测试合入：

1. **文本默认不 TTS**：`_stream_agent` 新增 `tts` 开关，文本 run 默认不建播报队列；语音会话（voice_start）强制播报；run 消息可显式 `tts: true`（为将来「朗读」入口留通道）。`server.py`、`runtime/voice.py`；新增 `test_text_run_does_not_tts_by_default`，既有 4 个 TTS 用例改显式 tts。sidecar 全量 1479 passed。
2. **窄窗项目可见性**（P1-09）：溪场 narrow/slim 档把项目卡并入今日轴列（`axis: ["project", "today"]`），1040px 下工作语境卡（项目名、目标、当前阶段、产物数）直接可见。`presets.ts`；装配测试 37 passed。
3. **「不打开面板」进裁决**（P1-05）：`arun` 检测用户文本里的显式压制意图（不打开/别弹/不要弹 面板/窗口等），命中则本轮工具带回的 panel 一律压成 `attention=quiet`（只进活动轨，连 explicit 也剥掉）。`loop.py`；新增 2 个测试。

## 一、结论

**主链路首次端到端走通，且真的产出了可播放的 60 秒竖屏 MP4。** 09-01 报告的六个 P0 里，除「中断留证」本轮没能在 GUI 层复现验证（见验收 4）外，其余在真实机测中全部成立；视频链从「立项 → 选题 → 证据 → 脚本 → 分镜 → 视觉 → 配音 → 时间线 → 渲染 → 交付」全程以一等 Artifact 推进，v1–v4 版本链完整，WorkflowRun 最终状态 `completed` 由验收（时长实测 56.7s 落在 55–65s 区间）而非 Agent 自报驱动。

与前两轮的本质差别：**验收门禁三次把人拉回来改稿**（86.3s 超时 → 压稿 v2 → 49s 不足 → 回补 v3 → 75.4s 又超 → 压到 v4 → 43.4s → 时间线补呼吸位 → 56.7s 通过）。这正是「完成由产物验收决定」的正面证据——但也暴露了配音能力缺语速参数导致的四轮往返（见问题 N3）。

同时必须诚实标注边界：**视觉内容仍是降级占位卡**（纯深色底 + 每镜画面描述文字排版，非 AI 生图）。系统在视觉阶段开始前明确告知了这一降级并给出三选一，符合「不在能力外承诺」；但最终成片对真实观众而言是字幕卡视频，不是科普短片。

### 综合判断（与前两轮对比）

| 维度 | 08-31 | 09-01 | 09-03（本轮） | 依据 |
|---|---:|---:|---:|---|
| 产品逻辑 | 5.5 | 4.5 | 8 | 验收驱动真实生效；三轮压稿虽繁琐但每次都停在人这里拍板 |
| 业务/领域建模 | 3 | 3.5 | 8 | topic/evidence/script/storyboard/shot/visual/voice/timeline/render 全部一等 Artifact，显式边 38 条 |
| 系统架构 | 5 | 4.5 | 7 | Work Graph + outbox + durable render 成立；Stage 宿主仍薄壳、器内容重启不重水合 |
| 形态创新 | 7 | 6.5 | 7 | 三态稳定；Peek 仍会在错误时机盖住待按印按钮（N2） |
| 可扩展性 | 3.5 | 4 | 6.5 | 视频链全由 Workflow Pack + 能力合同跑完；横版转码仍退回裸 shell |
| 可访问性 | 4.5 | 5 | 未测 | 本轮未做专项 |

## 二、验收标准逐条核对（09-01 报告第九节）

| # | 验收标准 | 结果 | 证据 |
|---|---|---|---|
| 1 | 新会话可选择继承/新任务/无项目，默认不隐式串线 | ✅ 核心成立 | 新会话工作语境卡显示「这段会话还没有工作语境 · 不会继承其他会话的项目与素材」，可接入 2 + 新建工作语境；地平线 ctx 回到 `home`。选择器是事后接入卡而非立项前选择弹层，可接受 |
| 2 | 「不复用旧素材」时查询带项目 scope，零旧项目对象 | ✅ | 本会话 8 条素材全部挂新选题 `ce61cf97`（含 project_id 回写 `proj_d702495d02e7`）；素材库 Peek 只显示本项目素材。立项前的「看库存避撞」是全局读，属有意为之 |
| 3 | UI 状态与最新 run 一致；迟到事件不改 UI | ✅（本轮未观察到分裂） | 地平线 echo 与 ctx 全程一致（think/work/idle 转换正常）；立项后选项直接交付，无「过早 idle 再等继续」 |
| 4 | 中断研究后，新一轮能列出已抓取来源 | ⚠️ 未能在 GUI 复现 | 三次尝试在生成中途按停止，本机生成/抓取均快于点击落点，run 都在打断前完成。该能力由单测覆盖（`test_loop.py` 中断留证 + `test_server.py` speech_stopped 分离），标「未复测」不算回归 |
| 5 | 停止 TTS 不产生「已打断」 | ✅（本轮文本轮零播报） | 全程文本对话无一次进入 speaking 态（修复 1 生效）；speech_stop/run_cancel 分离由单测锁定 |
| 6 | 「不打开面板」时 tool panel 只产 Inline 回执 | ✅ | 「保存后不要打开面板」一轮：5 条素材静默落库，只出 Inline 回执卡，无任何 Peek |
| 7 | 项目中可查 topic/evidence/material/script/storyboard/assets/voice/timeline/render 的显式关系 | ✅ | work_graph.db 直查：30+ Artifact（5 evidence、7 shot、7 visual、7 voice、timeline、render），边 contains×7 / derived_from×16 / uses×14 / rendered_from×1 |
| 8 | Stage 关应用重开后恢复 artifact/版本/滚动/草稿/运行/待确认 | ❌ 部分成立 | 会话历史、项目绑定、产物计数、工位入口全部恢复；Stage 壳与面包屑可恢复，但**器内容（编辑器正文）空白**，两次「恢复工位」均不水合 |
| 9 | 60 秒视频产出真实 MP4，时长/比例/未核实断言可验收 | ✅（视觉降级已披露） | `v1.mp4`：h264+aac、1080×1920、56.73s、1.5MB，ffprobe 独立验证；时长由 acceptance 区间卡住。帧抽查证实画面为占位文字卡（见 §五） |

## 三、本轮主链路实录（真实落点）

| 阶段 | 产物（真实落盘） | 关键行为 |
|---|---|---|
| 立项 | Workspace `proj_d702495d02e7` | L3 按印门禁；WorkflowRun `video.explainer` 建立 |
| 选题 | topic `ce61cf97`（三轮复测·方案C） | 3 个方向给齐再停；选题静默落库并挂项目 |
| 证据 | 5 条 research.evidence（Anthropic/OpenAI/UiPath 官方原文） | 「来源原文/归纳/设计观点」三栏；遵守「不打开面板」 |
| 脚本 | video.script v1–v4（408→205→342→203 字），内容在 BlobStore | 保存前给完整预览；版本递增 |
| 分镜 | storyboard + 7 shot（时长分配 4/5/11/12/11/5/12=60s） | 每镜可单选寻址 |
| 视觉 | 7× asset.visual（dark 占位卡 PNG） | 提前诚实声明降级并给三选一 |
| 配音 | 7× voice.track（Tingting，实测时长逐镜回写） | 时长实测成为验收硬输入 |
| 时间线 | timeline.composition v1（56.7s） | 见 N3：校准走了裸 shell |
| 渲染 | video.render v1 = 真实 MP4 | durable 长任务；ffprobe 验收写回；WorkflowRun → completed |
| 追加 | 横版 v2.mp4（1920×1080 模糊背景适配） | 渲染工具固定竖屏，横版退回裸 ffmpeg 命令 |

## 四、做得好的（本轮新增）

- **验收驱动第一次真的咬人**：时长三次不达标三次停下给选项（86.3s / 49s / 75.4s），每次都带实测数据和取舍建议，没有一次蒙混过关。
- **立项、落稿、落分镜、渲染全部按印**；批量审批（2 项待按印）与收件箱聚合工作正常。
- **无意 chaos 测试零泄漏**：复测中途我误点「切换」把会话切到旧项目两次（运行中），随后切回；事后全库核查，三轮复测期间**没有任何 artifact 写入其他 Workspace**。
- **新会话零串线 + 工作语境卡**：「不会继承其他会话的项目与素材」直接写在卡上。
- **窄窗项目卡回归**：1040×700 下项目名/目标/当前阶段/产物数直接在今日轴列可见。
- **素材全部有名有姓**：8 条素材全挂选题，无孤儿（09-01 的 P1-03 未复现）。

## 五、本轮新问题

| ID | 级别 | 问题 | 证据/影响 |
|---|---|---|---|
| N1 | P1 | **运行中可静默切换工作语境** | 会话运行中点「切换」立即改绑，无确认、无可见事件、无对运行中任务的保护。本轮两次误切没有造成串写，但靠运气不靠机制。应有 `SessionScopeChanged` 可见事件 + 运行中切换需确认 |
| N2 | P1 | **Peek 遮挡待按印按钮** | 分镜保存后自动弹出的选题详情 Peek 正好盖住「全部按印」按钮，必须先关 Peek 才能批准。瞬态层不得遮挡待决动作 |
| N3 | P1 | **时间线时长取入库元数据而非文件实测** | 配音落库后我用外部手段补了静音（56.7s），timeline builder 仍按入库时的 43.4s 组装，要靠 Agent 用裸 python 校准 JSON。工具合同应「组装前 ffprobe 重测音轨文件」 |
| N4 | P2 | **补静音这种媒体修整没有工具能力** | Agent 被迫走 watch_command 裸 shell，连续四轮失败（路径空格 / .tmp 封装 / 输出截断 / 时长未变）。缺「音轨修整」能力，也侧面说明 watch_command 不该承担这类活 |
| N5 | P2 | **配音无语速/目标时长参数** | Tingting 实测语速在 3.3–4.7 字/秒间漂移，导致 v1–v4 四轮压字。voice 能力应支持目标时长（自动调 rate 或补静音），把时长收敛从「人来回拍板」变成「能力内收敛 + 验收兜底」 |
| N6 | P2 | **重启后 Stage 器内容空白** | 验收 8 的失败项。SurfaceSession 只恢复了壳与对象身份，webview 器不重水合；即架构文档 Phase 4 的未做项 |
| N7 | P2 | **立项时不提示「视觉=降级占位」** | 能力预检存在（占位 provider 不算缺口所以无 blocked），但用户直到视觉阶段才知道是占位卡。立项回执/预检摘要应提示「降级路径」而不仅是「有无」 |
| N8 | P3 | **溪场家态提醒胶囊刷屏** | 主屏连续多条提醒胶囊（专注/久坐/流水线和重复的「中午好」），与任务无关的家态噪声依旧 |

## 六、证据边界

- 打断留证（验收 4）未在 GUI 层复现，不算通过也不算回归；由单元测试覆盖。
- 「文本不 TTS」以 UI 无 speaking 态 + 代码评审 + 单测为证；未直接测量音频输出。
- 横版转码、补静音由 Agent 用裸 shell 完成，其产物不在 Artifact Graph 里（绕过了工具合同）——本轮容忍，但 N3/N4 已立案。
- 视觉占位卡是双方确认的降级，不是缺陷；但它意味着「成片」离真实可发布还有 AI 生图能力的差距。
- 复测数据保留在本地：Workspace `proj_d702495d02e7`、topic `ce61cf97…`、renders `…/ce61cf97…/v1.mp4 + v2.mp4`。

## 七、下一步建议（按序）

1. **修 N1/N2**（小改动、高信任杠杆）：SessionScopeChanged 可见事件 + 运行中切换确认；Peek 避开待按印区。
2. **修 N3**（工具合同）：timeline 组装前对音轨文件 ffprobe 重测，不信入库元数据。
3. **配音能力加目标时长参数**（N5）：把四轮压字收敛成一次。
4. **Phase 4 薄壳问题**（N6）：Stage 器内容重启水合，这是「可恢复工作台」承诺的最后一公里。
5. **真实图 provider**（替换占位卡）与 **deck pack 真实 pptx**：前者让视频链真正可交付，后者验证内核泛化——这两项是下一轮审计的入场券。

## 八、修复回填（2026-09-03 下午，同日完成）

第七节 1–3 已当日修掉，均带测试：

| 问题 | 修法 | 证据 |
|---|---|---|
| N1 运行中静默切换 | ① sidecar：`project_switch` 成功后向该会话发 `notice`（含 `workspace_id`）——「工作语境已切换到『X』」落对话流（SessionScopeChanged 最小落地）。② desktop：会话 busy 时「切换」变两步确认（首点转「确认切换？」4s 回落，再点才切） | `test_server.py::test_project_switch_emits_scope_notice`；`HomeProject.test.ts` 2 例；实机复测确认 notice 出现在对话流 |
| N2 Peek 盖按印 | desktop：有待按印闸门时自动 Peek 让位落活动轨；闸门从 0 到有时收掉在开的 Peek（`HomeWindow.vue`） | vue-tsc 干净；vitest 411 passed（改动面小，实机难复现 staging，标代码级验证） |
| N3 时间线信元数据 | `timeline_save` 组装前对每镜音轨 ffprobe 实测；实测不可用才回退入库元数据，clip 带 `duration_source`（measured/metadata/storyboard） | `test_zimeiti_timeline.py` 10 passed（含真实链路 + 实测/回退两例） |
| N5 配音无目标时长 | `voice_save` 新增 `fit` 参数：按分镜目标时长逐镜收敛——短了 apad 补尾静音、长了 atempo 轻度变速（≤1.15x）、超 15% 标 `over` 不毁音、fit 失败保留原轨标 `unfit`；fit 才刚需 ffmpeg | `test_zimeiti_voice.py` 3 例（真实 say/afconvert/ffmpeg 跑通 padded / over / 默认不收敛） |

回归：sidecar 全量 **1486 passed**；desktop vue-tsc 干净、vitest **411 passed**（7 个 unhandled errors 全在 `PanelWindow.handoff.test.ts`，与本次改动无关，属存量）。

仍未做（保持 backlog）：N4（音轨修整能力化——N5 的 fit 已吸收最常见场景）、N6（Stage 器内容重启水合，Phase 4）、N7（立项回执提示降级路径）、N8（家态提醒噪声）。
