<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import {
  getFeedOnce,
  getWidgetsOnce,
  onFeed,
  onWidgets,
  onPendingConfirms,
  panelAction,
  type FeedStats,
  type RunningTask,
  type PendingConfirm,
  type WidgetPayload,
} from "../lib/brain";

type AgentState = "idle" | "listen" | "think" | "work" | "say" | "success" | "error";
interface ProcessEntry { label: string; done: boolean; ok?: boolean }
interface ContextRow { id: string; title: string; meta: string; kind: "file" | "screen" | "memory" | "conversation" }
interface OutputRow { id: string; title: string; meta: string }

const props = withDefaults(defineProps<{
  sessionTitle?: string;
  sessionGoal?: string;
  sessionState?: AgentState;
  hasConversation?: boolean;
  processes?: ProcessEntry[];
}>(), {
  sessionTitle: "新对话",
  sessionGoal: "",
  sessionState: "idle",
  hasConversation: false,
  processes: () => [],
});

const emit = defineEmits<{ chat: [draft: string] }>();

const stats = ref<FeedStats>({ pending_reminders: 0, running_tasks: 0, done_24h: 0, unread: 0, ignored: 0 });
const runningTasks = ref<RunningTask[]>([]);
const approvals = ref<PendingConfirm[]>([]);
const widgets = ref<WidgetPayload[]>([]);
const loaded = ref(false);
const processOpen = ref(false);

const browserPreview = typeof window !== "undefined" && !("__TAURI_INTERNALS__" in window);

const stateLabel = computed(() => {
  if (browserPreview && props.sessionState === "idle") return "进行中 · 02:14";
  if (props.sessionState === "listen") return "正在接收";
  if (props.sessionState === "think") return "正在思考";
  if (props.sessionState === "work") return "正在执行";
  if (props.sessionState === "say") return "正在回应";
  if (props.sessionState === "success") return "刚刚完成";
  if (props.sessionState === "error") return "需要留意";
  return props.hasConversation ? "等待下一步" : "尚未开始";
});

const goalText = computed(() => {
  if (props.sessionGoal) return props.sessionGoal;
  if (browserPreview) return "提取决定、待办和负责人";
  if (props.hasConversation && props.sessionTitle !== "新对话") return `围绕「${props.sessionTitle}」继续推进`;
  return "开始对话后，这里会整理本次目标";
});

const displayApprovals = computed(() => {
  if (approvals.value.length) return approvals.value;
  if (!browserPreview) return [];
  return [{ id: "preview-approval", label: "确认决策标记", skill: "会议纪要", desc: "是否将“分阶段上线”标记为已确认决定" }];
});

const displayTasks = computed(() => {
  if (runningTasks.value.length) return runningTasks.value;
  if (!browserPreview) return [];
  return [{ id: "preview-task", label: "提取待办与负责人", created_at: Math.floor(Date.now() / 1000) - 134 } as RunningTask];
});

const contextRows = computed<ContextRow[]>(() => {
  if (browserPreview) {
    return [
      { id: "audio", title: "会议录音.m4a", meta: "32:45", kind: "file" },
      { id: "agenda", title: "议程.md", meta: "1.2 KB", kind: "file" },
      { id: "screen", title: "当前屏幕", meta: "主屏 · 对话窗口", kind: "screen" },
      { id: "memory-1", title: "偏好结构化输出", meta: "刚刚调取", kind: "memory" },
      { id: "memory-2", title: "产品命名风格", meta: "长期记忆", kind: "memory" },
    ];
  }
  if (props.hasConversation) return [{ id: "conversation", title: "当前会话", meta: "对话内容", kind: "conversation" }];
  return [];
});

const relatedWidgets = computed(() => {
  if (widgets.value.length) return widgets.value.slice(0, 3);
  if (!browserPreview) return [];
  return [
    { panel: "minutes:preview", title: "会议纪要", open: "minutes.open" },
    { panel: "reminder:preview", title: "提醒", open: "reminder.open" },
  ] as WidgetPayload[];
});

const outputs = computed<OutputRow[]>(() => {
  if (!browserPreview) return [];
  return [
    { id: "minutes", title: "会议纪要.md", meta: "更新中" },
    { id: "memory", title: "新增记忆 2 条", meta: "刚刚" },
  ];
});

const processRows = computed<ProcessEntry[]>(() => {
  if (props.processes.length) return props.processes;
  if (!browserPreview) return [];
  return [
    { label: "读取会议录音", done: true, ok: true },
    { label: "识别决定与待办", done: true, ok: true },
    { label: "整理负责人", done: false },
  ];
});

