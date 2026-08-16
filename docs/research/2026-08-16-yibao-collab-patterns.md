# 译宝 × 插件协作模式调研与推导（2026-08-16）

## 0. 问题

译宝大脑（GLM 调度者，掌控全局）× 插件智能体（编码=Claude Code、自媒体、笔记等局部执行者）之间：
协作模式有哪几种？通信怎么设计（事件全给大脑会不会太重）？怎么编排调度不脑裂？

## 1. 业界调研结论

### 1a. 编排模式（LangGraph / OpenAI Agents SDK / Anthropic / AutoGen / CrewAI / ADK）

业界沿「控制权归属」和「上下文模型」两轴收敛出 5 种正交模式：

| 模式 | 语义 | 控制权 | 上下文传递 | 适用场景 |
|---|---|---|---|---|
| Handoff（接管） | 分诊 agent 把会话整体移交专家，专家直接面向用户 | 转移后不再回来，同一时刻一个活跃者 | 接管方默认看到完整历史，可用 input_filter 裁剪 | 路由本身是工作流、专家要直接对话（= coding 面板） |
| Supervisor/Subagents（督导派生） | 中心 agent 保有会话所有权，专家当工具调用，收回结果自己合成 | 中心化 hub-and-spoke，可并行 | worker 独立上下文，只回传摘要/最终产物 | 有界子任务、后台长任务（= R4 远程监控） |
| Router | 代码/分类器路由分发 | 代码决定 | 按路由切片 | 意图明确的固定分发 |
| Pipeline（接力） | 固定顺序串联，产物顺序传递，可挂评审循环 | 模板化 | 顺序传递 | 步骤可预定义（= 自媒体流水线） |
| GroupChat | 消息广播全员，轮流发言 | 共享黑板 | 全员共享完整历史 | 辩论式协作（桌面场景不适用） |

层级化 = supervisor 递归嵌套，不是独立模式。Skills 模式（单 agent 保控制权、按需加载专业知识）适合轻量插件。

**Handoff vs Fork 判据**（OpenAI 官方）：专家只帮有界子任务→fork；专家应拥有本轮剩余会话→handoff。Claude Code 的 fork 继承父上下文、subagent 全新上下文——对应派生时是否带现场。

### 1b. 通信协议与事件粒度（A2A / MCP / AG-UI / Temporal）

共识：**生命周期必收、内容流订阅**。Supervisor 默认只收「生命周期事件 + 最终摘要」，全量流是 opt-in 观察层。

- Claude Code subagents：子 agent 隔离上下文，只回传摘要；父会话收 Start/Stop/权限请求等生命周期 hook
- LangGraph 5 档 stream mode 按粒度订阅；langgraph-supervisor 有 output_mode=full_history|last_message 开关
- MCP：progress 必须客户端先发 progressToken 才允许上报；elicitation 三态（accept/decline/cancel）做人回环
- AG-UI：17 种事件分 5 类（生命周期/文本流/工具调用/状态/特殊），分级思想值得抄但种类太多
- Temporal：心跳携带进度字段 + 超时判活 + 节流抑制冗余上报
- ACP 已废弃并入 A2A，勿考虑；A2A 的 Agent Card/webhook/版本协商对单进程桌面过重

**长任务进度 = 里程碑事件 + 低频心跳（兼判活）+ 可选百分比**，三者职责不同不互相替代。

### 1c. 产品形态（Codex / Devin / Copilot Mission Control / ChatGPT Agent）

成熟产品收敛到同一范式：**单入口派发 → 任务卡实体化 → 可旁观的执行过程 → 关键节点审批 → 完成回流汇报**。

- 防困惑第一原则：对外只有一个人格/入口（微软 Copilot 品牌爆炸被迫改名 agents 收口是反面教材）
- 委派要可见：具名任务卡（"编码小助手正在改 X"），不是隐藏委派
- 进度：完成/阻塞/需审批 → 强提醒；常规进度 → 静默进活动流；Claude Code 教训："12 分钟深任务和死循环看起来一模一样"
- 介入：Steer（纠偏即时生效）优先于 Queue（追加排队）；最便宜的介入点是动手前（Devin 计划前置确认被评为最大 UX 胜利）
- 小屏做监督和批准完全成立（Codex Mobile 就是纯监控面）
- 桌宠类产品（逗逗等）无"宠物派活给专家"先例——空白区，宠物状态即系统状态（忙/睡）的思路可借鉴
- Clippy/Bonzi 老教训：主动打扰和不可控会杀死桌宠，全知必须可降级为静默

