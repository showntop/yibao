<script setup lang="ts">
// 大窗「上下文面板」：会话目标/状态 + 待批准（审批域在 composables/useContextApprovals）+ 过程行 + 上下文/输出/相关 widget。
import { computed, onMounted, onUnmounted, ref } from "vue";
import {
  getFeedOnce,
  getWidgetsOnce,
  onFeed,
  onWidgets,
  panelAction,
  canRememberTool,
  rememberLabelForTool,
  type FeedStats,
  type RunningTask,
  type WidgetPayload,
} from "../lib/brain";
import HomeWidget from "./HomeWidget.vue";
import { useLiveAssembly } from "../lib/home/home-chrome.ts";
import { setDeskOrigin } from "../lib/home/home-desk-presence.ts";
import { faceOf } from "../lib/home/home-assembly.ts";
import { useContextApprovals } from "../composables/useContextApprovals";

type AgentState = "idle" | "listen" | "think" | "work" | "say" | "success" | "error";
interface ProcessEntry { label: string; done: boolean; ok?: boolean }
interface ContextRow { id: string; title: string; meta: string; kind: "file" | "screen" | "memory" | "conversation" }
interface OutputRow { id: string; title: string; meta: string }

const props = withDefaults(defineProps<{
  sessionId?: string;
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

// 审批域（队列/历史/快照/订阅）独立成 composable
const approvalsApi = useContextApprovals({
  sessionId: () => props.sessionId,
  emitChat: (draft) => emit("chat", draft),
});
const {
  processedHistory,
  displayApprovals,
  interruptedApprovals,
  preparedInterruptedIds,
  approvalErrors,
  prepareInterruptedApproval,
  dismissInterruptedApproval,
  decideApproval,
  approvalTitle,
  approvalCommand,
  approvalCwd,
  isDeciding,
  approvalRemember,
  setApprovalRemember,
  processedMeta,
  setRefreshRunningTasks,
  listen: listenApprovals,
} = approvalsApi;

const assembly = useLiveAssembly();
const peekDensity = computed(() => faceOf(assembly.value, "now", "inspector"));

const stats = ref<FeedStats>({ pending_reminders: 0, running_tasks: 0, done_24h: 0, unread: 0, ignored: 0 });
const runningTasks = ref<RunningTask[]>([]);
const widgets = ref<WidgetPayload[]>([]);
const loaded = ref(false);
const historyOpen = ref(false);

// 浏览器设计预览（无 Tauri 桥）：展示假数据
const browserPreview = typeof window !== "undefined" && !("__TAURI_INTERNALS__" in window);
const previewDemo = browserPreview && new URLSearchParams(window.location.search).has("demo");

const stateLabel = computed(() => {
  if (previewDemo && props.sessionState === "idle") return "进行中 · 02:14";
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
  if (previewDemo) return "提取决定、待办和负责人";
  if (props.hasConversation && props.sessionTitle !== "新对话") return `围绕「${props.sessionTitle}」继续推进`;
  return "开始对话后，这里会整理本次目标";
});

const displayTasks = computed(() => {
  if (runningTasks.value.length) return runningTasks.value;
  if (!previewDemo) return [];
  return [{ id: "preview-task", label: "提取待办与负责人", created_at: Math.floor(Date.now() / 1000) - 134 } as RunningTask];
});

const contextRows = computed<ContextRow[]>(() => {
  if (previewDemo) {
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
  if (!previewDemo) return [];
  return [
    { panel: "minutes:preview", title: "会议纪要", open: "minutes.open", reason: "本次对话在整理会议" },
    { panel: "reminder:preview", title: "提醒", open: "reminder.open", reason: "识别到下周二截止的待办" },
  ] as WidgetPayload[];
});

const outputs = computed<OutputRow[]>(() => {
  if (!previewDemo) return [];
  return [
    { id: "minutes", title: "会议纪要.md", meta: "更新中" },
    { id: "memory", title: "新增记忆 2 条", meta: "刚刚" },
  ];
});

const hasInspectorContent = computed(() =>
  displayApprovals.value.length || interruptedApprovals.value.length || displayTasks.value.length || contextRows.value.length || relatedWidgets.value.length || outputs.value.length || processedHistory.value.length,
);

const contextLine = computed(() => {
  const row = contextRows.value[0];
  if (!row) return "";
  return row.meta ? `${row.title} · ${row.meta}` : row.title;
});

function elapsedSince(ts: number): string {
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (seconds < 60) return "刚开始";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟`;
  return `${Math.floor(seconds / 3600)} 小时`;
}

function pendingAge(ts: number): string {
  const seconds = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (seconds < 60) return "刚刚";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
  return `${Math.floor(seconds / 86400)} 天前`;
}

function openTasks() {
  if (browserPreview) return;
  void panelAction("agents.task_list", {}, undefined, "panel:agents").catch(() => {});
}

function openWidget(widget: WidgetPayload, event?: MouseEvent) {
  if (!widget.open) return;
  if (browserPreview) return;
  setDeskOrigin((event?.currentTarget as Element | null) ?? null);
  const pluginId = widget.panel.split(":")[0];
  void panelAction(widget.open, {}, undefined, `panel:${pluginId}`).catch(() => {});
}

/** 上下文行的 mono 短标签：文件取后缀，其他类型给短码（替代单字胶囊） */
function kindTag(row: ContextRow): string {
  if (row.kind === "memory") return "mem";
  if (row.kind === "screen") return "scrn";
  if (row.kind === "conversation") return "chat";
  const m = row.title.match(/\.([a-zA-Z0-9]+)$/);
  return m ? `.${m[1].toLowerCase()}` : "file";
}

async function refreshRunningTasks() {
  const result = await getFeedOnce().catch(() => ({ items: [], stats: stats.value, running_tasks: [] }));
  if (result.stats) stats.value = result.stats;
  runningTasks.value = result.running_tasks ?? [];
}

let unApprovals: (() => void) | null = null;
let unFeed: (() => void) | null = null;
let unWidgets: (() => void) | null = null;

onMounted(async () => {
  if (browserPreview) {
    loaded.value = true;
    return;
  }
  try {
    await refreshRunningTasks();
    loaded.value = true;
  } catch { loaded.value = true; }
  setRefreshRunningTasks(refreshRunningTasks);
  void listenApprovals().then((u) => (unApprovals = u));
  try {
    const result = await getWidgetsOnce().catch(() => ({ widgets: [] }));
    widgets.value = result.widgets ?? [];
  } catch { /* no related capabilities */ }
  try {
    unFeed = await onFeed((result) => {
      if (result?.stats) stats.value = result.stats;
      runningTasks.value = result?.running_tasks ?? [];
    });
  } catch { /* sidecar unavailable */ }
  try { unWidgets = await onWidgets((result) => (widgets.value = result?.widgets ?? [])); } catch { /* sidecar unavailable */ }
});

onUnmounted(() => {
  unApprovals?.();
  unFeed?.();
  unWidgets?.();
});
</script>

<template>
  <aside class="session-inspector" :class="peekDensity" aria-label="本次会话状态与上下文">
    <HomeWidget id="now" class="now-widget">
      <h2 class="yb-widget-head">本次</h2>
      <div class="now-body">
        <strong>{{ sessionTitle || "新对话" }}</strong>
        <p>{{ goalText }}</p>
        <span class="session-state" :class="`state-${sessionState}`"><i />{{ stateLabel }}</span>
        <template v-if="peekDensity === 'note' && contextLine">
          <p class="now-k">上下文</p>
          <p>{{ contextLine }}</p>
        </template>
      </div>
    </HomeWidget>

    <Teleport
      v-if="interruptedApprovals.length || displayApprovals.length"
      to="#home-paper-duty"
      :disabled="peekDensity !== 'note'"
      defer
    >
      <div class="duty-stack" :class="peekDensity">
        <section v-if="interruptedApprovals.length" class="yb-widget interrupted-section" aria-labelledby="session-interrupted-title">
          <h2 class="yb-widget-head" id="session-interrupted-title">上次未处理 <span class="yb-widget-meta">{{ interruptedApprovals.length }}</span></h2>
          <div class="yb-widget-body">
          <article v-for="approval in interruptedApprovals" :key="approval.id" class="approval-card interrupted-card">
            <div class="approval-head">
              <i class="row-node paused" />
              <span class="approval-copy">
                <strong>{{ approval.label || "受控操作" }}</strong>
                <small>重启后已暂停，不会自动执行 · {{ pendingAge(approval.createdAt) }}</small>
              </span>
              <span class="paused-badge">已暂停</span>
            </div>
            <p class="interrupted-note">重新准备后会放入输入框；发送时将重新核对内容并再次请求批准。</p>
            <div class="approval-actions">
              <button type="button" class="approval-btn reject" @click="dismissInterruptedApproval(approval)">移除</button>
              <button type="button" class="approval-btn resume" :disabled="preparedInterruptedIds.has(approval.id)" @click="prepareInterruptedApproval(approval)">
                {{ preparedInterruptedIds.has(approval.id) ? "已放入输入框" : "重新准备" }}
              </button>
            </div>
          </article>
          </div>
        </section>

        <section v-if="displayApprovals.length" class="yb-widget needs-you" aria-labelledby="session-needs-title">
          <h2 class="yb-widget-head" id="session-needs-title">需要你</h2>
          <div class="yb-widget-body">
          <article
            v-for="approval in displayApprovals"
            :key="approval.id"
            class="approval-card"
            :aria-busy="isDeciding(approval.id)"
          >
            <div class="approval-head">
              <i class="row-node" />
              <span class="approval-copy">
                <strong>{{ approvalTitle(approval) }}</strong>
                <small>{{ approval.desc || "确认后译宝才会继续" }}</small>
              </span>
            </div>
            <div v-if="approvalCommand(approval)" class="command-preview">
              <code>{{ approvalCommand(approval) }}</code>
              <span v-if="approvalCwd(approval)">{{ approvalCwd(approval) }}</span>
            </div>
            <p v-if="approvalErrors[approval.id]" class="approval-error" role="alert">{{ approvalErrors[approval.id] }}</p>
            <label v-if="canRememberTool(approval.tool_id)" class="approval-remember">
              <input type="checkbox" :checked="approvalRemember(approval.id)" @change="setApprovalRemember(approval.id, $event)" />
              <span>{{ rememberLabelForTool(approval.tool_id) }}</span>
            </label>
            <div class="approval-actions">
              <button type="button" class="approval-btn reject" :disabled="isDeciding(approval.id)" @click="decideApproval(approval, false)">拒绝</button>
              <button type="button" class="approval-btn allow" :disabled="isDeciding(approval.id)" @click="decideApproval(approval, true)">
                {{ isDeciding(approval.id) ? "处理中…" : approvalRemember(approval.id) ? "允许并记住" : "仅允许本次" }}
              </button>
            </div>
          </article>
          </div>
        </section>
      </div>
    </Teleport>

    <template v-if="peekDensity === 'inspector' && hasInspectorContent">
      <section v-if="displayTasks.length" class="yb-widget" aria-labelledby="session-running-title">
        <h2 class="yb-widget-head" id="session-running-title">正在进行</h2>
        <div class="yb-widget-body">
        <button v-for="task in displayTasks" :key="task.id" class="session-row task-row" type="button" @click="openTasks">
          <i class="row-node running" />
          <span class="row-main"><strong>{{ task.label }}</strong><small>{{ previewDemo ? "2 / 4" : "执行中" }} · {{ elapsedSince(task.created_at) }}</small></span>
          <span class="task-stop" aria-hidden="true" />
        </button>
        </div>
      </section>

      <section v-if="contextRows.length" class="yb-widget" aria-labelledby="session-context-title">
        <h2 class="yb-widget-head" id="session-context-title">上下文 <span class="yb-widget-meta">{{ contextRows.length }}</span></h2>
        <div class="row-group">
          <button v-for="row in contextRows" :key="row.id" class="plain-row" type="button" @click="emit('chat', `查看本次会话使用的上下文「${row.title}」`)">
            <span class="context-kind" :class="`kind-${row.kind}`" aria-hidden="true">{{ kindTag(row) }}</span>
            <span>{{ row.title }}</span><small>{{ row.meta }}</small>
          </button>
        </div>
      </section>

      <section v-if="relatedWidgets.length" class="yb-widget" aria-labelledby="session-capability-title">
        <h2 class="yb-widget-head" id="session-capability-title">关联能力</h2>
        <div class="row-group">
          <button v-for="widget in relatedWidgets" :key="widget.panel" class="plain-row capability-row" type="button" :disabled="!widget.open" @click="openWidget(widget, $event)">
            <i class="ability-dot" />
            <span class="capability-main">
              <span class="capability-title">{{ widget.title }}</span>
              <small v-if="widget.reason" class="capability-reason">{{ widget.reason }}</small>
            </span>
            <svg class="capability-arrow" viewBox="0 0 12 12" aria-hidden="true"><path d="M4 2 L8 6 L4 10" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round" stroke-linejoin="round" /></svg>
          </button>
        </div>
      </section>

      <section v-if="outputs.length" class="yb-widget" aria-labelledby="session-output-title">
        <h2 class="yb-widget-head" id="session-output-title">本次产出</h2>
        <div class="row-group">
          <button v-for="output in outputs" :key="output.id" class="plain-row" type="button" @click="emit('chat', `查看本次会话的产出「${output.title}」`)">
            <span class="output-mark" aria-hidden="true" /><span>{{ output.title }}</span><small>{{ output.meta }}</small>
          </button>
        </div>
      </section>

      <section v-if="processedHistory.length" class="yb-widget history-section">
        <button class="process-toggle" type="button" :aria-expanded="historyOpen" @click="historyOpen = !historyOpen">
          <span>已处理</span><small>{{ processedHistory.length }}</small><span class="process-chevron" :class="{ open: historyOpen }">⌄</span>
        </button>
        <div v-if="historyOpen" class="processed-list">
          <div v-for="item in processedHistory" :key="item.id" class="processed-row">
            <i :class="`status-${item.taskStatus || item.decision || 'done'}`" />
            <span><strong>{{ item.title }}</strong><small>{{ processedMeta(item) }}</small></span>
          </div>
        </div>
      </section>

    </template>
  </aside>
</template>

<style scoped>
.session-inspector {
  flex: 1;
  width: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  overflow-y: auto;
  scrollbar-width: thin;
  color: var(--yb-paper-ink);
}
/* 去掉内部 .yb-widget 的套娃瓷片视觉：host.kind-context 已经是瓷片了，
   内部子瓷片不再叠边框/背景/阴影/圆角，由 host 整体承担视觉。 */
.session-inspector :deep(.yb-widget) {
  border: 0;
  border-radius: 0;
  background: transparent;
  box-shadow: none;
}
.session-inspector :deep(.yb-widget::after) {
  display: none;
}

.session-inspector.note {
  height: 100%;
  min-width: 0;
  min-height: 0;
  gap: 8px;
  overflow: hidden;
}
.session-inspector.note .now-widget {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.session-inspector.note .now-body {
  position: relative;
  flex: 1;
  min-width: 0;
  min-height: 0;
  overflow-wrap: anywhere;
}
.session-inspector.note .now-body::after {
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
  background:
    repeating-linear-gradient(
      to bottom,
      transparent 0 21px,
      color-mix(in srgb, var(--yb-line) 72%, transparent) 21px 22px
    );
  mask-image: linear-gradient(180deg, transparent 0 36%, #000 52%, #000 78%, transparent);
  -webkit-mask-image: linear-gradient(180deg, transparent 0 36%, #000 52%, #000 78%, transparent);
}
.now-k {
  margin: 8px 0 0;
  color: var(--yb-text-faint);
  font-size: 11px;
}
.duty-stack.note {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.duty-stack.note .yb-widget {
  border: 0;
  background: transparent;
  box-shadow: none;
  padding: 0;
}

button { font: inherit; }

.now-body {
  padding: 2px var(--yb-widget-pad-x) 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.now-body strong {
  color: var(--yb-paper-ink);
  font-size: 15px;
  font-weight: var(--yb-fw-bold);
  letter-spacing: -0.01em;
  line-height: 1.35;
}

.now-body p {
  margin: 0;
  color: var(--yb-paper-ink-dim);
  font-size: 11px;
  line-height: 1.5;
}

.session-state {
  margin-top: 4px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--yb-text-faint);
  font-size: 10.5px;
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

.needs-you .yb-widget-head { color: var(--yb-intent-pending-ink); }

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
  padding: 8px 10px;
  display: flex;
  align-items: center;
  gap: 8px;
  border-radius: calc(var(--yb-widget-radius) - 6px);
  background: var(--yb-note-mute);
  box-shadow: var(--yb-press);
  transition: background 160ms var(--yb-ease-out);
}

.approval-card {
  box-sizing: border-box;
  padding: 10px;
  border-radius: calc(var(--yb-widget-radius) - 6px);
  background: color-mix(in srgb, var(--yb-intent-pending-soft) 40%, var(--yb-note-mute));
  box-shadow: var(--yb-press);
}

.interrupted-card {
  border-color: var(--yb-note-border);
  background: var(--yb-note-mute);
}

.approval-card + .approval-card,
.session-row + .session-row { margin-top: 7px; }
.row-node.paused { border-color: var(--yb-text-faint); background: rgba(var(--yb-c-slate-rgb), 0.08); }

.paused-badge {
  flex: none;
  padding: 2px 5px;
  border-radius: 5px;
  background: rgba(var(--yb-c-slate-rgb), 0.07);
  color: var(--yb-text-faint);
  font-size: 8px;
  font-weight: var(--yb-fw-medium);
}

.interrupted-note {
  margin: 8px 0 0;
  color: var(--yb-text-faint);
  font-size: 9px;
  line-height: 1.5;
}

.approval-head {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}

.approval-head .row-node { margin-top: 4px; }

.approval-copy {
  min-width: 0;
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 3px;
}

.approval-copy strong {
  color: var(--yb-text-strong);
  font-size: 11px;
  font-weight: var(--yb-fw-bold);
  line-height: 1.35;
}

.approval-copy small {
  color: var(--yb-text-faint);
  font-size: 9px;
  line-height: 1.45;
}

.command-preview {
  margin-top: 8px;
  padding: 7px 8px;
  overflow: hidden;
  border: 1px solid var(--yb-note-border);
  border-radius: 8px;
  background: var(--yb-note-mute);
}

.command-preview code,
.command-preview span {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.command-preview code {
  color: var(--yb-text);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 9px;
}

.command-preview span {
  margin-top: 3px;
  color: var(--yb-text-faint);
  font-size: 8px;
}

.approval-actions {
  margin-top: 9px;
  display: flex;
  justify-content: flex-end;
  gap: 6px;
}

.approval-remember {
  margin-top: 8px;
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--yb-text-dim);
  cursor: pointer;
  font-size: 9px;
  line-height: 1.35;
}

.approval-remember input {
  width: 13px;
  height: 13px;
  margin: 0;
  accent-color: var(--yb-intent-pending);
}

.approval-btn {
  min-height: 26px;
  padding: 0 9px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 9px;
  font-weight: var(--yb-fw-medium);
}

.approval-btn.reject {
  border: 0;
  background: var(--yb-note-soft);
  color: var(--yb-paper-ink-dim);
  box-shadow: 0 1px 3px rgba(var(--yb-paper-shade-rgb), 0.06);
}

.approval-btn.allow {
  border: 0;
  background: var(--yb-intent-pending);
  color: white;
  box-shadow: 0 2px 8px rgba(242, 160, 60, 0.28);
}

.approval-btn.resume {
  border: 0;
  background: var(--yb-note-soft);
  color: var(--yb-accent-deep);
  box-shadow: 0 1px 3px rgba(var(--yb-paper-shade-rgb), 0.06);
}

.approval-btn:hover:not(:disabled) { filter: brightness(0.98); }
.approval-btn:focus-visible { outline: 2px solid var(--yb-accent-soft); outline-offset: 2px; }
.approval-btn:disabled { cursor: default; opacity: 0.55; }

.approval-error {
  margin: 6px 0 0;
  color: var(--yb-danger);
  font-size: 9px;
}

.session-row:hover {
  background: var(--yb-note-soft);
}
.plain-row:hover { background: var(--yb-note-mute); }

/* 装饰圆点：改为 hairline 短竖条（2px 宽），仅起轻量指示作用，无状态色 */
.row-node {
  width: 2px;
  height: 14px;
  flex: none;
  border-radius: 1px;
  background: var(--yb-border-strong);
  align-self: center;
}
.row-node.running {
  background: var(--yb-accent);
  height: 18px;
  animation: row-pulse 1.2s var(--yb-ease-out) infinite;
}
@keyframes row-pulse {
  0%, 100% { opacity: 0.5; }
  50% { opacity: 1; }
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
.capability-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.capability-title {
  overflow: hidden;
  color: var(--yb-paper-ink);
  font-size: 11px;
  font-weight: var(--yb-fw-medium);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.capability-reason {
  margin-left: 0;
  overflow: hidden;
  color: var(--yb-text-faint);
  font-size: 9px;
  line-height: 1.4;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.capability-arrow {
  width: 12px;
  height: 12px;
  flex: none;
  color: var(--yb-text-faint);
  opacity: 0.7;
}
.capability-row:hover .capability-arrow {
  color: var(--yb-accent-deep);
  opacity: 1;
}
.task-stop { width: 8px; height: 8px; flex: none; border-radius: 2px; background: var(--yb-text-faint); opacity: 0.55; }

.row-group {
  overflow: hidden;
  margin: 0 8px 8px;
  border-radius: calc(var(--yb-widget-radius) - 6px);
  background: var(--yb-note-mute);
  box-shadow: var(--yb-press);
}

.plain-row {
  padding: 7px 10px;
  display: flex;
  align-items: center;
  gap: 7px;
  border-bottom: 1px solid rgba(var(--yb-paper-shade-rgb), 0.07);
  font-size: 11px;
  color: var(--yb-paper-ink);
}
.plain-row:last-child { border-bottom: 0; }
.plain-row > span:nth-of-type(2) { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.plain-row small { margin-left: auto; flex: none; color: var(--yb-text-faint); font-size: 9px; }
.plain-row:disabled { opacity: 0.48; cursor: default; }

.context-kind {
  flex: none;
  padding: 1px 5px;
  display: inline-flex;
  align-items: center;
  border-radius: 4px;
  background: rgba(var(--yb-paper-shade-rgb), 0.07);
  color: var(--yb-text-faint);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 9px;
  font-weight: var(--yb-fw-medium);
  letter-spacing: 0.01em;
  line-height: 1.4;
  text-transform: lowercase;
}
.kind-memory { color: var(--yb-accent-deep); background: rgba(var(--yb-c-sky-rgb), 0.08); }
.kind-screen { color: #567d95; }

.capability-row > span:nth-of-type(1) { flex: 1; }
/* 关联能力小圆点：简化为小竖条 */
.ability-dot { width: 2px; height: 12px; flex: none; border-radius: 1px; background: var(--yb-border-strong); align-self: center; }
.output-mark { width: 6px; height: 6px; flex: none; border-radius: 2px; background: var(--yb-surface-2); }

.history-section { padding-bottom: 4px; }
.process-toggle {
  width: 100%;
  min-height: 32px;
  padding: 8px var(--yb-widget-pad-x);
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

.processed-list {
  padding: 1px var(--yb-widget-pad-x) 8px;
  display: flex;
  flex-direction: column;
}

.processed-row {
  min-height: 34px;
  padding: 5px 3px;
  display: flex;
  align-items: center;
  gap: 8px;
  border-top: 1px solid var(--yb-note-border);
}

.processed-row > i {
  width: 6px;
  height: 6px;
  flex: none;
  border-radius: 50%;
  background: var(--yb-intent-ok);
  box-shadow: 0 0 0 3px rgba(var(--yb-c-sky-rgb), 0.06);
}

.processed-row > i.status-rejected,
.processed-row > i.status-failed,
.processed-row > i.status-timed_out { background: var(--yb-danger); }
.processed-row > i.status-cancelled { background: var(--yb-text-faint); }
.processed-row > i.status-running { background: var(--yb-accent); }

.processed-row > span {
  min-width: 0;
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 2px;
}

.processed-row strong {
  overflow: hidden;
  color: var(--yb-text);
  font-size: 10px;
  font-weight: var(--yb-fw-medium);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.processed-row small { color: var(--yb-text-faint); font-size: 9px; }

@media (prefers-reduced-motion: reduce) {
  .row-node.running { animation: none; }
  .process-chevron { transition: none; }
}
</style>
