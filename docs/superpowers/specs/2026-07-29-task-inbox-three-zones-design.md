# 任务收件箱三区统一（§4.5 子项目 B）设计 spec（2026-07-29）

## 1. 背景

子项目 A 已把确认链路从单槽连环弹窗升级为批量确认：loop 一轮攒批、server 多槽、`confirm_batch`、Home 待批准队列，以及小窗/面板窗快批均已落地并通过真机验收。

Home 目前仍把任务状态拆散在三个位置：问候只显示数量，待批准独立成区，运行中任务藏在 agents 插件面板，结束任务混在通用 Feed。用户能收到结果，但不能在一个视图里回答「现在跑什么、什么等我、刚结束什么」。

本子项目把任务控制面统一成同页纵向三区：**进行中 / 待批准 / 已完成**。

## 2. 目标 / 非目标

### 目标

1. Home 同页纵向展示任务三区，状态一眼可见。
2. 进行中直接读取 agents 任务库，不复制任务状态。
3. 待批准复用 A 的共享确认队列与批量操作。
4. 已完成复用 Feed 的 task 结束事件，不新增完成记录。
5. 通用动态不再重复展示 task 事件。
6. 任务开始和结束后，Home 自动刷新到最新状态。

### 非目标

- 不做 Feed 项「跟进 / 忽略」。
- 不实装 Notify / Question / Review 三分级。
- 不引入独立任务中心页面或新的导航层级。
- 不增加任务轮询、并行调度或多 run 并发。
- 不做插件业务数据到语义记忆的巩固 job。
- 不改变 agents 任务表、Feed 表或确认队列的数据模型。

## 3. 产品设计

### 3.1 页面结构

Home 的滚动区按以下顺序排列：

1. **任务收件箱**：三个非空区按「进行中 → 待批准 → 已完成」纵向排列。
2. **Widget**：保留现有插件一瞥卡。
3. **动态**：只展示 reminder / event，不再重复展示 task。
4. **常用 Dock**：保持现状。

空区不渲染；三区全空时不额外展示空卡，维持 Home 的轻量感。已完成最多显示最近 5 条；进行中与待批准展示当前全量（server 对进行中设 20 条防御上限）。

### 3.2 进行中

每条进行中任务展示：

- 执行者标签：`沙箱脚本` 或 `<agent> 任务`；
- prompt 单行摘要；
- 从 `created_at` 计算的已运行时间；
- 「查看」入口，打开 agents 任务列表面板。

数据源是 `plugins/agents/data.db` 的 `tasks` 表，条件固定为 `status=running`，按 `created_at DESC` 排序。Home 只读，不在前端修改任务状态。

### 3.3 待批准

完整复用 A 已实现的 `PendingConfirm[]`、多选、批量批准/拒绝、单条快批和 remember。B 只把该区纳入统一任务收件箱的视觉层级，不改变确认协议或行为。

### 3.4 已完成

已完成直接取 Feed 中 `kind=task` 的最近 5 条，终态包含：

- `done`：完成；
- `failed`：失败；
- `stopped`：已停止；
- `interrupted`：已中断。

区标题仍为「已完成」，每条用状态标签明确真实终态，避免把失败包装成成功。点击沿用 Feed 行为：未读则标记已读，并把任务 prompt 作为上下文草稿带入对话。

### 3.5 动态去重

现有 Feed `items` 保持全量返回，前端派生：

```ts
const completedTasks = computed(() => items.value.filter((it) => it.kind === "task").slice(0, 5));
const activityItems = computed(() => items.value.filter((it) => it.kind !== "task"));
```

这样不改 Feed 的持久化、未读统计和查询协议；只是同一条 task 事件只在「已完成」出现一次。

## 4. 数据与接口

### 4.1 sidecar

`server.py` 增加只读 helper：

```python
def _running_tasks(limit: int = 20) -> list[dict]:
    # agents data.db 不存在或读取失败时返回 []
```

返回项统一为：

```json
{
  "id": "task-id",
  "kind": "agent",
  "label": "codex 任务",
  "prompt": "修复登录问题",
  "status": "running",
  "created_at": 1785290000
}
```

`{"type":"feed"}` 回包扩展为：

```json
{
  "type": "feed",
  "items": [],
  "stats": {},
  "running_tasks": []
}
```

Rust 已整体转发 feed JSON，因此无需新增 Tauri command 或事件。

### 4.2 TypeScript

`brain.ts` 新增：

```ts
export interface RunningTask {
  id: string;
  kind: "agent" | "script";
  label: string;
  prompt: string;
  status: "running";
  created_at: number;
}
```

`FeedResponse` 增加 `running_tasks: RunningTask[]`，空响应默认 `[]`。

### 4.3 刷新时机

- Home 首次挂载：现有 `getFeedOnce()` 同时取得 running tasks。
- `agents.*` 的 `action_result`：任务可能刚启动或停止，防抖后 `fetchFeed()`。
- `reminder`：任务结束主动事件，沿用现有防抖刷新。

不加定时轮询。若任务启动后没有 action_result 或结束后没有 reminder，重新进入 Home 仍会从权威存储对齐。

## 5. 错误处理

- agents 数据库不存在：`running_tasks=[]`，不为统计或展示创建空数据库。
- running 查询异常：记录诊断信息并降级为空，不影响 Feed 主响应。
- Feed 查询失败/超时：沿用前端空响应；Home、Widget 和 Dock 继续可用。
- 已读写失败：沿用现有乐观更新回滚。
- 批量确认失败：沿用共享确认队列恢复，不在 B 重复实现。

## 6. 验证

### 自动化

1. sidecar server：agents 库有 running/done/failed 时，feed 回包只在 `running_tasks` 返回 running，字段和排序正确。
2. sidecar server：agents 库不存在或坏查询时，feed 仍正常回包且 `running_tasks=[]`。
3. 前端结构测试：Home 存在三区标题；completed/activity 按 `kind` 分流；running task 点击进入 agents 面板。
4. 前端：`node --test tests/inbox-ui.test.mjs`、`npx vue-tsc --noEmit`、`npm run build`。
5. sidecar 全量：`cd sidecar && uv run --extra dev pytest -q`。
6. Rust：`cargo check --manifest-path app/src-tauri/Cargo.toml` 与 `cargo test --manifest-path app/src-tauri/Cargo.toml`。

### 最小真机路径

1. 派一个持续 30 秒以上的 agents 任务：Home 出现「进行中」。
2. 同时触发一条高风险确认：Home 出现「待批准」，进行中仍在。
3. 批准并等待任务结束：进行中消失，结束结果进入「已完成」。
4. 检查同一任务没有在下方「动态」重复出现。

## 7. 与 C 的边界

B 只统一已有状态的展示与刷新，不发明新的任务动作。跟进/忽略需要持久状态，Notify/Question/Review 需要事件分类与路由，继续作为子项目 C 单独设计和实现。
