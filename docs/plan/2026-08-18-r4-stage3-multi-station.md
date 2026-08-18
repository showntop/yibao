# R4 阶段三：多工位（工位区 + 左栏会话列表 + 聚焦路由输入条）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** studio 面板从单会话视图升级为多工位工作台：左栏会话列表 + 1~3 并排工位 + 页底共享输入条按聚焦路由，窄窗自动单工位 + 抽屉。

**Architecture:** 全部在插件面板工程内（`plugins/coding/panel/`）：新增 stations 布局 store（纯逻辑：工位/绑定/聚焦/sid→工位路由表/未绑 sid 状态派生）；现 `App.vue` 的会话接线整体下沉为 `StationView.vue`（每工位一个实例、各持一个 session store，接线代码基本原样搬运）；新 `App.vue` 壳负责布局/onInit demux/attach 路由/左栏数据/自动回放主工位；共享输入条用「每工位各持一个 Composer 实例、聚焦者的 footer 经 CSS 停靠页底」兑现（草稿/IME/接线零改动）。takeover 转发层按 spec 退役（app 侧 + 面板侧）。后端零改动。

**Tech Stack:** Vue 3 `<script setup lang="ts">` + vitest（面板工程）；app 侧 Vue + vitest；无新增依赖。

**spec:** `docs/superpowers/specs/2026-08-17-coding-studio-r4-design.md`（落地顺序第 3 步）
**前序:** 阶段一/二已终审通过（分支 `feat/r4-module-panel-runtime`，基线 commit `7731b5b`）

## Global Constraints

- 施工目录：worktree `/Users/denny/Work/yibao/.worktrees/r4-stage1`，**绝不碰主检出** `/Users/denny/Work/yibao`。
- 注释/文案中文、全角标点（，：（）），与所在文件既有风格一致。
- 每工位一个 session store 实例（`createSessionStore` 工厂本就为此设计，见 session.ts:1-3 头注）。
- **demux 裁定（阶段二终审预警 #2 关闭）**：事件在 shell 按 sid 分拣后再进 per-station store，单 store 内事件序与单工位完全一致 → 现行 `lastToolCard` 切断规则（text_delta/user_msg 也切断）安全，**不改**。
- **未绑 sid 不做全量事件缓冲**：`_stream` 每条事件落 messages 表（coding.py:195 `_persist`），绑定时 `resumeSession` 拉全量历史即等价回放对齐；只需派生轻量状态（running/waiting/idle）供左栏。这是对 spec「per-sid 缓冲」条目的简化裁定，依据 = DB 落盘即缓冲。
- 左栏数据源用 quiet 别名 `coding.sessions`（`coding.list` 本体带 panel 事件，面板内周期刷新会制造噪音；InputBar.vue:71-72 已有同例）。
- 后端（sidecar/coding.py/api.toml）**零改动**；app 侧只动 takeover 退役相关文件。
- 不新增组件 DOM 测试基建（阶段二既定缺口，保持惯例）；逻辑全在 store 层用 vitest 覆盖。
- 闸门（每个 Task 末必跑，全绿才 commit）：
  - `cd plugins/coding/panel && pnpm test && pnpm build && pnpm typecheck`（基线 126 绿）
  - takeover 任务加：`cd app && pnpm test && pnpm build`（基线 115 绿）
- commit message：中文，`<type>: <摘要>` 格式对齐 git log 既有风格（feat/fix/chore/refactor）。

## 文件结构

**新建：**
- `plugins/coding/panel/src/stores/stations.ts` — 工位布局 store（T1）
- `plugins/coding/panel/src/stores/stations.test.ts` — 其测试（T1）
- `plugins/coding/panel/src/components/StationView.vue` — 单工位视图（T4，现 App.vue 接线下沉）
- `plugins/coding/panel/src/components/SessionRail.vue` — 左栏会话列表（T5，纯展示）

**重写/大改：**
- `plugins/coding/panel/src/App.vue` — 壳：布局 + demux + 左栏编排 + 窄窗自适应（T6）
- `plugins/coding/panel/src/style.css` — 工位布局 + 停靠 + 窄窗样式（T6）

**小改：**
- `plugins/coding/panel/src/stores/drivers.ts` — probe 模块级缓存（T2）
- `plugins/coding/panel/src/stores/drivers.test.ts` — 缓存用例（T2）
- `plugins/coding/panel/src/stores/session.ts` — takeover 退役、队列改名（T3）
- `plugins/coding/panel/src/stores/session.test.ts` — 同步改写（T3）
- `plugins/coding/panel/src/components/Composer.vue` — 头注一行（T3）

**app 侧（T7，takeover 退役）：**
- `app/src/components/PanelApp.vue` — 删 isCoding 分支/takeover-state 处理/submitBrain 逃生口/:takeover 传递
- `app/src/components/InputBar.vue` — 删 takeover prop 与 stopping 特例
- `app/src/components/WebviewPanel.vue` — 删 takeover prop 与 init 载荷字段
- `app/src/lib/at-mention.ts`、`app/src/shared/bridge.js` — 注释措辞

---

### Task 1: stations 布局 store（工位/绑定/聚焦/路由表/左栏状态派生）

**Files:**
- Create: `plugins/coding/panel/src/stores/stations.ts`
- Test: `plugins/coding/panel/src/stores/stations.test.ts`

**Interfaces:**
- Produces（后续任务依赖的确切签名）:
  - `MAX_STATIONS = 3`
  - `createStationsStore()` 返回 `{ state, addStation, removeStation, focus, bind, unbind, syncStationSid, stationForSid, bumpRail, pickBindTarget }`
  - `state: { stations: Station[]; focusId: number; railLive: Record<string, RailLive> }`
  - `interface Station { id: number; boundSid: string | null; boundAgent: string }`
  - `type RailLive = "running" | "waiting" | "idle"`
  - `addStation(): number | null`（满 3 返回 null；成功返回新工位 id 并聚焦）
  - `removeStation(id: number): boolean`（仅剩 1 个返回 false）
  - `bind(id, sid, agent)` / `unbind(id)` / `syncStationSid(id, sid: string | null, agent?: string)`
  - `stationForSid(sid: string): number | null`（demux 路由表查询）
  - `bumpRail(sid: string, kind: string): void`（仅未绑 sid 生效）
  - `pickBindTarget(): number`（加入工位的目标工位选择）

