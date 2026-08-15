# Coding Agent 桌面工具竞品对照（2026-08-15）

调研方式：6 路并行子代理，实看截图/演示视频帧 + 浅克隆代码 + GitHub issues 按 👍 排序挖掘。
目的：为译宝 coding 线的 UI 打磨与产品定位提供证据基线。

## 一、品类地图（四层）

| 层 | 玩家 | 与译宝关系 |
|---|---|---|
| Agent CLI 上游 | Claude Code（2026.2 起原生 `--worktree` 并行 + Desktop app）、Codex、Gemini CLI | 译宝包 SDK，不竞争 |
| 编排控制台 | Emdash（MIT，YC W26，25+ CLI 自动检测，Best-of-N）、Conductor（商业 Mac，最 polished）、Claudia→opcode（AGPL 20k★，Tauri 2+React）、Vibe Kanban（MCP 双向看板）、Crystal→Nimbalyst（workspace+iOS 监控）、Claude Squad（TUI 5.8k★）、JetBrains Air、VS Code 多 agent 模式（2026.1 内建） | 正面刚无胜算；只借鉴不跟进 |
| 工作流引擎 | CCG-Workflow（MIT，Linux.do 社区）：hook 每轮注入状态抗 compaction、10 策略自动选择、双模型并行分析+交叉评审（Go 桥）、Agent Teams、质量闸门、100+ 领域知识注入 | 无 UI，方法论层；其「任务生命周期持久化 + 质量闸门」可借鉴 |
| 工作台 OS | holaOS（Electron，66 commits 的私有仓导出镜像，anti-SaaS 条款）：任意 agent 并排 + sqlite-vec 共享本地记忆 + HolaApps（BrowserView 真 app 面）+ 8 渠道接入 + 工作区控制塔 | 和译宝「个人 OS/工作台」愿景同构，最近的对位产品 |

品类共同 DNA：worktree 隔离、看板/会话墙、统一 review 流（diff→commit→PR）、工单派发、多 provider 抽象。

## 二、逐个竞品要点

### Claudia/opcode（getAsterisk→winfunc，Tauri 2+React18+Tailwind4+shadcn，同栈参照）
- 纯暗色近黑底，反色白底主按钮，卡片中圆角，密度偏低、居中窄列（~880px）。
- 浏览器式多标签栏（每会话一 tab，运行中带绿点）；Timeline 右侧 dock。
- 工具卡：chevron+图标+工具名+一句话意图摘要，展开着色输出，卡脚 `Tokens: X in, Y out`。
- Todo 三态圆圈（绿勾+删除线/蓝 spinner/空心）+ 优先级 pill。
- 运行状态 = 底部居中悬浮 pill：`◐ Executing... | ⏱ 1.0s | # tokens | [Stop]`。
- Checkpoint：时间线书签 + 恢复⟳/分叉⑂/对比±，策略可配（Smart=破坏性操作后自动建）。
- 输入条占位文案即教学（`@ for files, / for commands`）；思考强度 5 档弹层（图标+人话+信号条）。
- 槽点：`<tool_use_error>` XML 原文上屏；每条消息都挂 token 页脚噪音；tab 无去重；无亮色主题；4K 无缩放（issue 👍14）。

### Cline（VS Code webview，功能集与译宝面板同源）
- 继承 VS Code 主题变量，紫品牌色，小圆角高密度（400px 侧栏塞完整任务流）。
- 任务头卡聚合成本：`Tokens ↑26.4k ↓754 · API Cost $0.0441 · EXPORT`；每次 API 请求挂 `$0.023` pill。
- **审批主按钮用词动态化**：按动作渲染 "Run Command"/"Save" 而非通用 Approve，绿实心+灰 Reject 双大按钮。
- diff 跳编辑器原生页（独立面板产品必须内联解决——反例）。
- Checkpoint 恢复三选：仅文件 / 仅对话 / 两者（shadow git 不碰用户仓库）。
- 槽点：密度过高拥挤；新旧两代 UI 并存割裂；逐条串行审批 → 批准疲劳（官方文档自承）。
- 稳定性污点（最大教训）：无声挂起类 issue 霸榜（👍41/24/23/17）——所有挂起必须超时+取消+tail 日志。

### opencode（SST，设计感最强）
- v2 设计系统：纯中性灰阶 + 语义蓝 accent；**agent 角色色编码**（build 蓝/plan 粉/explore 黄/review 绿，各带 solid/border/bg 三件套 token）；alpha 白边框分层；0.5px 内描边环代替投影。
- 工具卡折叠单行：`* Grep "pattern" (18 matches)`、`→ Read <path>`——图标+工具名 bold+参数灰 mono+结果计数。
- Edit 卡：双行号槽 + 红/绿半透明行底 + **词级 diff 高亮** + diff 内保留语法高亮。
- 标题条右侧常驻成本三件套：`39,413 tokens · 20% · ($0.29)`。
- 底栏三段式：输入框（蓝 2px 左竖条）→ agent/模型选择行 → 快捷键提示行（**按键白、动作灰**）。
- 用户消息 = 左竖条+浅一层底色卡片，与输入框同构。
- 槽点：全 mono 排版对中文不友好（中文必须回系统无衬线）；密度过高无呼吸感。

