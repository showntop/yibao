# Task 4 报告：StationView 抽取——现 App.vue 会话接线整体下沉

日期：2026-08-18 · 分支：r4-stage1 worktree · 实施：子代理（按主代理 9 条裁定执行）

## 搬运清单对照表

### 逐字搬运（仅 import 路径随目录层级 `../` 调整）

| 段 | 原 App.vue 行 | StationView.vue 落点 | 备注 |
|---|---|---|---|
| store 创建块（含 onResumedCwd/onQueueHandoff 闭包） | 42-56 | script 前段 | 天然工位 scoped，一字未改 |
| drivers store 创建 | 55-56 | 同上 | |
| 头部控件状态（cwd/curMode/switchAgent） | 58-63 | 同上 | watch 追加 sid-change（见「按裁定改」） |
| queueContext 快照 watch | 65-69 | 同上 | 注释「takeover 队列泄放」改「busy 排队泄放」 |
| 无桥预览样例 | 93-104 | 同上 | 裁定 9：保留在工位内 |
| 浮层互收 + esc/click-outside | 106-119 | 同上 | 加 focused 守卫（见「按裁定改」） |
| 成本聚合 costText / newChatDisabled | 121-127 | 同上 | |
| 状态行 status/tip/statusView/onComposerStatus | 129-151 | 同上 | |
| cwd chip + 浮层（commitCwd/onCwdCommit/onCwdBrowse） | 153-173 | 同上 | |
| refreshCwdState / autoReplay / prefillCwd | 175-208 | 同上 | |
| 引擎 chip + picker（onAgentToggle/pickAgent） | 210-228 | 同上 | |
| 权限模式 toggleMode | 230-234 | 同上 | |
| 接续浮层全套（openHistory/onAttachCc/onAttachCodex/onResumeRow） | 236-309 | 同上 | |
| Codex→CC 交接（startCodexHandoff/handoffBrief/pick/cancel/start） | 311-358 | 同上 | |
| ⏪ onRewind | 360-376 | 同上 | |
| runHandoff | 382-405 | 同上 | 注释去「takeover-input 共用」陈旧表述 |
| RunPill 布局（footerEl/errbarRef/pillBottom/pillVisible/relayout/RO/resize） | 428-468 | 同上 | relayout 内增 dockH 赋值（见「按裁定改」） |
| 模板：bridge-warn / MessageList / ErrBar / RunPill / footer+Composer+slots / HistoryOverlay / HandoffPicker | 472-572 | template | 浮层留工位内（裁定 8，样式 T6 统一调） |

### 按裁定改的点