设计要点：
- `state.stations` 初始 **2 个**空工位（spec 默认 2 栏），id 自增（1,2,…），`focusId` 初始 = 1。
- `bind`：sid 已绑别的工位 → 先在那边 unbind（一个会话至多占一个工位）；绑定后清掉该 sid 的 railLive（绑上后状态由工位流直接可见）。
- `syncStationSid`：站内会话自愈同步（newChat/handoff/start 换 sid 后由 StationView watch 上报）：旧 sid 出路由表，新 sid 入表；`sid=null` 等价 unbind。
- `bumpRail` 仅对**未绑定**的 sid 生效（已绑 sid 的状态工位内自见）：`permission_request→waiting`；`done|stopped|error→idle`；其余流事件 kind →`running`。
- `pickBindTarget`：聚焦工位空 → 聚焦工位；否则首个空工位；都无 → 聚焦工位（换绑语义）。

- [ ] **Step 1: 写失败测试** `plugins/coding/panel/src/stores/stations.test.ts`

```ts
// stations 布局 store：工位增删/绑定/聚焦/路由表/左栏派生状态。
import { describe, expect, it } from "vitest";
import { createStationsStore, MAX_STATIONS } from "./stations";

describe("stations store", () => {
  it("初始 2 个空工位,聚焦 1 号", () => {
    const s = createStationsStore();
    expect(s.state.stations.map((x) => x.id)).toEqual([1, 2]);
    expect(s.state.stations.every((x) => x.boundSid === null)).toBe(true);
    expect(s.state.focusId).toBe(1);
  });

  it("addStation 聚焦新工位;满 3 返回 null(第 4 栏不可加)", () => {
    const s = createStationsStore();
    expect(s.addStation()).toBe(3);
    expect(s.state.focusId).toBe(3);
    expect(s.addStation()).toBe(null);
    expect(s.state.stations).toHaveLength(MAX_STATIONS);
  });

  it("removeStation 仅剩 1 个拒绝;删聚焦工位后聚焦落到首个", () => {
    const s = createStationsStore();
    expect(s.removeStation(2)).toBe(true);
    expect(s.state.stations.map((x) => x.id)).toEqual([1]);
    expect(s.removeStation(1)).toBe(false);
    s.addStation(); s.addStation(); // 1,2,3
    s.focus(3);
    expect(s.removeStation(3)).toBe(true);
    expect(s.state.focusId).toBe(1);
  });

  it("bind/unbind 维护路由表;同 sid 换工位先解旧绑", () => {
    const s = createStationsStore();
    s.bind(1, "a", "codex");
    expect(s.stationForSid("a")).toBe(1);
    expect(s.state.stations[0]!.boundAgent).toBe("codex");
    s.bind(2, "a", "codex"); // 同 sid 绑到 2 号
    expect(s.stationForSid("a")).toBe(2);
    expect(s.state.stations[0]!.boundSid).toBe(null);
    s.unbind(2);
    expect(s.stationForSid("a")).toBe(null);
  });

  it("syncStationSid:站内换 sid 更新路由表;null 等价 unbind", () => {
    const s = createStationsStore();
    s.bind(1, "a", "claude-code");
    s.syncStationSid(1, "b", "codex");
    expect(s.stationForSid("a")).toBe(null);
    expect(s.stationForSid("b")).toBe(1);
    expect(s.state.stations[0]!.boundAgent).toBe("codex");
    s.syncStationSid(1, null);
    expect(s.stationForSid("b")).toBe(null);
    expect(s.state.stations[0]!.boundSid).toBe(null);
  });

  it("bumpRail 仅未绑 sid 生效:perm→waiting,终态→idle,其余流事件→running", () => {
    const s = createStationsStore();
    s.bumpRail("x", "text_delta");
    expect(s.state.railLive["x"]).toBe("running");
    s.bumpRail("x", "permission_request");
    expect(s.state.railLive["x"]).toBe("waiting");
    s.bumpRail("x", "permission_done");
    expect(s.state.railLive["x"]).toBe("running");
    s.bumpRail("x", "done");
    expect(s.state.railLive["x"]).toBe("idle");
    s.bind(1, "x", "claude-code"); // 绑定后派生状态清除且不再受理
    expect(s.state.railLive["x"]).toBeUndefined();
    s.bumpRail("x", "text_delta");
    expect(s.state.railLive["x"]).toBeUndefined();
  });

  it("pickBindTarget:聚焦空→聚焦;否则首个空;都无→聚焦(换绑)", () => {
    const s = createStationsStore();
    expect(s.pickBindTarget()).toBe(1); // 聚焦 1 空
    s.bind(1, "a", "claude-code");
    expect(s.pickBindTarget()).toBe(2); // 首个空
    s.bind(2, "b", "claude-code");
    expect(s.pickBindTarget()).toBe(1); // 满员 → 聚焦工位换绑
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd plugins/coding/panel && pnpm vitest run src/stores/stations.test.ts`
Expected: FAIL（`./stations` 不存在）

- [ ] **Step 3: 实现** `plugins/coding/panel/src/stores/stations.ts`

