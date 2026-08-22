# 晨间反刍 + 每日回顾 设计

日期：2026-08-04
状态：已评审（用户逐题确认）
前置：感知 v3 Distiller（`2026-08-02-perception-v3-distiller-design.md`）已落地——每日 04:17 把昨日观察提炼成 pattern(→mem0) / insight(≤3→Feed) / event(→Feed)，全量落 `distillations` 表。本设计是 Distiller 的**第一个消费者**，零返工。

## 1. 定位与目标

Distiller 每天产出的洞察现在以普通 `event` 混进动态页时间线，被淹没、无区分。本特性给这些提炼结果两个**可见出口**：

| 子能力 | 是什么 | 用户价值 |
|---|---|---|
| **晨间反刍** | 打开主窗时，团子主动把「昨日简报」端到你面前（气泡 + 可选语音） | 不用翻找，主动送达昨日的效率洞察与建议 |
| **每日回顾** | 动态页新增「回顾」视图，按天回看每日提炼（数字 + 洞察 + 提炼状态） | 随时翻阅历史，看自己每天怎么用电脑 |

反刍的气泡点开 → 跳到回顾视图的当天。

**核心架构决策**（逐题确认）：

1. **反刍触发 = 打开主窗**（非首次对话、非固定时段）。最贴近"早上打开看一眼"的自然时机。
2. **推送零 LLM、零延迟**：反刍文本推送时即时从 `distillations` 模板拼装，不调模型。
3. **建议在凌晨提炼时生成**：升级 Distiller prompt，让 insight 携带「现象 + 具体建议」，存库；早上只是端出。不新增 LLM 调用。
4. **回顾范围 = 每日提炼 + 结构化活动统计**（A+）：纯消费 distillations + 提炼副产物，不 live 查 perception store。
5. **回顾落点 = 动态页顶部 toggle「动态｜回顾」**：保持三页导航，回顾作为独立 view mode，per-day 粒度不污染 per-event 流。

## 2. 建议的深度边界（重要预期）

译宝能观察到的是 **app 时长、窗口标题、屏幕文字片段**层面。因此反刍里的建议：

- ✅ **靠谱**（行为 / 工作流层面）：「你在 Safari 耗了 2 小时，建议…」「连续专注 3 小时没动，建议每 50 分钟起身」「深夜 1 点还在干，建议早睡」——有时长 / 切换 / 作息数据撑着。
- ⚠️ **给不了**（领域专精层面）：「你这段代码思路错了，应该用 X 算法」——需深度理解任务，光靠屏幕文字片段做不到，硬给是瞎编。

即：建议是「怎么更高效用电脑 / 安排时间」，不是「怎么把你的活儿干得更好」。后者是更远的事（更深任务理解 + 更多隐私算力），本轮不做，prompt 里明确要求模型只给有观察依据的建议、没有就留空。

## 3. 数据流

### 晨间反刍（开窗即推，一天一次）

```
用户打开主窗（tray/热键/点 dock）
   ▼
前端检测 window-shown（getCurrentWindow 可见性事件）
   ▼  invoke("recap_check")  ← fire-and-forget，每窗 show 一次
大脑 serve_async 收到 recap_check
   ├─ 闸门：perception.master AND perception.distill AND perception.recap 全开？
   ├─ 去重：distill.db meta(recap_last_day) == 今天？是 → 直接返回
   ├─ 选材：day_items(昨天) → 取 insight(置信降序 ≤3)；无 insight 取 event 1 条兜底；空 → 返回
   ├─ build_recap_text(items) 模板拼「早上好，昨天我注意到：①… ②…」
   ├─ ProactiveDispatcher.emit({kind:"reminder", type:"morning_recap", text, day, deep_link})
   │     └─ 复用现成：level 闸（quiet 只落 Feed / full 气泡+语音）+ 同类👇降频 + Feed 记账
   └─ set_recap_day(今天)  ← 持久化去重标记，抗重启
   ▼
Rust brain-event → 前端 onBrainEvent → 团子气泡（surface=pet）
   ▼ 点气泡 / 「看详情」
切到动态页 → 回顾 mode → 滚到当天
```

### 每日回顾（按天浏览）

```
前端切到「回顾」mode
   ▼  invoke("get_distill_timeline", {days:14})
大脑 DistillerStore.recent_days(14)
   ▼  返回 [{day, status, stats:{app_seconds, active_blocks}, items:[…]}]
Rust brain-distill-timeline → 前端按天渲染卡片
```

## 4. 存储（distill.db 变更）

复用现有 `distillations` / `runs` 两表，**新增一个轻量 `meta` 表 + 给 `runs` 加一列**：