### Emdash（多 agent 编排标杆）
- 明暗双主题一等公民；Linear 系冷淡风；侧栏密、主区疏。
- 三栏壳：任务树（PINNED/PROJECTS）| 会话/diff 主区 | Changed 审查栏。
- 任务行单行配方：`任务名… + 绿+47/红-1 + 25m`（相对时间逐级缩写）。
- **Best-of-N = 会话顶部 agent tab 条**（Claude Code/Codex/OpenCode 并排 tab 切换对比，非多窗口）。
- 右栏 Changed 一条龙：文件清单(M/A 图标)→Discard/Stage all→Commit message→Create PR，全无模态。
- 长输出折叠 `… +24 lines (ctrl+o to expand)`。
- 工单派发模态：From Branch/Issue/PR 三 tab，Linear/GitHub/Jira 下拉。
- 槽点：无真正看板分列；浅色对比度偏低；工具卡无成功/失败强视觉标记。

### holaOS（个人工作台，对位产品）
- 暖白奶油底 + 珊瑚橙品牌色，大圆角大留白，消费级审美（Plus Jakarta Sans）。
- 工具轨迹默认整组折叠单行（✓+动词摘要+chevron）；**失败步骤不上 trace，终态错误由 turn 级状态条统一呈报**（非技术用户友好，直接可抄）。
- 输入框上方挂**作用上下文 chip**（`Notion · xxx`）——agent 操作对象显式化。
- 工作区控制塔：卡片墙（状态丸+最近消息预览+消息数/时间脚）+ 全局 composer 向任意工作区派活。
- 共享记忆：sqlite-vec + 纯文件落地 + 知识图谱可视化（320 nodes 截图实证）。
- 真实度：记忆/渠道/HolaApps 有硬代码；多 agent 是 ~100 行薄 CLI 壳；内置模型/企业功能全闭源；OSS 仓是 2026-07 才导出的镜像。

## 三、组件形态对照表（译宝该对齐的规范）

| 组件 | 品类最优解 | 出处 | 译宝现状 |
|---|---|---|---|
| 工具卡 | 默认折叠单行：chevron+图标+工具名+一句话意图摘要+(结果计数)，展开才给内容 | 四家一致 | 有折叠但摘要非意图化 |
| 审批卡 | 动态动词主按钮（"Run Command"非"批准"）+ 绿实心/灰拒绝双大按钮；等待审批 vs 执行中必须视觉分态 | Cline / emdash#531 | R2 有审批卡，形态可升 |
| 运行状态 | 底部居中悬浮 pill：spinner+耗时+token+Stop | Claudia | 无（中断假死已修） |
| 成本透明 | 会话级聚合一行（tokens/占比/费用），不要每条消息挂 | Cline/opencode（Claudia 反例） | 完成行有耗时 token |
| Checkpoint 恢复 | 三选：仅文件/仅对话/两者 | Cline | R2 ⏪ 单档 |
| 错误呈现 | 不上消息流，turn 级状态条统一呈报；绝不渲染协议 XML | holaOS（Claudia 反例） | 待查 |
| 输入区 | 三段式：输入框→上下文/agent/模型行→快捷键行（键白动作灰）；占位文案即教学 | opencode/Claudia | 需重构 |
| 长输出 | `… +N lines` 单行折叠 | Emdash | 无 |
| 上下文挂载 | 输入框上方作用域 chip | holaOS | 无 |
| 多 agent 对比 | 顶部 tab 条（非分栏） | Emdash | 无（roadmap） |

## 四、未解决问题（issue 证据，按信号强度排）

1. **远程/移动监控**（最强跨仓信号）：claudia#163 SSH 远程 👍34、#79 HTTP 访问 👍15；emdash#901 👍8（全仓第一）。本地 GUI 捆死一台机器是公认最大短板。
2. **无声挂起**：cline #1157 👍41 / #1146 👍24 / #531 👍23 / #1229 👍17——挂起无超时无取消无诊断。
3. **审批疲劳**：逐条串行审批被 Cline 官方自承；用户要分层信任（低危自动+高危闸口）。
4. **先审后干**：claudia#105 plan mode ❤️50（全仓最高）——译宝 R2 已有 plan pill，领先。
5. **上下文添加弱**：cline#653 👍39 / #567 👍20——@ 拾取是最高频操作（译宝 R2 已做 @files）。
6. **编辑落盘不可靠/破坏性操作无护栏**：cline#2175 👍29、#5120 👍15。
7. **GUI 拉子进程环境坑**：claudia#94+#269 合计 👍30（PATH/shebang）——与译宝 pick_folder 主线程死锁同源教训。
8. **升级丢数据/强制改版**：vibe-kanban#2687 等合计 👍80——数据可导出、改版留旧版开关是信任底线。
9. **无缩放/字号设置**：claudia#86 👍14（Tauri WebView 一行 set_zoom 的事）。
10. **私有化/离线**：vibe-kanban#1697 👍25、#2952 强制登录反感。
11. **无人值守长任务自验证循环**：vibe-kanban#1946 👍14（新兴）。
12. **Windows/Linux 平台**：claudia 合计 👍29。

## 五、译宝定位启示

- 不做「开发者控制台」（Emdash/Conductor/Claudia 已卷死，且 VS Code/JetBrains/官方进场）。
- 空白地带：**控制台隐喻之外没有「生命」隐喻**——宠物盯梢/派活/验收/手机戳你，无人占。
- 远程监控是品类第一痛点，译宝有 mobile 文档线 + 宠物天然是通知载体，可错位吃下。
- holaOS 证明「工作台+共享记忆+真 app 面」方向有人走且工程扎实，但它无生命感、OSS 成色低；译宝的宠物+插件体系是差异。
- CCG 证明「工作流引擎/质量闸门」在中文社区有真实需求；译宝 coding 线后续可吸收其任务生命周期持久化思路。