```ts
// 工位布局 store(R4 阶段三):工位增删/绑定/聚焦 + sid→工位路由表(demux 用)
// + 未绑工位 sid 的左栏派生活体(running/waiting/idle)。
// 未绑 sid 不缓冲全量事件:后端 _stream 逐条落 messages 表(coding.py _persist),
// 绑定时 resumeSession 拉全量历史即等价回放对齐——此处只需状态派生供左栏。
import { reactive } from "vue";

export const MAX_STATIONS = 3;

export interface Station { id: number; boundSid: string | null; boundAgent: string }
export type RailLive = "running" | "waiting" | "idle";

export interface StationsState {
  stations: Station[];
  focusId: number;
  railLive: Record<string, RailLive>;
}

export function createStationsStore() {
  const state = reactive<StationsState>({
    stations: [
      { id: 1, boundSid: null, boundAgent: "claude-code" },
      { id: 2, boundSid: null, boundAgent: "claude-code" },
    ],
    focusId: 1,
    railLive: {},
  });
  let seq = 2;

  const byId = (id: number) => state.stations.find((s) => s.id === id) ?? null;

  function focus(id: number) { if (byId(id)) state.focusId = id; }

  function addStation(): number | null {
    if (state.stations.length >= MAX_STATIONS) return null; // 第 4 栏不可加
    const id = ++seq;
    state.stations.push({ id, boundSid: null, boundAgent: "claude-code" });
    state.focusId = id;
    return id;
  }

  function removeStation(id: number): boolean {
    if (state.stations.length <= 1) return false;
    unbind(id);
    state.stations.splice(state.stations.findIndex((s) => s.id === id), 1);
    if (state.focusId === id) state.focusId = state.stations[0]!.id;
    return true;
  }

  function stationForSid(sid: string): number | null {
    for (const s of state.stations) if (s.boundSid === sid) return s.id;
    return null;
  }

  function unbind(id: number) {
    const st = byId(id);
    if (st) { st.boundSid = null; }
  }

  function bind(id: number, sid: string, agent: string) {
    const prev = stationForSid(sid);
    if (prev !== null && prev !== id) unbind(prev); // 一个会话至多占一个工位
    const st = byId(id);
    if (!st) return;
    st.boundSid = sid;
    st.boundAgent = agent;
    delete state.railLive[sid]; // 绑上后状态由工位流直接可见
  }

  /** 站内会话自愈同步(newChat/handoff/start 换 sid 后 StationView watch 上报) */
  function syncStationSid(id: number, sid: string | null, agent?: string) {
    const st = byId(id);
    if (!st) return;
    st.boundSid = sid;
    if (sid && agent) st.boundAgent = agent;
  }

  /** 未绑工位 sid 的左栏派生活体;已绑 sid 不受理(工位流内自见) */
  function bumpRail(sid: string, kind: string) {
    if (stationForSid(sid) !== null) return;
    if (kind === "permission_request") state.railLive[sid] = "waiting";
    else if (kind === "done" || kind === "stopped" || kind === "error") state.railLive[sid] = "idle";
    else state.railLive[sid] = "running";
  }

  /** 加入工位目标:聚焦工位空→聚焦;否则首个空;都无→聚焦(换绑) */
  function pickBindTarget(): number {
    const focused = byId(state.focusId);
    if (focused && !focused.boundSid) return focused.id;
    const empty = state.stations.find((s) => !s.boundSid);
    return (empty ?? focused ?? state.stations[0]!).id;
  }

  return { state, focus, addStation, removeStation, bind, unbind, syncStationSid, stationForSid, bumpRail, pickBindTarget };
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd plugins/coding/panel && pnpm vitest run src/stores/stations.test.ts`
Expected: PASS（7 个用例）

- [ ] **Step 5: 闸门 + Commit**

Run: `cd plugins/coding/panel && pnpm test && pnpm build && pnpm typecheck`
Expected: 126+7 绿，build/typecheck 干净

```bash
git add plugins/coding/panel/src/stores/stations.ts plugins/coding/panel/src/stores/stations.test.ts docs/plan/2026-08-18-r4-stage3-multi-station.md
git commit -m "feat: 多工位布局 store——工位/绑定/聚焦/路由表/左栏状态派生(R4 阶段三 T1)"
```

---

### Task 2: drivers 探针模块级缓存（多工位各持 drivers store，探测只跑一次）

**Files:**
- Modify: `plugins/coding/panel/src/stores/drivers.ts`
- Test: `plugins/coding/panel/src/stores/drivers.test.ts`

**Interfaces:**
- Consumes: 现有 `createDriversStore(deps)`（签名不变）
- Produces: 不变（`{ state, probe, setCurAgent, applyCwdDefault }` + 既有 `normAgent/agentLabel`）；新增仅内部缓存与 `_resetProbeCacheForTest()`

每工位一个 `createDriversStore`（`curAgent` 是工位态），但 `coding.drivers` 探测结果全局共享：模块级缓存 probe 的 promise；失败清缓存允许下次重试。

- [ ] **Step 1: 追加失败用例**（drivers.test.ts 末尾）

```ts
  it("probe 模块级缓存:两个 store 只探测一次;失败清缓存允许重试", async () => {
    let calls = 0;
    const mk = () =>
      createDriversStore({
        invoke: async () => {
          calls++;
          return { drivers: [{ id: "codex", available: true }] };
        },
      });
    _resetProbeCacheForTest();
    const a = mk();
    const b = mk();
    await Promise.all([a.probe(), b.probe()]);
    expect(calls).toBe(1);
    expect(a.state.codexAvailable).toBe(true);
    expect(b.state.codexAvailable).toBe(true);

    let fail = true;
    const c = createDriversStore({
      invoke: async () => {
        if (fail) throw new Error("探测失败");
        return { drivers: [] };
      },
    });
    _resetProbeCacheForTest();
    await c.probe();
    expect(c.state.codexAvailable).toBe(null); // 失败保持 null
    fail = false;
    await c.probe(); // 重试成功
    expect(c.state.codexAvailable).toBe(false); // 应答无 codex 项
  });
```

（import 行加 `_resetProbeCacheForTest`。）

- [ ] **Step 2: 跑确认失败**

Run: `cd plugins/coding/panel && pnpm vitest run src/stores/drivers.test.ts`
Expected: FAIL（`_resetProbeCacheForTest` 未导出）

- [ ] **Step 3: 实现**（drivers.ts 改动）

头注第 1 行后追加一行：
```ts
// 阶段三:probe 模块级缓存——多工位各持本 store(curAgent 是工位态),探测只跑一次;失败清缓存可重试。
```

`createDriversStore` 前加模块级缓存：
```ts
// 探测结果全局共享(codex 装没装与工位无关):缓存进行中的 promise,成功恒定复用,失败清缓存下次重试
let probeCache: Promise<{ drivers?: DriverInfo[] } | null> | null = null;
function probeOnce(invoke: DriversDeps["invoke"]): Promise<{ drivers?: DriverInfo[] } | null> {
  if (!probeCache) {
    probeCache = (invoke("coding.drivers", {}) as Promise<{ drivers?: DriverInfo[] }>).catch(() => {
      probeCache = null; // 失败不留缓存,下个 store 的 probe 重试
      return null;
    });
  }
  return probeCache;
}
/** 测试钩子:隔离用例间的探针缓存 */
export function _resetProbeCacheForTest() { probeCache = null; }
```

