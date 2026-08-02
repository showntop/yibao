# 感知 v3 · Distiller 设计

日期：2026-08-02
状态：已评审（用户逐段确认）
前置：感知 v1（A/C 源 + ObsStore）、v1.1（按需对话加载）、v2（B 源屏幕感知）均已落地，733 测试全绿

## 1. 定位与目标

Distiller 是 sidecar 里的**离线深加工层**：每日把全天感知观察批量发给 LLM 提炼，产出模式记忆与效率洞察。它是 roadmap v3 的第一站（晨间反刍、时间线为后续消费者）。

核心架构决策（经讨论确认，「分层统一」方案 C）：

- **在线路径不动**：对话 → mem0 的即写即记保持现状（`loop.py` 每轮结束 `memory.add`），保鲜不受批处理影响。
- **Distiller 统一离线深加工**：所有需要回看历史、跨源关联、LLM 批处理的活只有 Distiller 一个入口。做在线路径做不了的事：跨源关联（报错时段的 app 切换轨迹）、模式归纳（作息、时段×app）、洞察生成。
- **提炼物是中间产物**：结构化落库为唯一存放，Feed / mem0 / 未来的晨间反刍与时间线都是消费者。

目标产物三类：

| 类型 | 例子 | 去向 |
|---|---|---|
| `pattern` | 「工作日上午 9-11 点深度使用 VSCode」「平均 23:40 后仍活跃」 | → mem0（长期，对话自然生效） |
| `insight` | 「昨天下午同个报错你在编辑器/浏览器间切了 14 次，历时 2.5 小时」 | confidence ≥ 0.6 的前 3 条 → Feed |
| `event` | 「凌晨 2 点仍在工作」「连续专注 3 小时 40 分」 | → Feed（append_hourly 合并） |

用户价值主张：记忆是一方面，**阶段性主动告知、帮助提效的建议**同等重要——insight 类产物即为此设。

## 2. 数据流（一次提炼）

触发：每日 04:17 自动跑「昨日全天」窗口 + 设置页手动「立即提炼昨日」。同一函数，仅触发源不同。

```
1. 汇集（本地，零 LLM）
   ├─ PerceptionStore.query_window 拉昨日 A/C 源 → build_activity_segments 合并时间线段
   ├─ 拉昨日 B 源文本条目（a11y 树文本 + vision 概括；截图原图采集时已删，碰不到）
   └─ 拉近 3 天 mem0 记忆 + 昨日对话历史（跨源佐证用，只读不写，禁双写）
        ▼
2. 预聚合（本地，零 LLM）
   原始观察 → 紧凑摘要（时段×app 计数、活跃块、B 源条目去重压缩）
   目标压到 ~1 万 token 内；超出按时间截断保最近
        ▼
3. LLM 提炼（1 次调用，复用主 GLMProvider 配置）
   输出严格 JSON：{patterns: [...], insights: [...], events: [...]}
   每条带 confidence(0-1) 与 evidence（引用哪些观察）
        ▼
4. 落库与投影
   ├─ 全部结果 → distillations 表（唯一存放）
   ├─ patterns → mem0（memory.add，靠 mem0 自身去重）
   └─ insights 按 confidence 排序取 ≤3 条 → 投影 Feed
```

成本：每日自动 1 次；输入 ~1 万 token、输出限 ~2K；单日观察为空跳过 LLM 调用只记日志。手动触发不限次（用户主动行为）。

## 3. 存储

新建 `distill.db`（与 feed.db 同目录，明文——内容已是提炼后的人话，不含原始观察）：

```sql
CREATE TABLE distillations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  day TEXT NOT NULL,              -- 提炼目标日 '2026-08-01'
  kind TEXT NOT NULL,             -- pattern / insight / event
  text TEXT NOT NULL,             -- 人话文本
  data TEXT,                      -- 结构化 JSON（evidence、时段、app 等）
  confidence REAL,
  projected INTEGER DEFAULT 0,    -- 是否已投影到 Feed / mem0
  created_at REAL NOT NULL
);
```

留存：原料表 14 天滚动清理，并入现有每小时 purge 循环（`_perception_cleanup_loop` 同款）。

投影纪律：

