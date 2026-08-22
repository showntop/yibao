# Home Feed 主屏化（§4.2）设计 spec（2026-07-29）

## 1. 背景

`os-feel-design §4.2` 的 Home 主屏化，Feed 流先行。**关键现状更正**：§4.2 写的「大窗 = 对话页 + 插件列表，chatbot 结构」**已过时**——代码已实装完整主屏：

- `desktop/src/components/HomeFeed.vue` 是完整主屏（问候条 / 待批准队列 / widget 卡片区 / Feed 动态列表 / 插件 Dock / 常驻输入条 5 区）；
- `sidecar/src/yibao_brain/feed.py` FeedStore 完备（append-only SQLite，`_KINDS = task/reminder/event`，`{"type":"feed"}` 协议 + Rust 桥 + `brain-feed` 事件 + 8 个测试）；
- 大窗 `Home.vue` 是侧边栏 + 4 页结构，**主屏是默认落地页**；
- 已接 **2 路真实数据**：agents 长任务完成（`plugins/agents/skills/_common.py` → `server.py:476`）、提醒触发（`server.py:358`）；
- widget 已有 2 个插件声明（agents 任务动态、notes 最近闪念）。

## 2. Gap

1. **数据源只接 2 路**：mem0 记忆新增、短 code_exec 结果、审计日志都没写 Feed——§4.2「数据全现成」没兑现。
2. Dock 是**全量插件平铺**，不是「常用直达」。
3. 问候是**固定常驻 header**，不是动态叙事。
4. Feed **无状态**（append-only），无已读/未读，无收件箱语义。

## 3. 目标 / 非目标

**目标（本次，§4.2 完整化）：**
- 补齐 Feed 数据源到「四类结果性事件」：任务完成、提醒触发、code_exec 跑完、新记住的东西。
- Dock 从全量平铺改为「常用 4–5」（频率排序 + 手动固定）。
- 问候从固定 header 升级为动态叙事，每次回主屏刷新。
- Feed 项加「已读/未读」轻状态，为 §4.5 收件箱化铺路。

**非目标（划到 §4.5 下一项独立 spec）：**
- 任务三区（进行中/待批准/已完成）、Feed 项跟进/忽略、待批队列一键批/拒、Notify/Question/Review 三分级。
- 审计日志**不接** Feed（避免流水账噪音；审计是另一层）。

## 4. 设计

### 4.1 数据源补接

四类结果性事件，已接 2 路、补 2 路：

| 事件 | 现状 | 动作 |
|---|---|---|
| 任务完成（agents 长任务） | ✅ 已接（`_common._wait` → `_on_plugin_event` → `feed.add("task")`） | 不动 |
| 提醒触发 | ✅ 已接（`server.py:358`） | 不动 |
| code_exec 跑完（短脚本） | ⚠️ 长任务已接，**同步完成的短脚本**（`plugins/agents/skills/sandbox.py` 同步收尾分支）未接 | 补：同步收尾分支加 `feed.add(kind="task", meta={...})`，与长任务同构 |
| 新记住的东西（mem0） | ❌ 未接 | 补：见 4.2（按小时合并） |

### 4.2 新记忆进 Feed（按小时合并）

**痛点**：`loop.py` 每轮 final reply 都调 `memory.add`（`loop.py:220` 同步 / `:330` 异步）。若每条 add 写一条 Feed，一晚聊 20 轮 = 20 条，噪音。

**机制（写入时按小时合并）**：
- `loop.py` memory.add 后，若 mem0 **实际新增了事实**（返回 results 含 `event=ADD`，非 `NOOP`/`UPDATE`）→ 调 `feed.append_hourly(...)`。
  - 前置：`Memory.add` 当前返回 `None`，要改为返回 mem0 的 results（或至少返回「是否新增」布尔），供 loop 判断。
- `feed.py` 新增 `append_hourly(kind, text, meta, hour_key)`：
  - `hour_key` = 当前小时整点时间戳（如 `int(ts // 3600) * 3600`）；
  - 查 feed 表是否有「同 kind + meta.type=memory + 同 hour_key」的最近一条；
  - 有 → 追加文本（`text` 末尾拼「；」+ 新事实）、更新 ts；
  - 无 → insert 新一条。
- kind 用 `event`，`meta = {"type": "memory", "hour": hour_key}`，不新增 kind。
- **只在新增（ADD）时写**；去重（NOOP）、更新（UPDATE）不写。

### 4.3 Dock：全量平铺 → 常用 4–5

**组成**：`手动固定` 优先 + `频率排序` 补齐到 4–5。

- **频率数据源**：从审计日志统计 per-plugin tool 调用次数（`audit.py` 已记录所有 tool 执行，加一个 group-by-plugin 的查询）。无审计数据时退化为字母序。
- **手动固定**：用户在主屏 Dock 或设置里「固定/取消固定」插件。存储 = `settings.json` 新增 `dock_pinned: ["plugin_id", ...]`（沿用 settings 机制，上限 5）。
- **Dock 渲染**：`dock_pinned` 在前 + 按频率取未固定的 top 补到合计 4–5。空 Dock（无固定无频率）退化为当前全量平铺的字母序前 5（不空着）。
- **UI**：Dock 每项加「固定」图钉按钮（已固定时高亮）。点插件图标仍 `panelAction(pid.list)` 开面板。

