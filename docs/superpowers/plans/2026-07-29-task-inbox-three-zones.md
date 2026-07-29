# 任务收件箱三区统一 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Home 同页纵向统一展示进行中、待批准、已完成三个任务状态区，并从通用动态中去掉重复的任务结束事件。

**Architecture:** sidecar 在现有 `feed` 回包中附带从 agents 任务库只读归一化出的 `running_tasks`；待批准继续使用前端共享确认队列，已完成继续使用 Feed 的 `kind=task` 事件。Home 只做派生分流与展示，不新增数据库、轮询、Tauri command 或任务状态写路径。

**Tech Stack:** Python 3.12 / pytest、Vue 3 / TypeScript、Node test runner、Rust / Tauri v2（仅回归验证）。

---

## Global Constraints

- Spec：`docs/superpowers/specs/2026-07-29-task-inbox-three-zones-design.md`。
- 进行中只读 `plugins/agents/data.db` 的 `tasks.status=running`，查询失败降级 `[]`。
- 待批准协议、remember 和失败回滚不改。
- 已完成来自 Feed `kind=task`，最多 5 条；提醒和其它事件留在动态。
- 不做 C：跟进/忽略、Notify/Question/Review 分级均不实装。
- 每个生产改动必须先写失败测试并确认 RED。

## File Structure

**Modify:**

- `sidecar/src/yibao_brain/server.py` — 查询并归一化 running tasks，扩展 feed 回包。
- `sidecar/tests/test_feed.py` — running tasks 过滤、排序、归一化与失败降级集成测试。
- `app/src/lib/brain.ts` — `RunningTask` 与 `FeedResponse.running_tasks` 类型契约。
- `app/src/components/HomeFeed.vue` — 三区布局、Feed 分流、刷新时机和样式。
- `app/tests/task-inbox-ui.test.mjs` — 前端类型与三区结构测试。

**No Rust changes:** `app/src-tauri/src/lib.rs` 已把 sidecar 的 feed JSON 整体转发为 `brain-feed`。

---

### Task 1: sidecar 在 feed 回包中附带进行中任务

**Files:**

- Modify: `sidecar/src/yibao_brain/server.py`（`_feed_stats` 与 `rtype == "feed"`）
- Modify: `sidecar/tests/test_feed.py`

- [ ] **Step 1: Write the failing integration tests**

在 `sidecar/tests/test_feed.py` 顶部增加 `sqlite3`，并在 serve_async 集成测试前加入 helper 与两个测试：

```python
import sqlite3


def _seed_agent_tasks(root, rows):
    path = root / "plugins" / "agents" / "data.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE tasks ("
        "id TEXT PRIMARY KEY, kind TEXT, agent TEXT, prompt TEXT, "
        "status TEXT, created_at INTEGER)"
    )
    conn.executemany(
        "INSERT INTO tasks (id, kind, agent, prompt, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def test_serve_async_feed_includes_only_running_tasks_in_desc_order(tmp_path, monkeypatch):
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path))
    _seed_agent_tasks(tmp_path, [
        ("old", "agent", "claude", "旧任务", "running", 10),
        ("done", "agent", "codex", "已完成", "done", 20),
        ("new", "script", "python", "新脚本", "running", 30),
    ])
    out = []
    _run_async(serve_async(
        make_reader([{"type": "feed"}]),
        out.append,
        use_real=False,
        db_path=str(tmp_path / "a.db"),
        provider=FakeProvider(),
    ))
    feed_msg = next(m for m in out if m["type"] == "feed")
    assert feed_msg["running_tasks"] == [
        {
            "id": "new", "kind": "script", "label": "沙箱脚本",
            "prompt": "新脚本", "status": "running", "created_at": 30,
        },
        {
            "id": "old", "kind": "agent", "label": "claude 任务",
            "prompt": "旧任务", "status": "running", "created_at": 10,
        },
    ]
    assert feed_msg["stats"]["running_tasks"] == 2


def test_serve_async_feed_running_task_query_failure_degrades_to_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path))
    _seed_agent_tasks(tmp_path, [])

    def fail_query(*_args, **_kwargs):
        raise sqlite3.OperationalError("broken tasks")

    monkeypatch.setattr("yibao_brain.plugindb.PluginDb.query", fail_query)
    out = []
    _run_async(serve_async(
        make_reader([{"type": "feed"}]),
        out.append,
        use_real=False,
        db_path=str(tmp_path / "a.db"),
        provider=FakeProvider(),
    ))
    feed_msg = next(m for m in out if m["type"] == "feed")
    assert feed_msg["running_tasks"] == []
    assert feed_msg["stats"]["running_tasks"] == 0
```