`probe()` 改为：
```ts
  async function probe(): Promise<void> {
    const r = await probeOnce(deps.invoke);
    if (r === null) return; // 探测失败静默:保持 null,chip 按可用呈现
    const list = (r && r.drivers) || [];
    let found = false;
    for (const d of list) {
      if (d && d.id === "codex") { state.codexAvailable = !!d.available; found = true; break; }
    }
    if (!found) state.codexAvailable = false; // 应答里查无 codex 项同样视为不可用
    enforceFallback();
  }
```

注意：既有用例若跨 `it` 依赖独立探测，需在各 `it` 开头调 `_resetProbeCacheForTest()`——检查 drivers.test.ts 现有用例并补齐。

- [ ] **Step 4: 跑确认通过**

Run: `cd plugins/coding/panel && pnpm vitest run src/stores/drivers.test.ts`
Expected: PASS

- [ ] **Step 5: 闸门 + Commit**

Run: `cd plugins/coding/panel && pnpm test && pnpm build && pnpm typecheck`

```bash
git add plugins/coding/panel/src/stores/drivers.ts plugins/coding/panel/src/stores/drivers.test.ts
git commit -m "feat: drivers 探针模块级缓存——多工位共享探测结果(R4 阶段三 T2)"
```

---

### Task 3: session store takeover 退役 + 排队改名 queueInput

**Files:**
- Modify: `plugins/coding/panel/src/stores/session.ts`
- Modify: `plugins/coding/panel/src/stores/session.test.ts`
- Modify: `plugins/coding/panel/src/components/Composer.vue`（头注一行）

**Interfaces:**
- Consumes: 无
- Produces（T4 StationView 依赖）:
  - `queueInput(text: string, refs: string[], cwd: string, mode: string, agent: string): { queued: boolean }`（busy 入队/空闲直发，语义=原 takeoverInput）
  - `setQueueContext(ctx)` 不变
  - **删除**：`takeoverInput`/`takeoverStop`/`clearTakeoverQueue`/`report` dep/`ReportState` 类型
  - 其余 API 原样（`state/handleData/applyEvent/send/stop/newChat/resumeSession/handoffSend/startHandoffSession/pushHandoffCard/dropHandoffCard/historyToItems/isResuming/_test`）

spec 依据：输入条节「takeover-input 转发层（PanelApp.vue:291）随之退役」。takeover-state 上报随之作废（report dep 唯一用途）。队列机制保留并改名：阶段三共享输入条 busy 排队（Codex Queue 式）复用同一机制。

- [ ] **Step 1: 改测试**（session.test.ts）

- `describe("takeover", ...)` → `describe("queueInput(busy 排队)", ...)`；块内 `s.takeoverInput(...)` → `s.queueInput(...)`，断言 `{ queued }` 不变。
- 删「takeover-stop 调 stop」段（`takeoverStop` 移除）；该用例的入队/泄放断言保留。
- 删「clearTakeoverQueue」用例（清单 11）。
- 删「report 按状态流转上报」用例；所有 `createSessionStore({ report: ..., ... })` 测试夹具删 `report` 字段。
- 全文件 `takeover` 注释措辞改「队列」。

关键保留断言（排队→终态泄放、stopped 清空、跨引擎守卫泄放路径、火忘不外抛）逐字保留，只换方法名。

- [ ] **Step 2: 跑确认失败**

Run: `cd plugins/coding/panel && pnpm vitest run src/stores/session.test.ts`
Expected: FAIL（`queueInput` 不存在 / `report` 多余不报错但 takeoverInput 已删）

- [ ] **Step 3: 实现**（session.ts）

1. 头注 takeover 措辞改队列：
```ts
// 会话 store:事件→渲染模型归约器 + 发送状态机。单工位对外暴露一个「当前会话」视图,
// 事件按 sid 过滤(只受理当前会话,陈旧 sid 的流丢弃——无内部分槽);阶段三多工位在
// 壳层多实例化本 store + 壳做事件 demux 分发。
// 行为对齐 chat.html:气泡切断规则、工具卡配对(lastToolCard)、兜底用户气泡原地升级、
// pendingTurnEnded 秒败竞态、resumeSession 的 discarded 解锁、busy 排队泄放(T9:泄放路径
// 补跨引擎交接守卫,对齐原 send() 内联分支)、
// handoffSend 跨引擎交接(:1964-1991)、startHandoffSession Codex→CC(:2268-2319)。
// 阶段三:takeover 退役(spec 输入条节)——report/takeoverStop/clearTakeoverQueue 删除,
// takeoverInput 改名 queueInput(共享输入条 busy 排队复用同一机制)。
```
2. `SessionDeps`：删 `report` 字段与 `ReportState` 导出；删 `onQueueHandoff` 注释里「T9」无碍保留。
3. 全文删 `deps.report(...)` 调用（`onSessionEnded`/`send`/`newChat`/`resumeSession`/`permission_request`/`permission_done`/`handoffSend`/`startHandoffSession` 内共约 12 处）。
4. `takeoverQueue` → `sendQueue`（含 `_test.getQueue` 内部，名字保留 getQueue）。
5. 方法段替换：
```ts
  // —— busy 排队(共享输入条在工位忙时入队,终态泄放;原 takeover 队列机制更名沿用)——
  function queueInput(text: string, refs: string[], cwd: string, mode: string, agent: string) {
    if (state.sending || state.streaming) {
      sendQueue.push({ text, refs });
      return { queued: true };
    }
    // 失败已由 state.error 承载,火忘路径不再外抛
    void send(cwd, text, mode, agent, { refs }).catch(() => {});
    return { queued: false };
  }
```
6. `drainTakeoverQueue` → `drainSendQueue`；`onSessionEnded` 的 `stopped` 分支注释改「中断即放弃排队意图」保留；`newChat` 清 `sendQueue`。
7. return 块：删 `takeoverInput, takeoverStop, clearTakeoverQueue,`，加 `queueInput,`；删 `ReportState` 相关导出。
8. Composer.vue:17 头注改：
```ts
// 输入不锁(busy 排队语义在 store.queueInput)。多工位:每工位一个本组件实例,聚焦者的 footer 经 CSS 停靠页底。
```

- [ ] **Step 4: 跑确认通过 + typecheck**

Run: `cd plugins/coding/panel && pnpm vitest run src/stores/session.test.ts && pnpm typecheck`
Expected: PASS（typecheck 会暴露 App.vue 仍在用旧 API——T4 才迁走，本任务同步把 App.vue 的 takeover 接线**暂时保留编译通过**：见下）