### 4.4 问候：固定 header → 动态叙事

- **触发**：每次落地/切回主屏（HomeFeed 激活）时刷新（复用现有 `getFeedOnce` 拉到的 stats，不额外请求）。
- **内容**：时段问候（早/午/晚，按本地时间）+ 动态叙事句，基于 `FeedStats`：
  - `done_24h > 0`：「昨晚/过去 24 小时跑完了 N 个任务」；
  - `pending_reminders > 0`：「今天有 M 个提醒」；
  - `running_tasks > 0`：「有 K 个任务正在跑」；
  - 全 0：「暂时清净，随时叫我」。
- 拼接规则：问候 + 1–2 句叙事（不超过两句，避免啰嗦）。

### 4.5 Feed 项已读状态（为 §4.5 铺路）

- **schema**：`feed` 表加 `read INTEGER DEFAULT 0`（迁移：`ALTER TABLE feed ADD COLUMN read INTEGER DEFAULT 0`，对存量行天然 0=未读；FeedStore 初始化时 `apply_schema` 幂等加列）。
- **查询**：`feed.recent()` 返回每条带 `read` 字段。
- **未读数**：`_feed_stats()` 加 `unread = feed.count_unread()`（`count where read=0`）。前端主屏 nav 项带未读 badge。
- **标记已读**：
  - 新增 IPC `{"type":"feed_mark_read","id":...}` 与 `{"type":"feed_mark_all_read"}`；
  - server 加 `_feed_mark_read(id)` / `_feed_mark_all_read()`，`UPDATE feed SET read=1`；
  - Rust 桥转发（`get_feed` 同构）。
- **前端触发**：Feed 项**点击进对话（openInChat）**时即标该条已读（乐观更新，失败回滚）；Feed 头加「全部已读」按钮（清理累积未读，避免无限增长）。

## 5. 接口 / 数据流变更汇总

**sidecar：**
- `feed.py`：加 `read` 列；`recent()` 带 read；`count_unread()`；`append_hourly(kind,text,meta,hour_key)`；`mark_read(id)` / `mark_all_read()`。
- `memory.py`：`Memory.add` 返回值改为返回「是否新增事实」（或 mem0 results）；`FakeMemory`/`Mem0Memory`/`LazyMem0Memory` 同步。
- `loop.py`：final reply 的 memory.add 后，若新增事实 → `feed.append_hourly(...)`（需 loop 持有 feed 引用，或经 agent 注入）。
- `sandbox.py`：同步收尾分支加 `feed.add(kind="task",...)`（需能拿到 feed；经 ctx 或 emit_event）。
- `server.py`：`_feed_stats` 加 unread；新增 `feed_mark_read` / `feed_mark_all_read` IPC 分发；Dock 数据组装（频率 + 固定）；挂 feed 给 loop/sandbox（如已挂则复用）。
- `audit.py`：加 per-plugin 调用计数查询。
- `config.py` / settings：加 `dock_pinned` 默认值 `[]`。

**Tauri / 前端：**
- `lib.rs`：`feed_mark_read` / `feed_mark_all_read` 命令；Dock 固定/取消固定命令；转发 `brain-feed` 带 read。
- `brain.ts`：`FeedItem` 加 `read`；`FeedStats` 加 `unread`；`markFeedRead(id)` / `markAllFeedRead()`；`setDockPin(pid, on)`；Dock 列表接口带频率/固定。
- `HomeFeed.vue`：问候动态叙事；Dock 频率+固定+图钉按钮；Feed 项点击（进对话）标已读 + nav 未读 badge；「全部已读」按钮。

## 6. 验证

**sidecar：**
- `test_feed.py` 扩展：`append_hourly` 合并（同小时追加、跨小时新建）；`read` 列迁移与查询；`mark_read` / `mark_all_read`；`count_unread`；`_feed_stats` 含 unread。
- `test_mem_settings.py` / `test_loop.py`：memory.add 返回值变更不破坏现有断言；loop 在新增事实时写 feed（mock feed 断言调用）。
- `test_server.py`：`feed_mark_read` 往返；Dock 组装（固定优先 + 频率补齐）。
- 全量 `uv run --extra dev pytest -q` 绿。

**前端 / 壳：**
- `cd desktop && npx vue-tsc --noEmit && npm run build` exit 0；`cargo check` / `cargo test`。

**真机：**
1. 派一个 agents 长任务完成 → Feed 出现「任务完成」；一个短 code_exec → 同样进 Feed。
2. 聊天说几个新偏好（"我喜欢 X"、"我叫 Y"），同小时内 → Feed 合并为**一条**「记住了：…」；跨小时 → 新一条。
3. 落地主屏：问候显示「早上好，过去 24 小时跑完了 N 个、今天有 M 个提醒」；切走再切回 → 重新刷。
4. Dock：点图钉固定一个插件 → 它常驻 Dock 前列；再多用某插件 → 频率上升进 Dock。
5. Feed 项点开 → 标已读；主屏 nav 未读数 -1；「全部已读」清零。

## 7. 与 §4.5 的边界

本次给 Feed 加了 `read` 列与未读 badge，是 §4.5 收件箱化的**最小铺路**。§4.5 下一项独立 spec 将基于此做：任务三区、跟进/忽略、待批队列一键批/拒、Notify/Question/Review 三分级。本次不做这些。