同时把现有 `test_serve_async_feed_query_empty` 的最后断言补成：

```python
assert feeds[0]["running_tasks"] == []
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
cd sidecar && uv run --extra dev pytest tests/test_feed.py -q
```

Expected: 新测试因 feed 回包没有 `running_tasks` 失败；不是 fixture、表结构或导入错误。

- [ ] **Step 3: Implement `_running_tasks` and reuse it for stats**

在 `sidecar/src/yibao_brain/server.py` 的 FeedStore 初始化后、`_feed_stats` 前加入：

```python
    def _running_tasks(limit: int = 20) -> list[dict]:
        """读取 agents 权威任务表，只返回 Home 需要的 running 摘要。"""
        adb_file = os.path.join(plugin_data_dir("agents"), "data.db")
        if not os.path.exists(adb_file):
            return []
        try:
            from .plugindb import PluginDb

            adb = PluginDb("agents")
            try:
                rows = adb.query(
                    "tasks", where={"status": "running"},
                    order="created_at DESC", limit=limit,
                )
            finally:
                adb.close()
        except Exception as e:
            print(f"[yibao] 进行中任务查询失败（已降级为空）：{e}", file=sys.stderr)
            return []

        out = []
        for row in rows:
            task_id = str(row.get("id") or "")
            if not task_id:
                continue
            kind = "script" if row.get("kind") == "script" else "agent"
            agent_name = str(row.get("agent") or "智能体")
            out.append({
                "id": task_id,
                "kind": kind,
                "label": "沙箱脚本" if kind == "script" else f"{agent_name} 任务",
                "prompt": str(row.get("prompt") or ""),
                "status": "running",
                "created_at": int(row.get("created_at") or 0),
            })
        return out
```

把 `_feed_stats` 改为接收已查出的列表，删除它内部再次打开 agents DB 的逻辑：

```python
    def _feed_stats(running_tasks: list[dict] | None = None) -> dict:
        """主屏问候统计：待办提醒 / 进行中任务 / 近 24h 完成任务。"""
        stats = {"pending_reminders": 0, "running_tasks": 0, "done_24h": 0}
        rstore = getattr(agent, "reminder_store", None)
        if rstore is not None:
            try:
                stats["pending_reminders"] = len(rstore.list_pending())
            except Exception:
                pass
        stats["running_tasks"] = len(running_tasks or [])
        stats["done_24h"] = feed.count_since("task", time.time() - 86400)
        stats["unread"] = feed.count_unread()
        return stats
```

把 feed 分发改为一次查询、同时供列表和统计使用：

```python
        elif rtype == "feed":
            try:
                limit = int(msg.get("limit") or 60)
            except (TypeError, ValueError):
                limit = 60
            running_tasks = _running_tasks()
            write_msg({
                "type": "feed",
                "items": feed.recent(limit=limit),
                "stats": _feed_stats(running_tasks),
                "running_tasks": running_tasks,
            })
```

- [ ] **Step 4: Run focused and server regression tests**

Run:

```bash
cd sidecar && uv run --extra dev pytest tests/test_feed.py tests/test_server.py -q
```

Expected: PASS；现有 feed stats 四键不变，新 `running_tasks` 独立存在。

- [ ] **Step 5: Commit**

```bash
git add sidecar/src/yibao_brain/server.py sidecar/tests/test_feed.py
git commit -m "feat(inbox): feed 返回进行中任务"
```

---

### Task 2: TypeScript 固化 running task 契约

**Files:**

- Modify: `app/src/lib/brain.ts`
- Create: `app/tests/task-inbox-ui.test.mjs`

- [ ] **Step 1: Write the failing contract test**

创建 `app/tests/task-inbox-ui.test.mjs`：

```js
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const brainSource = await readFile(new URL("../src/lib/brain.ts", import.meta.url), "utf8");
const homeSource = await readFile(new URL("../src/components/HomeFeed.vue", import.meta.url), "utf8");

test("feed response carries normalized running tasks", () => {
  assert.match(brainSource, /export interface RunningTask/);
  assert.match(brainSource, /running_tasks:\s*RunningTask\[\]/);
  assert.match(brainSource, /running_tasks:\s*\[\]/);
});
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
cd app && node --test tests/task-inbox-ui.test.mjs
```