**注意**：session.ts 删 API 后 App.vue（旧壳）typecheck 会挂。本任务允许临时给 App.vue 打最小补丁保持编译（T4 整体替换它）：删 `report:` dep 行、`takeoverStop/clearTakeoverQueue` 调用点改成空操作注释标记 `// TODO(T4): 壳重写时移除`。不得改行为语义之外的代码。

- [ ] **Step 5: 闸门 + Commit**

Run: `cd plugins/coding/panel && pnpm test && pnpm build && pnpm typecheck`
Expected: 全绿（126±N：takeover 三用例删除、改名用例等量替换）

```bash
git add plugins/coding/panel/src/stores/session.ts plugins/coding/panel/src/stores/session.test.ts plugins/coding/panel/src/components/Composer.vue plugins/coding/panel/src/App.vue
git commit -m "refactor: session store takeover 退役、队列改名 queueInput(R4 阶段三 T3)"
```

---

### Task 4: StationView 抽取——现 App.vue 会话接线整体下沉

**Files:**
- Create: `plugins/coding/panel/src/components/StationView.vue`
- Modify: `plugins/coding/panel/src/App.vue`（本任务先缩减为「单 StationView 壳」保证可构建可验收；T6 再写成多工位壳）

**Interfaces:**
- Consumes: T1 stations store（本任务不用，T6 用）、T3 后的 session store API（`queueInput` 等）、T2 后的 drivers store
- Produces（T5/T6 壳依赖的确切接口）:
  - props: `{ focused: boolean; autoplay: boolean; defaultCwd: string }`
  - expose: `{ state, dockH, onData, bindSession, unbindSession, stop, isBusy }`（实装定稿：applyQueueInput 撤销——busy 排队并入 StationView 内部 onSend，入队即消费 composer）
    - `state` = session store 的 reactive state（壳读 busy/waiting/usage 做工位头/左栏）
    - `dockH: Ref<number>` = footer 实时高度（壳停靠布局用，RO 驱动）
    - `onData(data: PanelData): void` = 壳 demux 后投递（内调 `store.handleData`）
    - `bindSession(sid: string, agent: string): void` = 加入工位（内调 `store.resumeSession`，繁忙守卫：streaming/sending 中拒绝并 status 提示）
    - `unbindSession(): void` = 移出（内调 `store.newChat()`，繁忙守卫同上）
    - `applyQueueInput(text: string, refs: string[]): "sent" | "queued" | string` = 共享输入条发送入口（busy→queueInput 返 "queued"；空闲→走完整 onSend 校验/交接守卫；校验失败返提示文本）
    - `stop(): Promise<boolean>` = esc/中断（仅 streaming 受理）
    - `isBusy: ComputedRef<boolean>`
  - emits: `["sid-change", "request-focus"]`
    - `sid-change(sid: string | null, agent: string)`：watch `state.currentSession` 上报（壳更新路由表）
    - `request-focus()`：工位任意处 mousedown

设计要点（搬运纪律）：
- 现 App.vue `<script setup>` 的以下内容**逐字搬运**进 StationView（只改两处）：
  - store 创建块（42-56）：删 `report` dep（T3 已删此字段）；`onResumedCwd`/`onQueueHandoff` 闭包原样（它们引用本组件内的 cwd/switchAgent/drivers——天然工位 scoped，阶段二终审预警 #1 就此关闭）
  - takeover 相关**不搬**：40 行 `takeover` ref、75-95 onInit/onHostMessage  takeover 段、426-444 onTakeoverInput、83-86 body.takeover watch、模板 537 注释段（spec 退役）
  - onInit 保留但**剥离 takeover 标志读取**，改为调 expose 的 `onData` 内部实现（`store.handleData`）；onHostMessage 整段不搬（宿主消息通道随 takeover 退役无消费方）
  - 其余全部原样：drivers/头部控件状态/浮层互收/esc 优先级/成本/状态行/tip/cwd chip+浮层/refreshCwdState/autoReplay/prefillCwd/引擎 picker/mode pill/接续浮层/Codex→CC 交接/⏪/Composer 接线/RunPill 布局/RO/生命周期
- 两处必改：
  1. `onMounted` 的 `prefillCwd()` 仅在 `props.autoplay` 时调（多工位只有主工位自动回放）；非 autoplay 工位用 `props.defaultCwd` 预填 cwd（不回放）
  2. `watch(() => state.currentSession, ...)` 追加 `emit("sid-change", sid, state.curSessAgent)`
- 模板：现 `<header>` 改为工位头：左 = 会话标识（`state.currentSession ? "会话 " + state.currentSession.slice(0,8) : "新会话"`）+ 引擎徽标（`state.curSessAgent`）+ waiting/busy 状态点；右 = 成本 `#cost`、会话钮、新对话钮、移出 ✕（`@click="emit('remove-request', ...)"`——见下修订：**移出/关闭由壳发起**，工位头 ✕ emit `request-remove`，壳决定 unbind 或 removeStation）。
- esc/click-outside 文档级监听加 `if (!props.focused) return;` 守卫（多实例共存只有聚焦者响应）。
- HistoryOverlay/HandoffPicker 浮层留在工位内，CSS `position:absolute` 相对工位列（原相对全页，style.css T6 统一调）。
- `defineExpose` 按上表；`applyQueueInput` 内部复用现 `onSend` 逻辑（校验失败经 status 提示并返回提示文本）。

- [ ] **Step 1: 建 StationView.vue 并按上表搬运**（无组件测试基建，按惯例以 store 测试 + typecheck + 构建为闸）

- [ ] **Step 2: App.vue 临时单工位壳**（保证本任务独立可验收）：

```vue
<script setup lang="ts">
// coding:studio(R4 阶段三 T4 过渡壳):单 StationView 验证搬运无损;T6 重写为多工位壳。
import StationView from "./components/StationView.vue";
</script>

<template>
  <StationView :focused="true" :autoplay="true" default-cwd="" />
</template>
```

- [ ] **Step 3: 闸门**

Run: `cd plugins/coding/panel && pnpm test && pnpm build && pnpm typecheck`
Expected: 全绿（store 测试不动；构建证明搬运完整；typecheck 证明接口一致）

- [ ] **Step 4: Commit**

```bash
git add plugins/coding/panel/src/components/StationView.vue plugins/coding/panel/src/App.vue
git commit -m "refactor: 会话接线下沉 StationView——多工位前的单工位验证壳(R4 阶段三 T4)"
```

