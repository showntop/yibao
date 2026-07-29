# 插件记忆巩固：业务数据如何沉淀成语义记忆（2026-07-29 调研 + 路线）

## 1. 背景

今天清理了三个插件（notes / forge / zimeiti）对 memory capability 的误用：

- **notes / zimeiti**：声明了 `memory` capability，但代码里从没调用 `ctx.memory`——纯 db 业务插件（闪念、素材是业务数据，本就该走 db，符合 v2 spec §9「禁双写」）。
- **forge**：是唯一调了 `ctx.memory.add` 的，但把「某次需求裁决」这种**业务事件**当语义记忆**实时双写**进 mem0（违反「禁双写」），而且 user_id 还对不上——写的是 `forge:user`（verdict.py 写死），记忆管理查的是 `forge:default`（ns + agent.user_id），写了也查不到。

清理后 mem0 只剩底座 `loop.py` 自动抽取的用户画像（「译宝」组），干净；三个插件命名空间不再出现在记忆管理。

**遗留的产品问题**：插件业务数据（闪念、裁决、素材）里其实**藏着用户画像**——50 条闪念里大半跟咖啡有关，就该沉淀出「用户关注咖啡」。当前没有任何机制做这件事，画像有盲区。本文调研业界做法，给出译宝的后续路线，**不是现在实装**。

## 2. 业界范式：episodic → semantic 巩固

认知科学借词，业界（MemGPT / Letta / Zep / LangMem）公认三层：

| 层 | 含义 | 译宝对应 |
|---|---|---|
| **episodic（情景）** | 具体事件：某条闪念、某次裁决 | 插件 db 记录 |
| **semantic（语义）** | 抽象画像：用户偏好 / 品味 | mem0 |
| **consolidation（巩固）** | 把大量情景**蒸馏**成少量语义 | **当前缺失** |

人脑在睡眠时巩固（事件 → 知识）；AI 用 LLM 周期性总结。

**触发巩固的三种业界模式：**

1. **MemGPT / Letta 的 reflection**：LLM 在交互间隙反思，把重要的从工作记忆沉淀到 archival memory（self-editing memory）。
2. **RecMem（arXiv 2605.16045）recurrence-based**：**只有某模式反复出现才巩固**——不是每条事件都抽，而是检测到频次 / 规律才蒸馏。避免噪音。
3. **Zep / Graphiti**：自动从事件构建时序知识图谱（temporal knowledge graph）。

**核心结论**：业务数据**值得**沉淀成记忆，但方式是**周期性巩固**（积累到量 / 检测到复发 → LLM 抽画像），**不是实时双写**。forge 之前的「每次裁决写一条」恰好反了——把 episodic 当 semantic 灌进去。

## 3. 映射译宝

| 插件 | episodic（db，已有） | 可巩固出的 semantic（该进 mem0） |
|---|---|---|
| **notes** 闪念 | 零散 idea / 灵感 | 近期关注主题、兴趣方向（「关注咖啡、读书」） |
| **forge** 裁决 | 每次需求的裁决 + 理由 | 产品品味（「偏爱技术驱动型、否决同质化」） |
| **zimeiti** 素材 | 收集的写作资料 | 写作领域 / 风格偏好 |

forge 当初 `verdict.py` 想做的「快筛时召回历史裁决」，本质就是要 semantic recall（按品味召回），但实现成 episodic 写入（存单条事件）——方向反了。巩固机制能真正实现它的意图：召回的是**巩固出的品味**，不是某次裁决记录。

## 4. 巩固 job 设计（后续实装路线）

```
触发：每天首次启动 / 某插件累积 N 条 / 检测到主题复发
  ↓
读该插件 db 近期 episodic 记录（如最近 50 条闪念）
  ↓
LLM 抽取该领域的用户画像（少量 semantic facts）
  ↓
写进 mem0 该插件命名空间（user_id = agent.user_id，对齐 list_all）
  ↓
记忆管理里出现该插件 chip + 画像（可编辑 / 删除）
```

**前置依赖（修当前坑）**：`ScopedMemory` 要绑定 `agent.user_id`（构造时传入），插件 add 不再传 user_id，使所有插件记忆统一落在 `{ns}:{agent.user_id}`，与 `list_all` 查询对齐。今天清理后这条链路暂时没人走，但未来巩固 job 落地时必须先把 user_id 约定对齐，否则又会重蹈 `forge:user ≠ forge:default`。

**去重 / 更新**：巩固是周期性的，重复跑靠 mem0 自身的去重 + 更新（add 返回 `ADD` / `UPDATE` / `NOOP`），不要在 job 里自己管。

## 5. 何时做（YAGNI + RecMem recurrence）

- 数据量没起来前，巩固出的画像是空的或纯噪音。**不要现在为「验证机制」而造场景。**
- 建议触发门槛（参考 RecMem recurrence：复发才巩固）：
  - **notes**：闪念 ≥ 30 条，或某主题 ≥ 3 次出现；
  - **forge**：裁决 ≥ 10 次；
  - **zimeiti**：素材 ≥ 20 条。
- 达到门槛才跑该插件的巩固 job；没达到就不跑，记忆管理里该插件命名空间不出现（这是正常的，不是 bug）。

## 6. 和今天清理的关系

- **今天**：清理误用（实时双写 + 空声明），mem0 干净，user_id 坑随之消失（因为没人再写插件记忆）。
- **本文档**：未来某插件数据量够了、真需要画像沉淀时，按 §4 落地巩固 job。属于后续路线，不是现在实装。

## 来源

- [MemGPT: Engineering Semantic Memory（episodic→semantic 转换）](https://informationmatters.org/2025/10/memgpt-engineering-semantic-memory-through-adaptive-retention-and-context-summarization/)
- [RecMem: recurrence-based consolidation（复发才巩固）](https://arxiv.org/html/2605.16045v1)
- [LangMem: Long-term Memory 概念指南](https://langchain-ai.github.io/langmem/concepts/conceptual_guide/)
- [Temporal Semantic Memory for Personalized Agents（Zep / Graphiti 式）](https://aclanthology.org/2026.findings-acl.1496.pdf)
- [Atlan: Agent Memory 类型与巩固路径](https://atlan.com/know/types-of-ai-agent-memory/)