Expected: FAIL at `export interface RunningTask` because the contract does not exist.

- [ ] **Step 3: Add the TypeScript contract and empty fallback**

在 `FeedItem` 后加入：

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

扩展 `FeedResponse` 和 `EMPTY_FEED`：

```ts
export interface FeedResponse {
  items: FeedItem[];
  stats: FeedStats;
  running_tasks: RunningTask[];
}

const EMPTY_FEED: FeedResponse = {
  items: [],
  stats: { pending_reminders: 0, running_tasks: 0, done_24h: 0, unread: 0 },
  running_tasks: [],
};
```

- [ ] **Step 4: Run contract test and type check**

Run:

```bash
cd app && node --test tests/task-inbox-ui.test.mjs && npx vue-tsc --noEmit
```

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add app/src/lib/brain.ts app/tests/task-inbox-ui.test.mjs
git commit -m "feat(brain): 增加进行中任务类型"
```

---

### Task 3: HomeFeed 渲染纵向三区并去重动态

**Files:**

- Modify: `app/src/components/HomeFeed.vue`
- Modify: `app/tests/task-inbox-ui.test.mjs`

- [ ] **Step 1: Append failing UI structure tests**

向 `app/tests/task-inbox-ui.test.mjs` 追加：

```js
test("home inbox renders running pending and completed zones", () => {
  assert.match(homeSource, /const runningTasks = ref<RunningTask\[\]>/);
  assert.match(homeSource, />进行中/);
  assert.match(homeSource, />待批准/);
  assert.match(homeSource, />已完成/);
  assert.match(homeSource, /panelAction\("agents\.task_list"/);
});

test("completed tasks leave the generic activity stream", () => {
  assert.match(homeSource, /completedTasks\s*=\s*computed[\s\S]*kind === "task"[\s\S]*slice\(0, 5\)/);
  assert.match(homeSource, /activityItems\s*=\s*computed[\s\S]*kind !== "task"/);
  assert.match(homeSource, /v-for="it in activityItems"/);
  assert.match(homeSource, /v-for="it in completedTasks"/);
});

test("home refreshes task inbox when agent tasks start or finish", () => {
  assert.match(homeSource, /e\.kind === "reminder"/);
  assert.match(homeSource, /e\.kind === "action_result"[\s\S]*startsWith\("agents\."\)/);
  assert.match(homeSource, /fetchFeed\(\)/);
});
```

- [ ] **Step 2: Run the UI test and verify RED**

Run:

```bash
cd app && node --test tests/task-inbox-ui.test.mjs
```

Expected: contract test PASS，三个新 UI 测试因 running ref、三区和分流不存在而 FAIL。

- [ ] **Step 3: Add running/completed/activity state and helpers**

在 `HomeFeed.vue` 的 brain imports 加 `type RunningTask`，Feed 状态区改为：

```ts
const items = ref<FeedItem[]>([]);
const runningTasks = ref<RunningTask[]>([]);
const loaded = ref(false);
const completedTasks = computed(() => items.value.filter((it) => it.kind === "task").slice(0, 5));
const activityItems = computed(() => items.value.filter((it) => it.kind !== "task"));

async function reload() {
  const r = await getFeedOnce();
  items.value = r.items;
  stats.value = r.stats;
  runningTasks.value = r.running_tasks ?? [];
  loaded.value = true;
}
```

在 `approvals` 声明之后加入总显隐派生，避免任务收件箱全空时渲染空壳：

```ts
const hasInbox = computed(() =>
  runningTasks.value.length > 0 || approvals.value.length > 0 || completedTasks.value.length > 0,
);
```

加入展示 helper：

```ts
function elapsedSince(ts: number): string {
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (seconds < 60) return "刚开始";
  if (seconds < 3600) return `已运行 ${Math.floor(seconds / 60)} 分钟`;
  return `已运行 ${Math.floor(seconds / 3600)} 小时`;
}

function taskStatusLabel(it: FeedItem): string {
  const status = String(it.meta?.status ?? "done");
  return ({ done: "完成", failed: "失败", stopped: "已停止", interrupted: "已中断" } as Record<string, string>)[status]
    ?? "已结束";
}

function taskStatus(it: FeedItem): string {
  return String(it.meta?.status ?? "done");
}

function openTasks() {
  void panelAction("agents.task_list", {}, undefined, "panel:agents").catch(() => {});
}
```

把 `onFeed` 赋值补齐：

```ts
  unFeed = await onFeed((r) => {
    items.value = r.items;
    stats.value = r.stats;
    runningTasks.value = r.running_tasks ?? [];
  });
```

把事件刷新改成同一个防抖入口：

```ts
function scheduleFeedRefresh() {
  if (refetchTimer !== null) clearTimeout(refetchTimer);
  refetchTimer = setTimeout(() => {
    void fetchFeed().catch(() => {});
    void fetchWidgets().catch(() => {});
  }, 800);
}

// onMounted 内：
  unEvent = await onBrainEvent((e) => {
    const agentChanged = e.kind === "action_result"
      && !!e.action?.skill_id?.startsWith("agents.");
    if (e.kind === "reminder" || agentChanged) scheduleFeedRefresh();
  });
```

- [ ] **Step 4: Replace the top approval section with the three-zone inbox**

把当前滚动区顶部的 approvals `<section>` 替换为以下结构；其中待批准卡片内部继续使用现有选择、remember 与按钮绑定：

```vue
      <section v-if="hasInbox" class="sec sec-inbox">
        <div class="sec-title inbox-title">任务收件箱</div>

        <div v-if="runningTasks.length" class="inbox-zone zone-running">
          <div class="zone-title">进行中 · {{ runningTasks.length }}</div>
          <button
            v-for="task in runningTasks"
            :key="task.id"
            class="task-row running-row"
            @click="openTasks"
          >
            <span class="task-dot running"></span>
            <span class="task-main">
              <strong>{{ task.label }}</strong>
              <span>{{ task.prompt }}</span>
            </span>
            <span class="task-time">{{ elapsedSince(task.created_at) }}</span>
            <span class="task-go">查看 ›</span>
          </button>
        </div>

        <div v-if="approvals.length" class="inbox-zone zone-approvals sec-approvals">
          <div class="zone-title inbox-head">
            <span>待批准 · {{ approvals.length }}</span>
            <div v-if="approvals.length > 1" class="batch-btns">
              <button class="batch-no" :disabled="selectedCount === 0" @click="batchDecide(false)">
                全部拒绝{{ selectedCount ? ` (${selectedCount})` : "" }}
              </button>
              <button class="batch-yes" :disabled="selectedCount === 0" @click="batchDecide(true)">
                全部批准{{ selectedCount ? ` (${selectedCount})` : "" }}
              </button>
            </div>
          </div>
          <div
            v-for="p in approvals"
            :key="p.id"
            class="a-card"
            :class="{ selected: approvals.length > 1 && isSelected(p.id) }"
          >
            <label v-if="approvals.length > 1" class="a-check" title="选中后可一键批量">
              <input type="checkbox" :checked="isSelected(p.id)" @change="onToggleSelect(p.id, $event)" />
            </label>
            <div class="a-info">
              <span class="a-label">🔐 {{ p.label || p.skill }}</span>
              <span class="a-desc">{{ p.desc || p.skill }}</span>
            </div>
            <label class="a-remember" title="勾选后该技能在本会话内不再询问">
              <input type="checkbox" :checked="rememberOf(p.id)" @change="onToggleRemember(p.id, $event)" />
              <span>记住</span>
            </label>
            <div class="a-btns">
              <button class="a-no" @click="decideApproval(p, false)">拒绝</button>
              <button class="a-yes" @click="decideApproval(p, true)">批准</button>
            </div>
          </div>
        </div>

        <div v-if="completedTasks.length" class="inbox-zone zone-completed">
          <div class="zone-title">已完成 · 最近 {{ completedTasks.length }} 条</div>
          <button
            v-for="it in completedTasks"
            :key="it.id"
            class="task-row completed-row"
            :class="{ unread: it.read === 0 }"
            @click="openInChat(it)"
          >
            <span class="task-status" :class="`status-${taskStatus(it)}`">{{ taskStatusLabel(it) }}</span>
            <span class="task-main"><span>{{ it.text }}</span></span>
            <span class="task-time">{{ relTime(it.ts) }}</span>
          </button>
        </div>
      </section>
```

把动态区的空态和循环改为 `activityItems`：

```vue
        <div v-if="loaded && !activityItems.length" class="f-empty">
          还没有其他动态——提醒、记忆和主动消息会出现在这里
        </div>
        <button
          v-for="it in activityItems"
          :key="it.id"
          class="f-row"
          :class="{ unread: it.read === 0 }"
          @click="openInChat(it)"
        >
          <span class="f-icon">{{ kindIcon(it) }}</span>
          <span class="f-text">{{ it.text }}</span>
          <span class="f-time">{{ relTime(it.ts) }}</span>
        </button>
```

- [ ] **Step 5: Add inbox styles**

在 `.sec` 后加入以下样式；保留现有 approval、Feed、Widget 和 Dock 样式：

```css
.sec-inbox {
  padding: var(--yb-space-2);
  border: 1px solid var(--yb-surface-border);
  border-radius: 16px;
  background: color-mix(in srgb, var(--yb-surface-solid) 88%, transparent);
  box-shadow: var(--yb-shadow-soft);
}
.inbox-title {
  color: var(--yb-text);
  font-size: var(--yb-fs-md);
}
.inbox-zone + .inbox-zone {
  margin-top: var(--yb-space-3);
  padding-top: var(--yb-space-3);
  border-top: 1px solid var(--yb-surface-border);
}
.zone-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--yb-space-2);
  padding: 0 var(--yb-space-2) var(--yb-space-2);
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-sm);
  font-weight: 600;
}
.task-row {
  display: flex;
  align-items: center;
  gap: var(--yb-space-2);
  width: 100%;
  padding: var(--yb-space-2) var(--yb-space-3);
  margin-bottom: var(--yb-space-2);
  border: 1px solid var(--yb-surface-border);
  border-radius: 12px;
  background: var(--yb-surface-solid);
  color: var(--yb-text);
  cursor: pointer;
  font: inherit;
  text-align: left;
}
.task-row:hover {
  border-color: var(--yb-accent);
}
.task-row.unread {
  border-color: var(--yb-accent);
  background: var(--yb-accent-soft);
}
.task-main {
  min-width: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.task-main strong,
.task-main span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.task-main span {
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-sm);
}
.task-time,
.task-go {
  flex-shrink: 0;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-sm);
}
.task-dot {
  width: 8px;
  height: 8px;
  flex-shrink: 0;
  border-radius: 50%;
}
.task-dot.running {
  background: var(--yb-accent);
  box-shadow: 0 0 0 4px var(--yb-accent-soft);
}
.task-status {
  flex-shrink: 0;
  padding: 2px 7px;
  border-radius: var(--yb-radius-lg);
  background: var(--yb-btn-neutral);
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-sm);
}
.status-done {
  color: var(--yb-state-success);
}
.status-failed {
  color: var(--yb-danger);
}
```

- [ ] **Step 6: Run focused frontend verification**

Run:

```bash
cd app && node --test tests/task-inbox-ui.test.mjs tests/inbox-ui.test.mjs && npx vue-tsc --noEmit && npm run build
```

Expected: 三区与 Inbox A 测试全部 PASS；type check 和 Vite build exit 0。

- [ ] **Step 7: Commit**

```bash
git add app/src/components/HomeFeed.vue app/tests/task-inbox-ui.test.mjs
git commit -m "feat(home): 任务收件箱统一为三区"
```

---

### Task 4: Full verification and handoff

**Files:** no production changes expected.

- [ ] **Step 1: Run all sidecar tests**

```bash
cd sidecar && uv run --extra dev pytest -q
```

Expected: all tests PASS with zero failures.

- [ ] **Step 2: Run all frontend checks**

```bash
cd app && node --test tests/*.test.mjs && npx vue-tsc --noEmit && npm run build
```

Expected: all Node tests PASS; type check and build exit 0.

- [ ] **Step 3: Run Rust checks**

```bash
cargo check --manifest-path app/src-tauri/Cargo.toml
cargo test --manifest-path app/src-tauri/Cargo.toml
```

Expected: both commands exit 0.

- [ ] **Step 4: Verify diff and repository state**

```bash
git diff --check
git status --short --branch
git log --oneline -6
```

Expected: no uncommitted production/test changes; branch contains the spec, plan, sidecar contract, TypeScript contract, and Home UI commits.

- [ ] **Step 5: Minimal manual acceptance**

1. 派一个持续 30 秒以上的 agents 任务，Home 出现「进行中」。
2. 同时触发一条高风险确认，Home 同时出现「待批准」。
3. 批准并等待任务结束，运行中消失，结果进入「已完成」。
4. 确认该任务没有在下方「动态」重复出现。