**本任务人工冒烟（实施者本地 `pnpm dev` 起 panel 预览或直接构建后真机）**：单工位对话/审批/交接/回放与阶段二一致。冒烟结果写进 commit body 或任务回报。

---

### Task 5: SessionRail 左栏组件（纯展示）

**Files:**
- Create: `plugins/coding/panel/src/components/SessionRail.vue`

**Interfaces:**
- Consumes: T1 的 `RailLive`、stations store state
- Produces（T6 壳装配的确切接口）:
  - props:
    ```ts
    {
      rows: RailRow[];            // 会话行(壳合并 coding.sessions 结果与派生状态)
      stations: Station[];        // 工位徽标行(已绑会话归属显示)
      focusId: number;
      drawer: boolean;            // 窄窗抽屉模式(壳传;宽窗忽略)
    }
    ```
    `interface RailRow { id: string; title: string; subtitle: string; agent: string; live: RailLive; boundStationId: number | null }`（从本文件 export）
  - emits: `["join", "stop", "new-session", "focus-station", "close-drawer"]`
    - `join(sid: string, agent: string)`：点未绑行 = 加入工位
    - `focus-station(id: number)`：点已绑行 = 聚焦对应工位
    - `stop(sid: string)`：行内「停止」
    - `new-session()`：顶部「+ 新工位」（满 3 时壳侧禁用并 title 提示「最多 3 个工位」）

设计要点：
- 纯展示组件，零逻辑（不 invoke）；行结构对齐会话墙卡片语义：title=「{cwd basename} · {prompt 前 20 字}」，subtitle=「{引擎} · {活体文案} · {相对时间}」（文案拼装由壳在 T6 做，组件只渲染）。
- 活体文案映射（本文件导出供壳用）：`const LIVE_TEXT: Record<RailLive, string> = { waiting: "等待审批", running: "运行中", idle: "空闲" }`。
- 已绑行显示工位角标（`工位 {boundStationId}`）且点击 emit `focus-station`；未绑行点击 emit `join`；running/waiting 行显示「停止」钮（stopPropagation）。
- drawer 模式：罩层 + 左滑出面板，点罩层/选中后 emit `close-drawer`。
- 样式进 `style.css`（T6 统一），本文件只写结构 + class。

- [ ] **Step 1: 写组件**（结构如下，class 名即最终约定，T6 样式直接钉这些名）

```vue
<script setup lang="ts">
// 左栏会话列表(R4 阶段三 T5,纯展示):工位徽标 + 会话行(加入工位/聚焦/停止) + 新工位入口。
// 数据编排(coding.sessions 拉取/派生合并/防抖)在壳 App.vue;本组件只渲染与转发意图。
import type { Station } from "../stores/stations";

export interface RailRow {
  id: string; title: string; subtitle: string; agent: string;
  live: "running" | "waiting" | "idle"; boundStationId: number | null;
}
export const LIVE_TEXT = { waiting: "等待审批", running: "运行中", idle: "空闲" } as const;

defineProps<{ rows: RailRow[]; stations: Station[]; focusId: number; drawer: boolean }>();
const emit = defineEmits<{
  join: [sid: string, agent: string];
  stop: [sid: string];
  "new-session": [];
  "focus-station": [id: number];
  "close-drawer": [];
}>();

function onRow(r: RailRow) {
  if (r.boundStationId !== null) { emit("focus-station", r.boundStationId); emit("close-drawer"); }
  else { emit("join", r.id, r.agent); emit("close-drawer"); }
}
</script>

<template>
  <aside v-if="!drawer" class="rail">
    <div class="rail-head">
      <span class="rail-title">会话</span>
      <button type="button" class="rail-add" @click="emit('new-session')">+ 新工位</button>
    </div>
    <div class="rail-rows">
      <div
        v-for="r in rows" :key="r.id" class="rail-row" :class="{ bound: r.boundStationId !== null }"
        @click="onRow(r)"
      >
        <div class="rail-row-title">{{ r.title }}</div>
        <div class="rail-row-sub">
          <span v-if="r.boundStationId !== null" class="rail-badge">工位 {{ r.boundStationId }}</span>
          {{ r.subtitle }}
        </div>
        <button
          v-if="r.live !== 'idle'" type="button" class="rail-stop"
          @click.stop="emit('stop', r.id)"
        >停止</button>
      </div>
      <div v-if="!rows.length" class="rail-empty">暂无会话</div>
    </div>
  </aside>
  <template v-else>
    <div class="rail-mask" @click="emit('close-drawer')"></div>
    <aside class="rail rail-drawer"><!-- 同构内容:复用上方 aside 内部结构 --></aside>
  </template>
</template>
```

（实现时把 aside 内部抽成 `<template>` 复用块或直接双写；双写可接受，行结构完全一致。）

- [ ] **Step 2: 闸门 + Commit**

Run: `cd plugins/coding/panel && pnpm test && pnpm build && pnpm typecheck`

```bash
git add plugins/coding/panel/src/components/SessionRail.vue
git commit -m "feat: 左栏会话列表组件(纯展示)(R4 阶段三 T5)"
```

---

### Task 6: 壳 App.vue 重写——demux/左栏编排/聚焦停靠/窄窗自适应

**Files:**
- Modify: `plugins/coding/panel/src/App.vue`（替换 T4 过渡壳）
- Modify: `plugins/coding/panel/src/style.css`（布局/停靠/窄窗样式）

**Interfaces:**
- Consumes: T1 stations store、T4 StationView 接口（props/expose/emits）、T5 SessionRail 接口、桥 `onInit/hasBridge/invoke`
- Produces: 完整多工位 studio（无下游任务依赖其内部）

设计要点：

1. **demux（onInit 唯一入口）**：
```ts
onInit((data) => {
  const d = data as PanelData;
  if (!d) return;
  if (d.attach === true && !d.event) {           // attach 载荷(任务卡/会话墙「接管」路由)
    const target = stations.pickBindTarget();
    stations.bind(target, String(d.session_id || ""), normAgent(String(d.agent || "")));
    stationRefs[target]?.bindSession(String(d.session_id || ""), normAgent(String(d.agent || "")));
    stations.focus(target);
    return;
  }
  const sid = d.session_id ? String(d.session_id) : "";
  if (!sid || !d.event) return;
  const id = stations.stationForSid(sid);
  if (id !== null) stationRefs[id]?.onData(d);   // 已绑工位:投递
  else {                                          // 未绑:左栏派生 + 终态/陌生 sid 防抖刷新
    stations.bumpRail(sid, d.event.kind);
    scheduleRailRefresh();
  }
});
```
`stationRefs: Record<number, StationViewExposed>` 经 `:ref="el => stationRefs[s.id] = el"` 收集（v-for 函数 ref）。`normAgent` 从 drivers.ts import。