### 1d. 失败模式（必须内化进设计）

- 委派失控：委派消息必须含 目标+输出格式+边界+工具指引（Anthropic 实证）
- 事件噪音互扰：子 agent 更新太频繁会带偏协调者——事件分级的直接证据
- 循环委派：所有框架都内置护栏（CrewAI 默认禁委派、Claude Code 限深度 3 层/并发 20）
- Supervisor 上下文饱和：8-12 轮子 agent 往返后路由准确率下降——大脑只收摘要的另一个理由
- Swarm agent drift：>8-10 次串行 handoff 后质量退化
- 多 agent token 消耗约为聊天 15 倍——事件分级也是成本控制
- 编码类任务真正可并行的少，「长会话接管 + 完成回填」优于「频繁双向委派」（Anthropic）

## 2. 推导：译宝协作模式目录（4 模式 + 1 底层通道）

| # | 模式 | 语义 | 控制权 | 译宝感知 | 适用 | 首发场景 |
|---|---|---|---|---|---|---|
| P1 | **接管（Handoff）** | 面板在场，专家直接对用户；输入条路由给专家 | 专家持有会话，译宝旁观全知 | 事件 tap 全收 | 专业控制台长会话 | coding 面板输入条接管 |
| P2 | **督导（Supervisor fork）** | 译宝派生后台任务，保有会话所有权，完工回流汇报 | 译宝持有 | 生命周期+摘要，完工气泡汇报 | 后台长任务/并行 | coding 后台修 bug（R4）、zimeiti 调研 |
| P3 | **接力（Pipeline）** | 插件产物作下一插件输入，译宝编排或事件触发 | 模板化流转 | 每棒产物+状态 | 可预定义流水线 | 划词存素材→zimeiti 素材库→forge 排版发布 |
| P4 | **会诊（Consult）** | 智能体反向向译宝/用户提问（elicitation 三态）；或译宝把插件能力当工具调（Skills 式） | 提问方等待应答 | 双向请求-响应 | 中途澄清/能力借用 | coding 审批上移 L2 确认条 |
| P0 | **旁观（Observe，底层通道非独立模式）** | reportPanelContext 现有机制：面板内容作为 focus 注入大脑上下文 | — | 持续 | 所有面板 | 已有 |

编排判据（路由规则）：
- 专家拥有本轮会话 → P1 接管（coding 面板打开时）
- 有界子任务/后台并行 → P2 督导
- 产物要流向下一个插件 → P3 接力
- 中途需要用户/大脑澄清 → P4 会诊（嵌入 P1/P2 内部，不独立触发）

## 3. 通信规范：事件分级最小集

借鉴 A2A 的 Task/Artifact 分离 + AG-UI 分级思想砍到 8 种 + MCP elicitation + Temporal 心跳：

```
信封：{ id, taskId, pluginId, type, ts, payload }
```

**L1 监督通道（大脑必收，每会话 ~10 个事件）：**
- `task.started {title}` — 具名任务实体化
- `task.progress {percent?, message, step?}` — 节流 ≥1s，里程碑式
- `task.artifact {name, mime, ref}` — 产物与状态分离（改动文件清单、生成的文案）
- `task.finished {summary}` — 一句话总结+证据引用
- `task.error {code, message, retryable}`
- `task.heartbeat` — 无事件时 ≥10s 一次，兼判活（区分"在干活"和"卡死了"）

**L2 订阅通道（opt-in，大脑默认不订）：**
- `task.delta {chunk}` — token/日志原文，仅 QuickPanel 活动流展开或调试时订阅

**L3 人回环（反向请求-响应，非事件）：**
- `task.ask {question, schema?}` → `{action: accept|decline|cancel, content?}` — 抄 MCP elicitation；coding 审批、插件澄清统一走这里

