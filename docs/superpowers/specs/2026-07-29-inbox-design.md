# 任务收件箱（§4.5 子项目 A：收件箱核心）设计 spec（2026-07-29）

## 1. 背景

`os-feel §4.5` 把"任务/确认"从散装（播报散在对话、批准一次性弹窗、无统一视图）升级为**收件箱**——有状态控制面（待办、可批、可追溯、可批量）。

§4.5 全做跨 4 层（backend loop 重构 + 前端 4 处收口 + Feed 状态机 + 事件三分级），一个 spec 不现实，拆 3 子项目：
- **A 收件箱核心**（本 spec）：确认攒批 + 一键批/拒 + popup 收口
- B 三区统一视图（进行中/待批准/已完成聚齐）
- C Feed 跟进/忽略 + Notify/Question/Review 三分级

### 现状（零件齐、组装没做）

| 零件 | 位置 | 状态 |
|------|------|------|
| 确认闸门 | `loop.py:258/377` | 风险 > L1 → `confirmation_needed`，loop **阻塞** `await confirm` |
| server 单槽 | `server.py:470` | 一个 `pending_confirm` future，**同时只 1 个确认在飞** |
| remembered_confirm | `server.py:482/514` | 「本会话不再问」机制完备，前端没暴露 |
| HomeFeed approvals | `HomeFeed.vue` + `brain.ts` | 待批准区 + `PendingConfirm` + nav 徽标 |
| 4 处 popup | App/HomeChat/HomePlugins/PanelApp | ConfirmDialog/confirm-bar **散装、未收口** |
| agents tasks + Feed read | `plugins/agents` + `feed.py` | 已有（§4.2 铺） |

**连环弹窗根因**：loop 每个 CONFIRM 阻塞 + server 单槽，多步/多独立任务逐条弹。**攒批对"单轮多独立 CONFIRM"有价值**（如批量发邮件、批量删除——单轮 LLM 返回多个独立高风险 tool）。依赖链（B 用 A 结果）攒批仍安全（执行按顺序）。

## 2. 目标 / 非目标

**目标（A）：**
1. loop 一轮多 CONFIRM **攒批**（不再逐个阻塞）
2. server **多槽** + `confirm_batch` API
3. 大窗**收件箱**（多选 + 一键批/拒 + remember 勾选）
4. popup **分级退役**（大窗→收件箱；小窗/面板窗→单条快批）
5. 扩展性预留（`PendingConfirm.tier` 字段）

**非目标（不做）：**
- 三区统一视图（B 子项目）
- Feed 跟进/忽略 + 三分级实装（C 子项目；A 只预留 `tier` 字段，不实装 tier 逻辑/着色）
- loop **并行执行**（YAGNI；攒批 + 顺序执行已解决连环等。独立 tool 仍顺序跑，慢但正确）
- 多 run 并发（未来 server 调度模型；A 的多槽不阻挡，但不实装并发调度）

## 3. 设计

### 3.1 loop 攒批（核心改造）

**当前**（arun 一轮，`loop.py:374-383`；同步 run `:255-261` 同构）：
```
for tc in tool_calls:
    decision = invoker.decide(action)
    if CONFIRM: yield confirmation_needed; await confirm(action)  # 逐个阻塞
    execute(tc)
```

**改后**（CONFIRM 批量等，执行严格按 LLM 顺序，不重排）：
```
# 1) 按顺序收集本轮决策（不立即执行——避免 AUTO 抢在 CONFIRM 前重排、破坏依赖）
plan = [(invoker.propose(tc), invoker.decide(action)) for tc in tool_calls]
confirm_actions = [a for a, d in plan if d == CONFIRM]

# 2) 有 CONFIRM：批量等用户（一次推收件箱，不再逐个阻塞）
if confirm_actions:
    yield Event(kind="confirmation_needed", actions=confirm_actions)
    verdicts = await batch_confirm(confirm_actions)   # {action.id: (approved, remember)}

# 3) 按 LLM 顺序执行（依赖链安全：B 用 A 结果时 A 仍在 B 前）
for action, decision in plan:
    if decision == AUTO: execute(action)
    elif decision == DENY: messages.append(tool 策略拒绝)
    else:  # CONFIRM
        approved, remember = verdicts[action.id]
        if remember: gate.session_allow(action)       # 勾 remember 进 remembered_confirm
        if approved: execute(action)
        else: messages.append(tool 用户拒绝)
```

要点：
- **不判断独立/依赖、不重排**：CONFIRM 批量等，但执行严格按 LLM 顺序——B 用 A 结果的依赖链仍安全（A 在 B 前）。AUTO 不抢前。
- 单 CONFIRM（只 1 个）也走攒批路径（批量 size=1，统一逻辑）。
- `remember` 批量批时每个 action 独立带（勾了的 skill 进 remembered_confirm，后续同 skill 不再问）。
- `confirmation_needed` 事件载荷从单个 `action` 改为 `actions: list`（批量）；兼容单条（list 长度 1）。

### 3.2 server 多槽 + batch confirm API

**当前**：`pending_confirm = {"future": None, "early": None}`（单槽，`server.py:470`）；`confirmer(action) -> bool`（单）。

**改后**：多槽 dict + 批量 confirmer。
```python
pending_confirms: dict[str, asyncio.Future] = {}   # 按 confirmation_id 索引（多槽）
early_answers: dict[str, tuple[bool, bool]] = {}   # 早到的答案 {id: (approved, remember)}

async def batch_confirmer(actions) -> dict[str, tuple[bool, bool]]:
    # 为每个 action 建 future（或取 early_answer），await 全部，返回 {id: (approved, remember)}
    futures = {a.id: pending_confirms.setdefault(a.id, asyncio.Future()) for a in actions}
    results = {}
    for aid, fut in futures.items():
        if aid in early_answers: results[aid] = early_answers.pop(aid)
        else: results[aid] = await fut
    return results
```