```sql
-- 反刍去重（单例 kv，抗重启）
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
-- 回顾用的结构化活动统计（gather_summary 本就计算了 app_seconds/活跃段，此前丢弃）
ALTER TABLE runs ADD COLUMN stats TEXT;   -- JSON: {"app_seconds":{"VSCode":11520,...},"active_blocks":[["09:00","11:00"],...]}
```

`meta` 走 `CREATE TABLE IF NOT EXISTS`（写进 `_SCHEMA`）；`runs.stats` 是新列，对存量 distill.db 用**幂等迁移**——`DistillerStore.__init__` 里 `ALTER TABLE runs ADD COLUMN stats TEXT`，catch `duplicate column` 跳过（与 `feed.py` 给 `read`/`status` 列做迁移同款）。同一 target_day 可能有多行 runs（auto+manual），`recent_days` 取该天最新一行的 stats。

- `gather_summary` 返回值扩容：除 `{app_count, screen_count}` 外，把已算好的 `app_seconds`（秒）与 `active_blocks`（HH:MM 二元组列表）一并返回（零额外计算，原本就算了）。
- `Distiller.run_yesterday` 跑完把当天 `stats` 写进对应的 `runs` 行（`record_run` 增可选 `stats` 入参）。
- 反刍去重：`set_recap_day(day)` / `recap_last_day()` 读写 `meta`。

**投影纪律不变**：pattern 仍只进 mem0，insight/event 投影规则不动；反刍与回顾都是 distillations 的**只读消费者**，不写 distillations。

## 5. 反刍内容选择（纯逻辑，可单测）

`recap_select(day_items, *, now) -> list[dict] | None`（distiller.py 新增，注入 clock 可测）：

1. 只看 `kind="insight"`，按 `confidence` 降序取 ≤3。
2. 若无 insight，取 `kind="event"` 1 条（取最新）兜底。
3. 全空 → 返回 None（昨日无可反刍内容，静默不推）。
4. **pattern 永不进反刍**（它已进 mem0 长记忆，是对话资产，不重复打扰）。

`build_recap_text(items) -> str`：模板「早上好。昨天我注意到：①{insight1} ②{insight2}」。每条 insight 文本已含「现象——建议」。≤2 句开场白固定，主体靠 distillations 成品人话。

## 6. 出站授权与隐私

- 新增独立开关 `perception.recap`（默认**关**），设置页「感知」组 distill 之下。
  - 反刍本质是把昨日提炼**主动端到你面前**——与 distill 同源隐私语义，故默认关、需显式开。
  - 依赖 `perception.distill`：distill 关时 recap 禁用并提示「需先开启每日提炼」。
  - 闸门语义：`perception.master AND perception.distill AND perception.recap` 全开才出。
- **反刍本身零新出站**：内容来自已产出的 distillations，建议在凌晨那次（已授权的）distill 调用里生成。recap 开关只控「要不要主动推给你」，不增加任何网络出站。
- 回顾视图纯本地查询 distill.db，零出站。

## 7. 错误处理（全链路「挂了不碍事」）

- 反刍任何异常（选材失败 / emit 失败 / 标记失败）只 print，**绝不影响开窗与主对话**。
- 昨日无产物（distill 关 / no_data / failed / 无 insight 且无 event）→ 静默不推，不 nag。
- recap_check 是 fire-and-forget：大脑不在线 / 超时 → 前端无感（本就没等回包）。
- 回顾查询失败 → 前端空态「暂时没有回顾」，不崩。
- 与 Distiller 同纪律：反刍 / 回顾是增强面，不许拖垮主链路。

## 8. 实现落点

### sidecar（`sidecar/src/yibao_brain/`）

- `distiller.py`：
  - `_DISTILL_PROMPT` 升级：insight 要求「**现象 + 具体可执行建议**」，明确「只给有观察依据的建议，没有就留空，不要编造领域专精建议」。
  - `gather_summary` 返回值扩容含 `app_seconds` / `active_blocks`。
  - `DistillerStore`：新增 `meta` 表 + `set_recap_day` / `recap_last_day`；`recent_days(n)`（跨天查询：JOIN runs 状态+stats 与 distillations items，按天聚合，含空天状态）；`record_run` 加 `stats` 入参。
  - 新增纯函数 `recap_select(day_items, *, now)` 与 `build_recap_text(items)`。
- `server.py`（serve_async）：
  - 新 IPC `recap_check`（fire-and-forget）：闸门 → 去重 → 选材 → emit（经 ProactiveDispatcher）→ 标记。全 try/except。
  - 新 IPC `get_distill_timeline`：调 `recent_days`，回包经新事件 `distill_timeline` 出。
  - `_SETTINGS_DEFAULTS` 加 `perception.recap: False`。
- 复用：`ProactiveDispatcher.emit`（reminder 路径，含 level 闸 / 降频 / Feed 记账）、`DistillerStore.day_items`、`FeedStore`。