成本控制：大脑上下文只进 L1 + summary；L2 不进 LLM 上下文（纯 UI 展示通道）。

## 4. 编排与调度规则

- **路由器在大脑**：意图 → 插件/模式；用户显式指令（点插件、热键）优先于模型路由
- **委派消息模板**（P2 必守）：目标 + 输出格式 + 边界 + 可用工具指引
- **护栏**：委派深度 ≤2 层、并发 ≤4、串行 handoff 链 ≤5 次（防 drift）、事件节流 ≥1s
- **介入优先级**：计划前置确认 > Steer 纠偏 > Queue 追加 > 中止
- **打扰纪律**：完成/阻塞/需审批才强提醒；其余静默进活动流（Clippy 教训）

## 5. 防脑裂规则

1. 同一屏幕同一时刻只有一个文本输入框（接管排他）
2. 接管态有明确归属标识：placeholder + chip（"编码智能体接管中"）+ 团子状态同步
3. 对话历史不双写：接管期间全部归面板消息流，译宝侧只留一行回执
4. 团子是唯一人格和入口；智能体以具名任务卡存在，不以独立人格对用户说话
5. handoff 移交时裁剪历史（input_filter 思想），不让用户重复叙述（客服系统最大怨点）

## 6. 与现有架构映射 + 落地路径

现有资产：postMessage 桥 + api.toml 白名单（传输层）；reportPanelContext（P0 通道）；PanelApp L2 确认条（L3 落点）；InputBar draft 预填（@ 插入回输通道）；Chat.html 审批卡/工具卡/run pill（P1 面板侧控件）。

落地三步（每步独立可验收）：

1. **P1 接管**（当前焦点）：桥加 iframe→父事件通道 → chat.html 输入上移到工作台条（textarea 隐藏，控制件保留）→ 状态经 reportPanelContext 上报 → IME 守卫移植 → @ 按钮改插 draft。coding 面板被 PeekSurface 打开的处理一并定（倾向禁止 peek 重度控制台）
2. **L3 会诊上移**：coding 审批从 iframe 卡片迁到 PanelApp 确认条体系，统一人回环
3. **P2 督导**（= R4 起点）：coding.start 后台化 + 任务卡（团子气泡汇报）+ steer 介入；多任务即多 agent 会话墙

## 7. 主要来源

- [Anthropic: Building a multi-agent research system](https://www.anthropic.com/engineering/built-multi-agent-research-system)
- [Claude Code sub-agents](https://docs.anthropic.com/en/docs/claude-code/sub-agents)
- [LangChain multi-agent 模式矩阵](https://docs.langchain.com/oss/python/langchain/multi-agent) / [langgraph-supervisor](https://github.com/langchain-ai/langgraph-supervisor-py) / [LangGraph streaming 分档](https://docs.langchain.com/oss/python/langgraph/streaming)
- [OpenAI Agents SDK: orchestration](https://openai.github.io/openai-agents-python/multi_agent/) / [agents-as-tools](https://openai.github.io/openai-agents-python/tools/)
- [A2A spec](https://github.com/a2aproject/A2A/blob/main/docs/specification.md) / [MCP elicitation](https://modelcontextprotocol.io/specification/2025-06-18/client/elicitation) / [MCP progress](https://modelcontextprotocol.io/specification/2024-11-05/basic/utilities/progress)
- [AG-UI 17 事件](https://webflow.copilotkit.ai/blog/master-the-17-ag-ui-event-types-for-building-agents-the-right-way) / [ACP 现状](https://casys.ai/blog/mcp-a2a-acp-agent-protocols)
- [Temporal heartbeat](https://docs.temporal.io/develop/php/activities/timeouts)
- [MAST 失败模式分类（Berkeley）](https://arxiv.org/abs/2503.13657)
- [GitHub Copilot Mission Control](https://github.blog/changelog/2025-10-28-a-mission-control-to-assign-steer-and-track-copilot-coding-agent-tasks/) / [Introducing Codex](https://openai.com/index/introducing-codex/)
- [swarm vs supervisor 对比](https://www.augmentcode.com/guides/swarm-vs-supervisor)