- **只把 pattern 写进 mem0**。insight/event 是时效性内容，进长期记忆会污染。
- Feed 投影格式：`feed.add("event", text, {"type": "distill_insight", "distill_id": N})`，带 `distill_id` 回指原料表；`meta.type` 归组复用现有 feedback 降频机制，用户可黙。
- event 类走 `feed.append_hourly` 合并，防刷屏。

## 4. 出站授权与隐私

- 新增独立开关 `perception.distill`（默认关），设置页「感知」组，行内确认文案：「每日将全天感知内容发送给当前模型做提炼，产出模式记忆与效率洞察」。
- 与 `perception.model_access` 互相独立：model_access 闸「对话中模型按需查阅」，distill 闸「每日自动批量提炼」。语义不同，分开授权。
- 关着则调度循环直接跳过，一步出站都不发生。
- 原料只有文本：B 源截图原图在采集时已「帧即清」，Distiller 碰不到图；三层过滤（黑名单/无痕标题/安全输入）在采集侧已生效。
- 依赖表达：Distiller 要吃 B 源需 `perception.screen` 同开，设置页提示此依赖。

## 5. 错误处理（全链路「挂了不碍事」）

- LLM 调用失败/超时（60s）→ 记日志，Feed 不写入，当天标记失败；次日不重跑，过期窗口跳过不积压。
- LLM 返回非法 JSON → 等同调用失败；未解析文本绝不直接投影 Feed。
- mem0 写入失败 → 只 print，不影响 distillations 落库与 Feed 投影（原料还在）。
- 并发互斥：同一时刻只允许一个提炼任务（自动/手动互斥；手动触发时已在跑返回「进行中」）。
- Distiller 异常 try/except 全包，绝不影响感知采集与主对话回路。
- 与感知背压同纪律：Distiller 是增强面，不许拖垮主链路。

## 6. 实现落点

sidecar（Python，`sidecar/src/yibao_brain/`）：

- 新文件 `distiller.py`：DistillerStore（distill.db 读写 + 14 天 purge）、gather（汇集+预聚合）、distill（LLM 调用+JSON 解析）、project（mem0/Feed 投影）、互斥锁。
- `server.py`：仿 `_perception_cleanup_loop` 起 asyncio 循环，每日 04:17 触发；purge 并入现有每小时清理；新 IPC `distill_now`（手动触发，返回 跑/不跑原因）；`_SETTINGS_DEFAULTS` 加 `perception.distill: False`。
- 复用：`query_window` / `build_activity_segments`（perception.py）、`memory.add`（memory.py）、`feed.add` / `append_hourly`（feed.py）、`GLMProvider`（llm.py）。

前端（app/）：

- 设置页「感知」组：`perception.distill` 开关 + 行内确认 + 「立即提炼昨日」按钮（显示进行中/完成/失败原因），B 源依赖提示。
- Rust/Tauri：IPC 透明转发 `distill_now`（现有转发模式）。

## 7. 测试与验收

自动化（sidecar pytest 现有体系，733 基础上新增）：

- 单测：预聚合压缩（超限截断/空数据）、JSON 解析（合法/非法/部分非法）、三类产物分流（pattern→mem0、insight≤3 投影、event 合并）、confidence 阈值过滤、互斥锁、失败路径（LLM 挂 / mem0 挂 / DB 挂互不传染）、`perception.distill` 关闭时零出站。
- 集成：手动 IPC 端到端——塞假观察 → 跑提炼 → 断言 distillations 表 + Feed 投影 + mem0 收到 pattern。
- LLM 用 `FakeProvider` 造假返回，零真实出站。
- 前端：vue-tsc + Vite build 过；Rust：cargo check + cargo test 过。

真机验收：

1. 开 `perception.distill`（含行内确认）→ 手动「立即提炼昨日」→ Feed 出现洞察、记忆管理页出现 pattern、distillations 表有原料。
2. 关开关 → 次日 04:17 无任何出站（看日志）。
3. `perception.screen` 关闭时设置页依赖提示正确。

## 8. 明确不做（本轮）

- 晨间反刍、时间线 UI：v3 后续站，届时纯消费 distillations 表，零返工。
- 主动建议旋钮闸门（`proactive_level` 扩展）、使用统计：v4。
- 对话 → mem0 在线路径迁移：保持现状（分层统一决策）。
- Distiller 独立模型配置：复用主 GLMProvider 配置（与 mem0 一致）。