| 裁定 | 落点 | 实装 |
|---|---|---|
| 1 onInit 剥离 takeover | `onData` + `onInit` | `function onData(data: PanelData) { store.handleData(data); }`；`onInit((data) => onData(data as PanelData))`。onHostMessage 整段不搬 |
| 2 busy 排队并入 onSend | `onSend` | busy 时过与空闲同组校验（先 cwd 空→提示+开 cwd 浮层；后 prompt 空→提示+focus）→ `store.queueInput(prompt, refs, cwd.trim(), curMode, dstate.curAgent)` → `composerRef.clear()`（防滞留二次发出）→ 状态行「已排队，本轮结束后自动发送」。**连带改 Composer.vue**：`doSend` 删 busy 重入守卫、发送钮删 `:disabled="busy"`——否则 onSend busy 分支不可达（裁定「不再静默丢弃」无法兑现）。store.send 内重入守卫仍在 |
| 2 expose `dockH` | `relayout` | `const dockH = ref(0)`；relayout 的 nextTick 内 `dockH.value = footerEl.offsetHeight`（与 pill 同 RO 节奏） |
| 2 `bindSession`/`unbindSession` | 新增两函数 | busy（sending‖streaming）→ `onComposerStatus("会话进行中", true)` 并 return；否则 `void store.resumeSession(sid, agent)` / `store.newChat()` |
| 2 `stop`/`isBusy` | 原 onComposerStop 改名 `stop` | `state.streaming ? store.stop() : Promise.resolve(false)`；`isBusy` = 既有 `busy` computed 别名 |
| 3 emits | `defineEmits` | 见下签名；`watch(state.currentSession)` 追加 `emit("sid-change", sid, state.curSessAgent)`（switchAgent 清零保留在前） |
| 4 陈旧 takeover 清理 | 头注 / watch takeover / onHostMessage / 模板 footer 注释 | 全部不进 StationView；`emitPanelEvent` 在 bridge.ts 保留未动；style.css 的 body.takeover 死规则不在本任务范围 |
| 5 props | `withDefaults(defineProps...)` | `{ focused:false, autoplay:false, defaultCwd:"" }`；onMounted：`autoplay` 才 `prefillCwd()`，否则 `defaultCwd` 非空才填进 cwd；`drivers.probe()` 不受 autoplay 限（T2 缓存共享） |
| 6 工位头 | `<header>` 重写 | 左：`会话 <sid前8>` / `新会话`（title 全量 sid）+ `.agent-badge`（agentLabel(curSessAgent)）+ 状态点（waiting→`.dot-waiting`，否则 streaming→`.dot-running`，无则不显；无语义样式，T6 落）；右：#cost/会话/新对话原样 + `.station-remove` ✕ → `emit('request-remove')` |
| 7 过渡壳 | App.vue | 按简报成稿，单 StationView `:focused="true" :autoplay="true" default-cwd=""` |
| 3 request-focus 锚 | 工位根 | `<div class="station" style="display:contents" @mousedown="emit('request-focus')">`——display:contents 不产生盒子，子节点仍是 #app flex 列直接布局项（与搬运前逐像素等价），根仅作挂载/事件锚；DOM 事件经子节点冒泡到根，display:contents 不影响 |

### expose / emits 实装签名

```ts
defineExpose({
  state,          // SessionState（store 响应式态）
  dockH,          // Ref<number>，footer 实时高度（RO 驱动）
  onData,         // (data: PanelData) => void，内调 store.handleData
  bindSession,    // (sid: string, agent: string) => void，busy 守卫「会话进行中」
  unbindSession,  // () => void，busy 守卫同上
  stop,           // () => Promise<boolean>，仅 streaming 受理
  isBusy: busy,   // ComputedRef<boolean> = sending || streaming
});

defineEmits<{
  "sid-change": [sid: string | null, agent: string]; // watch state.currentSession 上报
  "request-focus": [];  // 工位根 mousedown
  "request-remove": []; // 工位头 ✕
}>();
```

## 闸门

- `pnpm test`：132/132 绿（10 文件，与基线一致）
- `pnpm build`：绿，`dist/` 正常生成（index 1,099.79 kB）
- `pnpm typecheck`（vue-tsc）：绿
- 冒烟：panel 工程无 `pnpm dev` script（仅 test/build/typecheck），按闸门约定的降级证据——构建产物正常生成 + 产物静态核验通过：bundle 内含「已排队，本轮结束后自动发送」、`dot-waiting`、`station-remove`、`agent-badge`、`class:"station",style:{display:"contents"},onMousedown...` 编译结果、无桥预览样例与 bridge-warn 文案。真机对话/审批/交接/回放行为未在真桥环境复验（T8 验收清单覆盖）

## Concerns

1. **Composer.vue 连带改动（超出简报 Files 清单）**：裁定 2 的「busy 不再静默丢弃」要成立，必须放行 Composer 的 busy 拦截（doSend 守卫 + 发送钮 disabled），否则 onSend busy 分支永不可达。已按最小改动实装并同步注释。副作用：sending 窗（invoke 在飞约毫秒级）双击发送会把同一文本排入队列——与原 takeover-input 排队语义一致，属裁定内行为。
2. **入队即 clear composer**：裁定未明确；不清空会在泄放后滞留文本、有二次误发风险，取「捕获即消费」语义（与发送成功清空对齐）。
3. **状态点优先级**：waiting 与 streaming 同现时只显 waiting 黄点（v-else-if），按裁定字面顺序解读。
4. **style.css 未动**（裁定 8）：`body.takeover` 死规则、工位头新 class（agent-badge/dot-waiting/dot-running/station-remove）暂无样式，等 T6 统一落。