**batch confirm IPC**（壳→脑）：
```json
{"type":"confirm_batch", "items": [{"id":"...", "approved": true, "remember": false}, ...]}
```
server 收到 → 对每个 item 设其 future 的 result（或存 early_answer 若 future 还没建）；回 `{"type":"confirm_batched", "ok": true}`。

**单 confirm 兼容**：小窗单条快批发 `confirm_batch` size=1（不保留旧 `confirm` 单条 IPC，统一 batch）。

### 3.3 大窗收件箱（HomeFeed approvals 升级）

现有 approvals 区（`HomeFeed.vue:254-267`）升级：
- 每条加 **checkbox**（多选）+ 单条「批准/拒绝」+ remember 勾选框
- 顶部「**全部批准 / 全部拒绝**」按钮（对选中的调 `confirmBatch`）
- 批量后乐观出队（选中项本地移除）+ 失败回滚；`remember` 传勾选状态
- 多条时（N>1）默认全选（鼓励批量）；单条时直接快批

### 3.4 popup 分级退役

| 窗口 | 当前 | 改后 |
|------|------|------|
| 大窗 `HomeChat` | ConfirmDialog | **移除** → confirmation_needed 路由主屏收件箱 |
| 大窗 `HomePlugins` | confirm-bar | **移除** → 路由收件箱 |
| 小窗 `App`（宠物窗） | ConfirmDialog（全套） | **简化为单条快批条**：单条直接批/拒；N>1 时只显示「N 项待批，去大窗批量」+ 团子 notify 态 |
| 面板窗 `PanelApp` | confirm-bar | **单条快批**（类似小窗） |

- 大窗收件箱是**批量批**的主入口；小窗/面板窗是**单条快批**（轻量，不强迫开大窗）。
- `confirmation_needed` 事件含 `actions: list`——大窗收件箱收全量；小窗/面板窗取**第一条**快批（或提示去大窗）。

### 3.5 扩展性预留

- `PendingConfirm` 加 `tier?: "Notify" | "Question" | "Review"`（C 子项目三分级用，A 不实装 tier 逻辑/着色）。
- `confirm_batch` API + 多槽 dict **不假设并发数**——当前单 run 抢占（dict 通常 1 entry），未来允许多 run 并发时这层不用改。

## 4. 接口 / 数据流变更

**sidecar：**
- `loop.py`：run/arun 的 CONFIRM 段改攒批（3.1 伪码）；`confirmation_needed` 事件载荷 `action` → `actions: list`。
- `server.py`：`pending_confirm` 单 → `pending_confirms` dict（多槽）+ `early_answers`；`confirmer` 单 → `batch_confirmer`；新 `confirm_batch` IPC 分发（替旧 `confirm`）。
- 测试：`test_loop.py`（攒批：多 CONFIRM 一次 yield、批量批回、顺序执行、remember 累积）、`test_server.py`（多槽同时多 future、confirm_batch 往返、early_answer）。

**Tauri：**
- `lib.rs`：`confirm` 命令 → `confirm_batch`（桥 `{"type":"confirm_batch","items":[...]}`）；`confirmation_needed` 转发载荷含 `actions`。

**前端：**
- `brain.ts`：`sendConfirm` → `sendConfirmBatch(items)`；`PendingConfirm` 加 `tier?`；`onPendingConfirms` 载荷适配批量。
- `HomeFeed.vue`：approvals 区升级收件箱（多选 + 一键批 + remember）。
- `App.vue`：ConfirmDialog 简化为单条快批条 + 多条提示。
- `HomeChat.vue` / `HomePlugins.vue`：移除 ConfirmDialog/confirm-bar。
- `PanelApp.vue`：confirm-bar 改单条快批。

## 5. 验证

**sidecar：** `cd sidecar && uv run --extra dev pytest -q` 全绿。重点：
- loop 攒批（多 CONFIRM 一次 yield + 批量批回 + 顺序执行 + 拒的跳过 + remember 累积）
- server 多槽（同时多个 future、early_answer 命中、confirm_batch IPC 往返）
- 回归（单 CONFIRM 走 batch size=1；AUTO/DENY 不变；reminders/feed 不受影响）

**前端/壳：** `cd app && npx vue-tsc --noEmit && npm run build && cargo check --manifest-path src-tauri/Cargo.toml`。

**真机：**
1. 派"给 A 和 B 各发一封邮件"（2 独立高风险）→ 大窗收件箱一次出现 2 条 →「全部批准」一次批 → 顺序执行。
2. 多步依赖任务（读→改→写，各 CONFIRM）→ 收件箱逐轮出现（依赖链仍顺序，不乱）。
3. 小窗：单条快批；多条时提示「去大窗」+ 团子 notify。
4. remember 勾选 → 同 skill 后续不再问。
5. 4 处 popup 退役后，确认只在收件箱/单条快批条，不再散装弹。

## 6. 与 B/C 的边界

A 只做收件箱核心（确认攒批 + 批量 + popup 收口 + 多槽扩展性）。B 三区视图、C Feed 跟进/忽略 + 三分级实装是后续独立 spec。A 预留 `PendingConfirm.tier` 供 C。loop 并行执行、多 run 并发均为非目标（未来）。