2. **左栏编排**：
```ts
const railRows = ref<RailRow[]>([]);
let railTimer: ReturnType<typeof setTimeout> | null = null;
function scheduleRailRefresh() {                     // 400ms 防抖(对齐 wall 刷新惯例)
  if (railTimer) clearTimeout(railTimer);
  railTimer = setTimeout(() => { railTimer = null; void refreshRail(); }, 400);
}
async function refreshRail() {
  try {
    const r = await invoke<{ sessions?: SessionRow[] }>("coding.sessions", {}); // quiet 别名
    const rows = (r && r.sessions) || [];
    railRows.value = rows.map((row) => {
      const sid = String(row.id || "");
      const boundId = stations.stationForSid(sid);
      const live = boundId !== null ? liveOfStation(boundId) : (stations.state.railLive[sid] ?? normLive(row.live));
      return { id: sid, title: railTitle(row), subtitle: railSubtitle(row, live), agent: normAgent(String(row.agent || "")), live, boundStationId: boundId };
    });
  } catch { /* 静默:下轮事件再刷 */ }
}
```
- `liveOfStation(id)`：工位内状态直读（waiting > streaming/sending > idle，读 stationRefs[id].state）。
- `normLive`：后端 `_live_state` 字串直通（"waiting"/"running"/"idle"），缺省 "idle"。
- `railTitle`/`railSubtitle`：对齐 wall_data 语义（basename+prompt 20 字 / 引擎·活体·相对时间 `_rel_time` 逻辑——把 `_rel_time` 在面板侧 `lib/format.ts` 实现一份 20 行的等值函数，命名 `relTime(now, ts)`）。
- 挂载即 `refreshRail()`；之后 demux 的未绑事件/终态事件防抖触发；已绑工位的 sid-change 也触发（新会话入库后左栏可见）。

3. **行动作**：
- `join(sid, agent)`：`const t = stations.pickBindTarget(); stations.bind(t, sid, agent); stationRefs[t]?.bindSession(sid, agent); stations.focus(t);`
- `stop(sid)`：`void invoke("coding.stop", { id: sid }).catch(() => {});`（已绑工位的停止工位内也有入口，此处主要服务未绑行）
- `new-session`：`stations.addStation()`（返回 null 即满——壳在满 3 时给 rail-add 加 disabled 更优：`:disabled="stations.state.stations.length >= MAX_STATIONS"`）
- `focus-station(id)`：`stations.focus(id)`

4. **StationView 装配**：
```vue
<StationView
  v-for="s in stations.state.stations" :key="s.id"
  :ref="(el) => setStationRef(s.id, el)"
  v-show="!narrow || s.id === stations.state.focusId"
  class="station" :class="{ focused: s.id === stations.state.focusId }"
  :focused="s.id === stations.state.focusId"
  :autoplay="s.id === 1 && firstMount"
  :default-cwd="lastCwd"
  @sid-change="(sid, agent) => { stations.syncStationSid(s.id, sid, agent); scheduleRailRefresh(); }"
  @request-focus="stations.focus(s.id)"
  @request-remove="onRemoveStation(s.id)"
/>
```
`onRemoveStation(id)`：已绑 → `stationRefs[id]?.unbindSession()` + `stations.unbind(id)`；空工位且 >1 → `stations.removeStation(id)`；仅剩 1 个空工位 → 无操作（或提示）。
`lastCwd`：refreshRail 后取 rows[0].cwd 缓存（新工位预填）。
`firstMount`：模块级或 onMounted 内布尔，仅首个工位自动回放。

5. **聚焦停靠 CSS**（每工位各自 Composer，聚焦者 footer 停靠页底）：
```css
.shell { position: relative; display: flex; height: 100vh; }
.stations { position: relative; flex: 1; display: flex; min-width: 0; }
.station { flex: 1; min-width: 0; display: flex; flex-direction: column; border-left: 1px solid var(--line, #2a2a2a); }
.station:first-child { border-left: none; }
.station footer { display: none; }                        /* 非聚焦工位输入条隐藏(实例存活,草稿保留) */
.station.focused footer {
  display: block; position: absolute; z-index: 20;
  left: 0; right: 0; bottom: 0;                           /* 停靠 stations 区页底 */
  background: var(--bg, #141414); border-top: 1px solid var(--line, #2a2a2a);
}
.stations { padding-bottom: var(--dock-h, 150px); }        /* 壳按聚焦工位 dockH 设 --dock-h */
.station.focused { box-shadow: inset 0 0 0 1px var(--accent-dim, #3a5); } /* 聚焦高亮(色调对齐既有变量) */
```
壳侧：`const dockH = computed(() => stationRefs[stations.state.focusId]?.dockH?.value ?? 150)`，`:style="{ '--dock-h': dockH + 'px' }"` 挂 `.stations`。

6. **窄窗自适应**：`matchMedia("(max-width: 720px)")` → `narrow` ref（挂载注册 change 监听，卸载移除）。
- narrow：rail 隐藏，工位头左侧出「☰」钮（StationView 头部由壳插槽/或壳绝对定位一个按钮）开抽屉（`drawerOpen` ref → SessionRail drawer 模式）；`v-show` 只留聚焦工位（模板已含）。
- StationView 头部 ☰：最简做法——壳在 `.stations` 左上角绝对定位一个 `narrow` 才显示的按钮，点击开抽屉；聚焦切换在抽屉内完成。

7. **style.css 其余**：rail 宽 240px（drawer 时 overlay 260px）、rail-row/badge/stop/empty 样式（对齐会话墙视觉：卡片化、圆角、暗色既有变量）；HistoryOverlay/HandoffPicker 容器改 `position:absolute; inset:0` 相对 `.station`（`.station{position:relative}`）。

- [ ] **Step 1: 实现 App.vue + style.css**
- [ ] **Step 2: 闸门**

Run: `cd plugins/coding/panel && pnpm test && pnpm build && pnpm typecheck`
Expected: 全绿