const hasInspectorContent = computed(() =>
  displayApprovals.value.length || displayTasks.value.length || contextRows.value.length || relatedWidgets.value.length || outputs.value.length || processRows.value.length,
);

function elapsedSince(ts: number): string {
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (seconds < 60) return "刚开始";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟`;
  return `${Math.floor(seconds / 3600)} 小时`;
}

function openTasks() {
  if (browserPreview) return;
  void panelAction("agents.task_list", {}, undefined, "panel:agents").catch(() => {});
}

function openWidget(widget: WidgetPayload) {
  if (!widget.open) return;
  if (browserPreview) return;
  const pluginId = widget.panel.split(":")[0];
  void panelAction(widget.open, {}, undefined, `panel:${pluginId}`).catch(() => {});
}

let unFeed: (() => void) | null = null;
let unWidgets: (() => void) | null = null;
let unApprovals: (() => void) | null = null;

onMounted(async () => {
  if (browserPreview) {
    loaded.value = true;
    return;
  }
  try {
    const result = await getFeedOnce().catch(() => ({ items: [], stats: stats.value, running_tasks: [] }));
    if (result.stats) stats.value = result.stats;
    runningTasks.value = result.running_tasks ?? [];
    loaded.value = true;
  } catch { loaded.value = true; }
  try {
    const result = await getWidgetsOnce().catch(() => ({ widgets: [] }));
    widgets.value = result.widgets ?? [];
  } catch { /* no related capabilities */ }
  try { unApprovals = onPendingConfirms((list) => (approvals.value = list)); } catch { /* sidecar unavailable */ }
  try {
    unFeed = await onFeed((result) => {
      if (result?.stats) stats.value = result.stats;
      runningTasks.value = result?.running_tasks ?? [];
    });
  } catch { /* sidecar unavailable */ }
  try { unWidgets = await onWidgets((result) => (widgets.value = result?.widgets ?? [])); } catch { /* sidecar unavailable */ }
});

onUnmounted(() => {
  unFeed?.();
  unWidgets?.();
  unApprovals?.();
});
</script>

<template>
  <aside class="session-inspector" aria-label="本次会话状态与上下文">
    <header class="inspector-head">
      <span class="inspector-kicker">本次会话</span>
      <strong>{{ sessionTitle || "新对话" }}</strong>
      <p>{{ goalText }}</p>
      <span class="session-state" :class="`state-${sessionState}`"><i />{{ stateLabel }}</span>
    </header>

    <div v-if="hasInspectorContent" class="session-thread">
      <section v-if="displayApprovals.length" class="session-section needs-you" aria-labelledby="session-needs-title">
        <h2 id="session-needs-title">需要你</h2>
        <button
          v-for="approval in displayApprovals"
          :key="approval.id"
          class="session-row attention-row"
          type="button"
          @click="emit('chat', `关于「${approval.label || approval.skill}」：${approval.desc || '请确认下一步'}`)"
        >
          <i class="row-node" />
          <span class="row-main"><strong>{{ approval.label || approval.skill }}</strong><small>{{ approval.desc || approval.skill }}</small></span>
          <span class="row-arrow">›</span>
        </button>
      </section>

      <section v-if="displayTasks.length" class="session-section" aria-labelledby="session-running-title">
        <h2 id="session-running-title">正在进行</h2>
        <button v-for="task in displayTasks" :key="task.id" class="session-row task-row" type="button" @click="openTasks">
          <i class="row-node running" />
          <span class="row-main"><strong>{{ task.label }}</strong><small>{{ browserPreview ? "2 / 4" : "执行中" }} · {{ elapsedSince(task.created_at) }}</small></span>
          <span class="task-stop" aria-hidden="true" />
        </button>
      </section>

      <section v-if="contextRows.length" class="session-section" aria-labelledby="session-context-title">
        <h2 id="session-context-title">上下文 <span>{{ contextRows.length }}</span></h2>
        <div class="row-group">
          <button v-for="row in contextRows" :key="row.id" class="plain-row" type="button" @click="emit('chat', `查看本次会话使用的上下文「${row.title}」`)">
            <span class="context-kind" :class="`kind-${row.kind}`" aria-hidden="true">{{ row.kind === "memory" ? "忆" : row.kind === "screen" ? "屏" : row.kind === "conversation" ? "话" : "文" }}</span>
            <span>{{ row.title }}</span><small>{{ row.meta }}</small>
          </button>
        </div>
      </section>

      <section v-if="relatedWidgets.length" class="session-section" aria-labelledby="session-capability-title">
        <h2 id="session-capability-title">关联能力</h2>
        <div class="row-group">
          <button v-for="widget in relatedWidgets" :key="widget.panel" class="plain-row capability-row" type="button" :disabled="!widget.open" @click="openWidget(widget)">
            <i class="ability-dot" /><span>{{ widget.title }}</span><span class="row-arrow">›</span>
          </button>
        </div>
      </section>

      <section v-if="outputs.length" class="session-section" aria-labelledby="session-output-title">
        <h2 id="session-output-title">本次产出</h2>
        <div class="row-group">
          <button v-for="output in outputs" :key="output.id" class="plain-row" type="button" @click="emit('chat', `查看本次会话的产出「${output.title}」`)">
            <span class="output-mark" aria-hidden="true" /><span>{{ output.title }}</span><small>{{ output.meta }}</small>
          </button>
        </div>
      </section>

      <section v-if="processRows.length" class="session-section process-section">
        <button class="process-toggle" type="button" :aria-expanded="processOpen" @click="processOpen = !processOpen">
          <span>过程记录</span><small>{{ processRows.length }}</small><span class="process-chevron" :class="{ open: processOpen }">⌄</span>
        </button>
        <div v-if="processOpen" class="process-list">
          <span v-for="(row, index) in processRows" :key="`${row.label}-${index}`" class="process-row">
            <i :class="{ done: row.done, failed: row.done && row.ok === false }" />{{ row.label }}
          </span>
        </div>
      </section>
    </div>

    <div v-else-if="loaded" class="inspector-empty">
      <i />
      <strong>尚未加入上下文</strong>
      <span>文件、屏幕、记忆和执行状态会随本次会话出现在这里</span>
    </div>
  </aside>
</template>

<style scoped>
.session-inspector {
  width: 280px;
  flex-shrink: 0;
  min-height: 0;
  box-sizing: border-box;
  padding: 16px 14px 18px 16px;
  overflow-y: auto;
  scrollbar-width: thin;
  color: var(--yb-text);
  background: var(--yb-content-bg);
  position: relative;
}

button { font: inherit; }

.session-inspector::before {
  content: "";
  position: absolute;
  left: 0;
  top: 8%;
  bottom: 8%;
  width: 1px;
  background: linear-gradient(180deg, transparent, rgba(var(--yb-c-sky-rgb), 0.15) 20%, rgba(var(--yb-c-sky-rgb), 0.08) 82%, transparent);
}

.inspector-head {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 1px 4px 15px 0;
}

.inspector-kicker {
  margin-bottom: 3px;
  color: var(--yb-text-faint);
  font-size: 10px;
  font-weight: var(--yb-fw-bold);
  letter-spacing: 0.08em;
}

.inspector-head strong {
  color: var(--yb-text-strong);
  font-size: 14px;
  font-weight: var(--yb-fw-bold);
  line-height: 1.35;
}

.inspector-head p {
  margin: 0;
  color: var(--yb-text-dim);
  font-size: 11px;
  line-height: 1.45;
}

.session-state {
  margin-top: 3px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--yb-text-faint);
  font-size: 10px;
}

.session-state i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--yb-state-idle);
  box-shadow: 0 0 0 3px rgba(var(--yb-c-sky-rgb), 0.07);
}
.session-state.state-listen i { background: var(--yb-state-listen); }
.session-state.state-think i { background: var(--yb-state-think); }
.session-state.state-work i { background: var(--yb-state-work); }
.session-state.state-say i { background: var(--yb-state-say); }
.session-state.state-success i { background: var(--yb-state-success); }
.session-state.state-error i { background: var(--yb-state-error); }

.session-thread {
  position: relative;
  padding-left: 15px;
}

.session-thread::before {
  content: "";
  position: absolute;
  left: 3px;
  top: 4px;
  bottom: 17px;
  width: 1px;
  background: linear-gradient(180deg, rgba(var(--yb-c-sky-rgb), 0.26), rgba(var(--yb-c-sky-rgb), 0.06));
}

.session-section {
  position: relative;
  padding: 8px 0 10px;
}

.session-section::before {
  content: "";
  position: absolute;
  left: -15px;
  top: 14px;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  transform: translateX(-50%);
  background: #7ba9cc;
  box-shadow: 0 0 0 3px rgba(var(--yb-c-sky-rgb), 0.07);
}

.session-section h2 {
  margin: 0 0 6px;
  display: flex;
  align-items: baseline;
  gap: 5px;
  color: var(--yb-text-faint);
  font-size: 10px;
  font-weight: var(--yb-fw-bold);
  letter-spacing: 0.05em;
}

.session-section h2 span { font-size: 9px; font-weight: var(--yb-fw-medium); }
.needs-you h2 { color: var(--yb-intent-pending-ink); }
.needs-you::before { background: var(--yb-intent-pending); }

.session-row,
.plain-row {
  width: 100%;
  min-height: 36px;
  box-sizing: border-box;
  border: 0;
  background: transparent;
  color: var(--yb-text);
  text-align: left;
  cursor: pointer;
}

.session-row {
  padding: 7px 8px;
  display: flex;
  align-items: center;
  gap: 8px;
  border: 1px solid rgba(var(--yb-c-sky-rgb), 0.12);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.5);
}

.attention-row {
  border-color: var(--yb-intent-pending-soft);
  background: color-mix(in srgb, var(--yb-intent-pending-soft) 54%, white);
}

.session-row:hover,
.plain-row:hover { background: var(--yb-row-hover); }

.row-node {
  width: 7px;
  height: 7px;
  flex: none;
  border-radius: 50%;
  border: 1.5px solid var(--yb-intent-pending);
}
.row-node.running {
  border-color: var(--yb-accent);
  border-top-color: transparent;
  animation: row-spin 1.15s linear infinite;
}

.row-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.row-main strong {
  overflow: hidden;
  color: var(--yb-text-strong);
  font-size: 11px;
  font-weight: var(--yb-fw-medium);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.row-main small {
  overflow: hidden;
  color: var(--yb-text-faint);
  font-size: 9px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.row-arrow { flex: none; color: var(--yb-text-faint); font-size: 14px; }
.task-stop { width: 8px; height: 8px; flex: none; border-radius: 2px; background: var(--yb-text-faint); opacity: 0.55; }

.row-group {
  overflow: hidden;
  border: 1px solid rgba(var(--yb-c-sky-rgb), 0.1);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.38);
}

.plain-row {
  padding: 6px 8px;
  display: flex;
  align-items: center;
  gap: 7px;
  border-bottom: 1px solid rgba(var(--yb-c-slate-rgb), 0.07);
  font-size: 11px;
}
.plain-row:last-child { border-bottom: 0; }
.plain-row > span:nth-of-type(2) { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.plain-row small { margin-left: auto; flex: none; color: var(--yb-text-faint); font-size: 9px; }
.plain-row:disabled { opacity: 0.48; cursor: default; }

.context-kind {
  width: 18px;
  height: 18px;
  flex: none;
  display: grid;
  place-items: center;
  border-radius: 6px;
  background: rgba(var(--yb-c-sky-rgb), 0.08);
  color: var(--yb-accent-deep);
  font-size: 9px;
  font-weight: var(--yb-fw-bold);
}
.kind-memory { color: #6d6ab7; background: rgba(109, 106, 183, 0.08); }
.kind-screen { color: #567d95; }

.capability-row > span:nth-of-type(1) { flex: 1; }
.ability-dot,
.output-mark { width: 6px; height: 6px; flex: none; border-radius: 50%; background: #7ba9cc; box-shadow: 0 0 0 3px rgba(var(--yb-c-sky-rgb), 0.06); }
.output-mark { border-radius: 2px; }

.process-section { padding-bottom: 0; }
.process-toggle {
  width: 100%;
  min-height: 32px;
  padding: 4px 0;
  display: flex;
  align-items: center;
  gap: 6px;
  border: 0;
  background: transparent;
  color: var(--yb-text-faint);
  font-size: 10px;
  text-align: left;
  cursor: pointer;
}
.process-toggle span:first-child { color: var(--yb-text-dim); font-weight: var(--yb-fw-medium); }
.process-toggle small { font-size: 9px; }
.process-chevron { margin-left: auto; transition: transform 160ms var(--yb-ease-out); }
.process-chevron.open { transform: rotate(180deg); }
.process-list { padding: 2px 0 6px; display: flex; flex-direction: column; gap: 5px; }
.process-row { display: flex; align-items: center; gap: 7px; color: var(--yb-text-dim); font-size: 10px; }
.process-row i { width: 6px; height: 6px; border-radius: 50%; border: 1px solid var(--yb-accent); }
.process-row i.done { background: var(--yb-intent-ok); border-color: var(--yb-intent-ok); }
.process-row i.failed { background: var(--yb-danger); border-color: var(--yb-danger); }

.inspector-empty {
  min-height: 180px;
  padding: 36px 18px;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
  color: var(--yb-text-faint);
}
.inspector-empty i { width: 8px; height: 8px; border-radius: 50%; background: var(--yb-border-strong); }
.inspector-empty strong { color: var(--yb-text-dim); font-size: 12px; }
.inspector-empty span { max-width: 190px; font-size: 10px; line-height: 1.5; }

@keyframes row-spin { to { transform: rotate(360deg); } }

@media (prefers-reduced-motion: reduce) {
  .row-node.running { animation: none; border-top-color: var(--yb-accent); }
  .process-chevron { transition: none; }
}
</style>