### 前端（`desktop/src/`）

- `lib/brain.ts`：`recapCheck()`（invoke `recap_check`）；`fetchDistillTimeline(days)` / `getDistillTimelineOnce()`（命令 → `brain-distill-timeline` 事件）；类型 `DistillDay{day,status,stats,items}`。
- `components/HomeFeed.vue`：顶部加 toggle「动态｜回顾」（macOS Segmented Control，复用现有 `.segmented` 样式）；回顾 mode 渲染按天卡片（数字条 app 时长 + 活跃段 + 洞察/事件列表 + 提炼状态徽章：已提炼/未提炼/失败）。
- `App.vue`：主窗 show 检测（`getCurrentWindow` 可见性事件）→ `recapCheck()`（每窗 show 一次）；`onBrainEvent` 收 `morning_recap` → 团子气泡；气泡点击 → 切动态页 + 回顾 mode + 滚到 `event.day`。

### Rust（`desktop/src-tauri/src/lib.rs`）

- IPC 桥接：sidecar 出的 `distill_timeline` 行 → `app.emit("brain-distill-timeline", v)`（仿现有 `brain-distill-now` / `brain-feed-stats`）。
- recap 走现有 `brain-event` 通道（已是 reminder 事件），无需新通道。
- `recap_check` / `get_distill_timeline` 加入 invoke handler 表（现有转发模式）。

## 9. server.py 轻量拆分（穿插，用户选「本轮穿插做轻量拆分」）

server.py 已 76KB。借本次新增反刍/回顾循环之际，做**保守、有回归网**的拆分：

- 新文件 `background.py`：把三个自包含后台循环及其纯 helper 整体搬出——
  - `_perception_cleanup_loop` / `_distiller_loop` / `_reminder_loop`
  - 各自依赖的纯函数（`_gate_proactive_event`、`_dispatch_reminder`、`_recover_background_jobs`、`_watch_tick` 等，按依赖顺带）
- `serve_async` 只留：建循环（`asyncio.ensure_future(...)`）+ IPC 分发主循环 + `build_loop` 编排。
- **不动**：IPC dispatch 表、`build_loop`、`handle_panel_action`、voice/tts 泵——这些动了回归面太大，本轮不碰。
- 反刍 `recap_check` 处理与 `get_distill_timeline` 自然落在 background / distiller 侧。
- **回归网**：拆分后 756 测试必须全绿（这些循环/helper 多有单测兜底）；拆分以"纯搬运、不改逻辑"为纪律，任何行为变更单独成 commit。

## 10. 测试与验收

**自动化（sidecar pytest，756 基线上新增，零真实 LLM）**：

- `recap_select`：insight 降序取 ≤3 / 无 insight 取 event 兜底 / 全空返 None / pattern 不入选（注入 clock）。
- `build_recap_text`：模板拼装 / 多条编号 / 空输入。
- 去重：`recap_last_day == 今天` 时 recap_check 零 emit；标记写入后抗重启（新 store 实例仍命中）。
- 闸门：master / distill / recap 任一关 → 零 emit。
- `recent_days`：跨天聚合 / 空天状态 / stats 回填 / 按天分组。
- Distiller prompt 升级：FakeProvider 造「带建议 insight」返回 → 解析 → 投影 → recap_select 能选出。
- `gather_summary` 返回含 app_seconds / active_blocks。
- 拆分回归：搬出循环后 serve_async 仍正常调度（现有循环单测全绿）。

**前端**：`vue-tsc` + `Vite build` exit 0；Rust：`cargo check` + `cargo test` exit 0。

**真机验收（留人工）**：
1. 开 `perception.recap`（含依赖提示）→ 次日开主窗 → 团子气泡出昨日简报（含建议）→ 点气泡跳回顾当天。
2. 同日再开窗 → 不重复推。
3. 昨日无产物 → 开窗静默无气泡。
4. quiet 档：只落 Feed 不响；full 档：气泡 + 语音。
5. 回顾 mode：按天卡片数字 + 洞察 + 状态正确；空天显示「未提炼」。
6. 同类👇≥2 → 后续反刍降 quiet（复用现有降频）。

## 11. 明确不做（本轮）

- **领域专精建议**（"你代码思路错了应该用 X"）——观察深度不够，留 backlog。
- **完整活动时间线**（原始 A/B/C 观察 per-hour 仪表盘）——宽版 B，perception store schema 非为回看设计，是另一个大特性。
- **独立回顾页**（大窗第 4 tab）——选了 toggle 方案，不做。
- **固定晨间时段推送**（方案 3）——选了开窗触发，不做。
- **Distiller 独立模型配置**——建议生成复用主 GLMProvider，不单独配模型。
- **签名公证 / 分发**——不碰。