- [ ] **Step 3: Commit**

```bash
git add plugins/coding/panel/src/App.vue plugins/coding/panel/src/style.css
git commit -m "feat: 多工位壳——demux/左栏编排/聚焦停靠/窄窗自适应(R4 阶段三 T6)"
```

---

### Task 7: takeover 转发层退役（app 侧）

**Files:**
- Modify: `app/src/components/PanelApp.vue`
- Modify: `app/src/components/InputBar.vue`
- Modify: `app/src/components/WebviewPanel.vue`
- Modify: `app/src/lib/at-mention.ts`（注释）
- Modify: `app/src/shared/bridge.js`（注释）

**Interfaces:**
- Consumes: T3/T4 后面板侧已不消费 takeover-input/stop、不再发 takeover-state
- Produces: 无新接口；WebviewPanel 的 `postToIframe` 通用通道**保留**（桥协议 onMessage 是 SDK 一部分）

逐点删除（行号为 T6 前现状，实施时以内容锚定）：

1. `PanelApp.vue:292-299` `submit()` 删 isCoding 分支（恢复恒走 runInput）；删 294-295 注释。
2. `PanelApp.vue:339` 与 `:432` 两处 `postToIframe({type:"takeover-stop"})` 删除（停止交由工位内 esc/钮）。
3. `PanelApp.vue:388-393` `isCoding` computed 与「coding 运行闸」注释块删除；`:502`（WebviewPanel `:takeover="isCoding"`）与 `:581`（InputBar `:takeover="isCoding"`）两处传参删除。
4. `PanelApp.vue:405-407+` `onPanelEvent` 的 `takeover-state` 分支删除（团子 avatar/上报大脑上下文随之不再有 coding 态——spec 退役语义）；`insert-draft` 等其他分支保留。
5. `submitBrain`（307-318）逃生口及其 UI 入口删除（takeover 消失后 submit 恒为大脑路径，逃生口无对象）。
6. `InputBar.vue:11` 头注、`:19-21` `takeover` prop 及默认值、`:292-293` stopping 特例（恢复 `props.busy && !props.listening`）删除。
7. `WebviewPanel.vue:8` 协议注释 takeover 段、`:23` prop、`:149` init 载荷 takeover 字段、`:153` 注释例子改中性（如 `{type:"takeover-input"}` → 保留 onMessage 通道说明，举例改 `{type:"ping"}`）；`app/src/shared/bridge.js:25` 注释同步改中性。
8. `at-mention.ts:45` 注释「coding takeover 转发」改为「coding 面板 @refs 组装用」。

**不删**：`fileRefPaths`（@ 文件 chips 主输入条自用）、`postToIframe`、桥 `onMessage` 通道。

- [ ] **Step 1: 逐点删除**
- [ ] **Step 2: 闸门**

Run: `cd app && pnpm test && pnpm build`
Expected: 115 绿、构建干净（无组件测试覆盖这些文件，靠构建 + typecheck via build）

- [ ] **Step 3: Commit**

```bash
git add app/src/components/PanelApp.vue app/src/components/InputBar.vue app/src/components/WebviewPanel.vue app/src/lib/at-mention.ts app/src/shared/bridge.js
git commit -m "refactor: takeover 转发层退役——studio 共享输入条为唯一编码输入路径(R4 阶段三 T7)"
```

---

### Task 8: 全分支闸门 + 真机验收清单

- [ ] **Step 1: 全闸门**

```bash
cd plugins/coding/panel && pnpm test && pnpm build && pnpm typecheck   # 面板
cd app && pnpm test && pnpm build                                      # 前端
cd sidecar && .venv/bin/pytest tests/ -q                               # 后端(本阶段零改动,回归确认)
cd app/src-tauri && cargo test                                         # Rust(零改动,回归确认)
```
Expected: 面板 126+8 绿 / app 115 绿 / sidecar 1089 绿 / cargo 40 绿

- [ ] **Step 2: prepare-dist 同步**（面板 dist 随包管线）：`cd app && bash scripts/prepare-dist.sh`（阶段二已加 panel 硬检查）确认通过。

- [ ] **Step 3: 真机验收清单（交付用户）**

```
cd .worktrees/r4-stage1/app && pnpm tauri dev
```
1. 插件页点「多工位」：默认 2 工位并排，主工位自动回放最近会话（阶段二行为保留）
2. 左栏列表 = 会话墙内容（标题/引擎/活体/相对时间）；点未绑行加入空工位；点已绑行聚焦
3. 双工位并行：工位 1 CC、工位 2 codex（picker 选），各发任务，流式互不串扰；输入条跟随聚焦切换（草稿各自保留）
4. busy 排队：运行中发送 → 提示已排队，本轮结束自动发出
5. esc 中断聚焦工位；左栏行内「停止」停未绑会话
6. 换绑/移出：工位头 ✕ 移出后会话后台继续跑（左栏仍见 running），重新加入历史完整
7. 「+ 新工位」到 3 后不可再加；移出/关闭到 1 个后不可再减
8. 窄窗：主窗收窄 → 单工位 + ☰ 抽屉，能力不缺
9. takeover 退役回归：大窗输入条在 studio 打开时直走译宝大脑（不再路由进 coding）
10. 第 4 栏不可加；attach（Feed 任务卡「接管」）落聚焦/空工位正确
```

---

## Self-Review 记录

- spec 覆盖：工位区(T1/T4/T6)✓ 左栏(T5/T6)✓ 聚焦路由输入条(T4 内联 composer + T6 停靠)✓ busy 排队(T3 queueInput + T4 applyQueueInput)✓ esc(T4 守卫)✓ 换绑/移出(T1/T6)✓ 第 4 栏不可加(T1)✓ 窄窗抽屉(T6)✓ takeover 退役(T3/T4/T7)✓ 自适应单一 UI(T6)✓
- 预警关闭：#1 deps 闭包按工位 scoped(T4 搬运天然兑现)✓ #2 lastToolCard 裁定不改(demux 保序,Global Constraints 记录)✓
- 留阶段四：review 栏/permission_resolved 广播/工位流内 perm 卡移除——本阶段 perm 卡行为不动。
- 已知取舍：两表面(主窗内嵌 + 大窗)同开 studio 时布局各自独立(视图态不共享,发送无冲突)——现状即如此,本阶段不改。
