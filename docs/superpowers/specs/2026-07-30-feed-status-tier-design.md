# Feed 处置态 + 三分级（§4.5 子项目 C）设计 spec（2026-07-30）

## 1. 背景

§4.5 A（收件箱核心）+ B（三区统一）已完成。C 是收件箱体验的最后一块：Feed 项加**跟进/忽略**处置态 + **Notify/Question/Review** 三分级轻着色。

现状：Feed 只有 `read`（0/1）；`PendingConfirm.tier` 字段预留未用；EventKind 15 种无 tier 标识。

## 2. 设计

### 2.1 Feed 处置态（read + status，正交）
- `feed.py` 加 `status` 列（default `'none'`）：`none` / `follow` / `ignore`。
- IPC：`feed_mark_follow(id)` / `feed_mark_ignore(id)` / `feed_mark_none(id)`（取消标记）；`_feed_stats` 加 `ignored` 计数。
- 前端：Feed 项加「跟进 / 忽略」按钮；**忽略的折叠**（"已忽略 N 条"可展开）；可按 status 筛选（全部/跟进/忽略）。
- read 与 status 独立（跟进项仍可未读/已读）。

### 2.2 tier 三分级（自动推导 + 轻着色）
- Feed 项 tier **按 kind 自动推导**：`task`→Review、`reminder`/`event`→Notify（FYI）；收件箱 `confirmation`→Question（已有）。
- 前端：Feed 项按 tier 轻着色/图标标识（Notify 灰、Review 蓝），**可按 tier 筛选**，不强制分组（保持时间流）。
- 不显式标 tier 字段（事件不带 tier，前端按 kind 推导）。

## 3. 非目标（YAGNI）
- 跟进项的 snooze/到期提醒（跟进只是标记，不做定时提醒）。
- 显式 tier 标注（自动推导够）。

## 4. 接口/数据流变更
- `feed.py`：`status` 列（幂等迁移 ALTER）+ `mark_follow/ignore/none(id)` + `count_by_status(status)`。
- `server.py`：3 个 `feed_mark_*` IPC 分发 + `_feed_stats` 加 `ignored`。
- `app/src-tauri/src/lib.rs`：3 个 `feed_mark_follow/ignore/none` 命令 + 回包事件转发。
- `app/src/lib/brain.ts`：`FeedItem.status` + `markFollow/Ignore/None(id)` + tier 推导 helper（kind→tier）。
- `app/src/components/HomeFeed.vue`：跟进/忽略按钮 + 忽略折叠 + status 筛选 + tier 着色。

## 5. 验证
- sidecar `pytest`：status 列迁移（幂等）、`mark_follow/ignore/none`、`count_by_status`、3 IPC 往返、`_feed_stats.ignored`。
- `vue-tsc --noEmit` + `npm run build` + `cargo check`。
- 真机：跟进/忽略标记、忽略折叠展开、status 筛选、tier 着色、read 不受 status 影响。
