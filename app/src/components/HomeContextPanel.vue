<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import {
  getFeedOnce,
  getWidgetsOnce,
  onFeed,
  onBrainEvent,
  onWidgets,
  onPendingConfirms,
  panelAction,
  sendConfirmBatch,
  canRememberSkill,
  rememberLabelForSkill,
  type FeedStats,
  type RunningTask,
  type PendingConfirm,
  type WidgetPayload,
} from "../lib/brain";
import HomeWidget from "./HomeWidget.vue";
import { useLiveAssembly } from "../lib/home-chrome";
import { setDeskOrigin } from "../lib/home-desk-presence";
import { faceOf } from "../lib/home-assembly";

type AgentState = "idle" | "listen" | "think" | "work" | "say" | "success" | "error";
interface ProcessEntry { label: string; done: boolean; ok?: boolean }
interface ContextRow { id: string; title: string; meta: string; kind: "file" | "screen" | "memory" | "conversation" }
interface OutputRow { id: string; title: string; meta: string }
interface ProcessedItem {
  id: string;
  kind: "approval" | "task";
  title: string;
  decision?: "approved" | "rejected";
  remembered?: boolean;
  taskStatus?: "running" | "completed" | "failed" | "timed_out" | "cancelled";
  taskId?: string;
  ts: number;
}
interface InterruptedApproval {
  id: string;
  skill: string;
  label: string;
  desc: string;
  risk?: number;
  surface?: string;
  createdAt: number;
}

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
const assembly = useLiveAssembly();
const peekDensity = computed(() => faceOf(assembly.value, "now", "inspector"));

const stats = ref<FeedStats>({ pending_reminders: 0, running_tasks: 0, done_24h: 0, unread: 0, ignored: 0 });
const runningTasks = ref<RunningTask[]>([]);
const approvals = ref<PendingConfirm[]>([]);
const widgets = ref<WidgetPayload[]>([]);
const loaded = ref(false);
const historyOpen = ref(false);
const decidingIds = ref<Set<string>>(new Set());
const approvalErrors = ref<Record<string, string>>({});
const rememberMap = ref<Record<string, boolean>>({});
const previewApprovalDismissed = ref(false);
const previewInterruptedDismissed = ref(false);
const knownApprovals = new Map<string, PendingConfirm>();
const preparedInterruptedIds = ref<Set<string>>(new Set());
const browserPreview = typeof window !== "undefined" && !("__TAURI_INTERNALS__" in window);
const previewDemo = browserPreview && new URLSearchParams(window.location.search).has("demo");
const previewInterrupted = browserPreview && new URLSearchParams(window.location.search).get("demo") === "interrupted";

const PROCESSED_KEY = "yb-session-processed-v1";
function loadProcessedStore(): Record<string, ProcessedItem[]> {
  try {
    const parsed = JSON.parse(localStorage.getItem(PROCESSED_KEY) || "{}");
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}
const processedStore = ref<Record<string, ProcessedItem[]>>(loadProcessedStore());
const processedSessionKey = computed(() => props.sessionId || (previewDemo ? "__preview__" : "__current__"));
const processedHistory = computed(() => processedStore.value[processedSessionKey.value] ?? []);

const PENDING_SNAPSHOT_KEY = "yb-session-pending-v1";
function loadPendingSnapshotStore(): Record<string, InterruptedApproval[]> {
  try {
    const parsed = JSON.parse(localStorage.getItem(PENDING_SNAPSHOT_KEY) || "{}");
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}
const pendingSnapshotStore = ref<Record<string, InterruptedApproval[]>>(loadPendingSnapshotStore());
const pendingSnapshots = computed(() => pendingSnapshotStore.value[processedSessionKey.value] ?? []);

watch(processedSessionKey, () => (historyOpen.value = false));

function saveProcessedStore() {
  try { localStorage.setItem(PROCESSED_KEY, JSON.stringify(processedStore.value)); } catch { /* 存储不可用时保留本次窗口状态 */ }
}

function savePendingSnapshotStore() {
  try { localStorage.setItem(PENDING_SNAPSHOT_KEY, JSON.stringify(pendingSnapshotStore.value)); } catch { /* 无本地存储时不阻断审批 */ }
}

function setPendingSnapshots(items: InterruptedApproval[]) {
  pendingSnapshotStore.value = {
    ...pendingSnapshotStore.value,
    [processedSessionKey.value]: items.sort((a, b) => b.createdAt - a.createdAt).slice(0, 20),
  };
  savePendingSnapshotStore();
}

function rememberPendingSnapshots(items: PendingConfirm[]) {
  const next = [...pendingSnapshots.value];
  for (const item of items) {
    if (next.some((existing) => existing.id === item.id)) continue;
    next.push({
      id: item.id,
      skill: item.skill,
      label: safeApprovalHistoryTitle(item),
      desc: item.desc || "等待你确认后继续",
      risk: item.risk,
      surface: item.surface,
      createdAt: Date.now(),
    });
  }
  setPendingSnapshots(next);
}

function removePendingSnapshot(id: string) {
  setPendingSnapshots(pendingSnapshots.value.filter((item) => item.id !== id));
  const prepared = new Set(preparedInterruptedIds.value);
  prepared.delete(id);
  preparedInterruptedIds.value = prepared;
}

function setProcessedHistory(items: ProcessedItem[]) {
  processedStore.value = {
    ...processedStore.value,
    [processedSessionKey.value]: items.sort((a, b) => b.ts - a.ts).slice(0, 40),
  };
  saveProcessedStore();
}

function upsertProcessed(item: ProcessedItem) {
  const items = processedHistory.value.filter((existing) => existing.id !== item.id);
  setProcessedHistory([item, ...items]);
}

function updateProcessed(id: string, patch: Partial<ProcessedItem>): boolean {
  const item = processedHistory.value.find((existing) => existing.id === id);
  if (!item) return false;
  upsertProcessed({ ...item, ...patch });
  return true;
}

function safeApprovalHistoryTitle(approval: PendingConfirm): string {
  if (approval.skill === "watch_command") return "后台命令";
  return approval.label || approval.skill || "受控操作";
}

function recordApprovalDecision(approval: PendingConfirm, approved: boolean, remembered: boolean) {
  removePendingSnapshot(approval.id);
  upsertProcessed({
    id: approval.id,
    kind: "approval",
    title: safeApprovalHistoryTitle(approval),
    decision: approved ? "approved" : "rejected",
    remembered: approved && remembered,
    ts: Date.now(),
  });
}

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

const displayApprovals = computed(() => {
  if (approvals.value.length) return approvals.value;
  if (previewInterrupted) return [];
  if (!previewDemo || previewApprovalDismissed.value) return [];
  return [{
    id: "preview-approval",
    label: "后台盯命令",
    skill: "watch_command",
    desc: "译宝需要你的许可才能执行这条命令",
    params: { command: "npm run build", cwd: "/Users/denny/Work/yibao/app" },
  }];
});

const interruptedApprovals = computed(() => {
  const activeIds = new Set(displayApprovals.value.map((item) => item.id));
  const saved = pendingSnapshots.value.filter((item) => !activeIds.has(item.id));
  if (saved.length || !previewInterrupted || previewInterruptedDismissed.value) return saved;
  return [{
    id: "preview-interrupted",
    skill: "watch_command",
    label: "后台命令",
    desc: "等待你确认后继续",
    risk: 3,
    surface: "pet",
    createdAt: Date.now() - 5 * 60_000,
  }];
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

function approvalTitle(approval: PendingConfirm): string {
  if (approval.skill === "watch_command") return "允许在后台运行命令？";
  return approval.label || approval.skill || "允许执行这项操作？";
}

function approvalCommand(approval: PendingConfirm): string {
  const value = approval.params?.command;
  return typeof value === "string" ? value : "";
}

function approvalCwd(approval: PendingConfirm): string {
  const value = approval.params?.cwd;
  return typeof value === "string" ? value : "";
}

function isDeciding(id: string): boolean {
  return decidingIds.value.has(id);
}

function approvalRemember(id: string): boolean {
  return rememberMap.value[id] ?? false;
}

function setApprovalRemember(id: string, event: Event) {
  const checked = (event.target as HTMLInputElement).checked;
  rememberMap.value = { ...rememberMap.value, [id]: checked };
}

function clearApprovalRemember(id: string) {
  const next = { ...rememberMap.value };
  delete next[id];
  rememberMap.value = next;
}

function normalizeTaskStatus(status: unknown): ProcessedItem["taskStatus"] | undefined {
  if (status === "done" || status === "success") return "completed";
  if (status === "stopped" || status === "interrupted") return "cancelled";
  if (status === "timeout") return "timed_out";
  return status === "running" || status === "completed" || status === "failed" || status === "timed_out" || status === "cancelled"
    ? status
    : undefined;
}

function processedMeta(item: ProcessedItem): string {
  const taskLabels: Record<NonNullable<ProcessedItem["taskStatus"]>, string> = {
    running: "执行中",
    completed: "已完成",
    failed: "失败",
    timed_out: "已超时",
    cancelled: "已取消",
  };
  let state = "已处理";
  if (item.decision === "rejected") state = "已拒绝";
  if (item.decision === "approved") state = item.remembered ? "已允许相同操作" : "已允许";
  if (item.taskStatus) state = taskLabels[item.taskStatus];
  const time = new Date(item.ts).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
  return `${state} · ${time}`;
}

function interruptedPrompt(approval: InterruptedApproval): string {
  if (approval.skill === "watch_command") {
    return "重新处理上次未完成的后台命令。请先根据当前会话重新确认具体命令和工作目录，再请求我的批准；不要直接执行。";
  }
  return `重新处理上次未完成的「${approval.label}」。请先结合当前会话重新确认操作内容，再请求我的批准；不要直接执行。`;
}

function prepareInterruptedApproval(approval: InterruptedApproval) {
  emit("chat", interruptedPrompt(approval));
  preparedInterruptedIds.value = new Set([...preparedInterruptedIds.value, approval.id]);
}

function dismissInterruptedApproval(approval: InterruptedApproval) {
  if (approval.id === "preview-interrupted") previewInterruptedDismissed.value = true;
  else void sendConfirmBatch([{ id: approval.id, approved: false, remember: false }]).catch(() => {});
  removePendingSnapshot(approval.id);
  upsertProcessed({
    id: approval.id,
    kind: "approval",
    title: approval.label || "受控操作",
    decision: "rejected",
    ts: Date.now(),
  });
}

async function decideApproval(approval: PendingConfirm, approved: boolean) {
  const remembered = approved && approvalRemember(approval.id);
  if (previewDemo) {
    recordApprovalDecision(approval, approved, remembered);
    previewApprovalDismissed.value = true;
    clearApprovalRemember(approval.id);
    return;
  }
  if (isDeciding(approval.id)) return;
  decidingIds.value = new Set([...decidingIds.value, approval.id]);
  const nextErrors = { ...approvalErrors.value };
  delete nextErrors[approval.id];
  approvalErrors.value = nextErrors;
  try {
    await sendConfirmBatch([{ id: approval.id, approved, remember: remembered }]);
    recordApprovalDecision(approval, approved, remembered);
    clearApprovalRemember(approval.id);
  } catch {
    approvalErrors.value = { ...approvalErrors.value, [approval.id]: "没有提交成功，请重试" };
  } finally {
    const next = new Set(decidingIds.value);
    next.delete(approval.id);
    decidingIds.value = next;
  }
}

let unFeed: (() => void) | null = null;
let unBrain: (() => void) | null = null;
let unWidgets: (() => void) | null = null;
let unApprovals: (() => void) | null = null;

async function refreshRunningTasks() {
  const result = await getFeedOnce().catch(() => ({ items: [], stats: stats.value, running_tasks: [] }));
  if (result.stats) stats.value = result.stats;
  runningTasks.value = result.running_tasks ?? [];
}

onMounted(async () => {
  if (browserPreview) {
    loaded.value = true;
    return;
  }
  try {
    await refreshRunningTasks();
    loaded.value = true;
  } catch { loaded.value = true; }
  try {
    const result = await getWidgetsOnce().catch(() => ({ widgets: [] }));
    widgets.value = result.widgets ?? [];
  } catch { /* no related capabilities */ }
  try {
    unApprovals = onPendingConfirms((list) => {
      const visible = list.filter((item) => !item.surface || item.surface === "pet");
      visible.forEach((item) => knownApprovals.set(item.id, item));
      rememberPendingSnapshots(visible);
      for (const item of visible) {
        const prepared = interruptedApprovals.value.find((old) => preparedInterruptedIds.value.has(old.id) && old.skill === item.skill);
        if (prepared) removePendingSnapshot(prepared.id);
      }
      approvals.value = visible;
    });
  } catch { /* sidecar unavailable */ }
  try {
    unFeed = await onFeed((result) => {
      if (result?.stats) stats.value = result.stats;
      runningTasks.value = result?.running_tasks ?? [];
    });
  } catch { /* sidecar unavailable */ }
  try {
    unBrain = await onBrainEvent((event) => {
      const commandStarted = event.kind === "action_result" && event.action?.skill_id === "watch_command";
      const commandFinished = event.kind === "reminder" && event.type === "watch_command";
      const actionId = event.action?.id;
      const knownApproval = actionId ? knownApprovals.get(actionId) : undefined;
      if (event.kind === "action_result" && knownApproval && !processedHistory.value.some((item) => item.id === actionId)) {
        recordApprovalDecision(knownApproval, true, false);
      }
      if (event.kind === "error" && knownApproval) {
        recordApprovalDecision(knownApproval, false, false);
      }
      if ((event.kind === "action_result" || event.kind === "error") && actionId) knownApprovals.delete(actionId);
      if ((event.kind === "action_result" || event.kind === "error") && actionId) removePendingSnapshot(actionId);
      if (commandStarted) {
        const historyId = actionId || `task:${Date.now()}`;
        const taskId = typeof event.result?.data?.task_id === "string" ? event.result.data.task_id : undefined;
        const taskStatus: ProcessedItem["taskStatus"] = event.result?.success ? "running" : "failed";
        if (!updateProcessed(historyId, { taskId, taskStatus, ts: Date.now() })) {
          upsertProcessed({ id: historyId, kind: "task", title: "后台命令", taskId, taskStatus, ts: Date.now() });
        }
      }
      if (commandFinished) {
        const taskId = event.task_id;
        const taskStatus = normalizeTaskStatus(event.status) ?? "completed";
        const existing = processedHistory.value.find((item) => taskId && item.taskId === taskId);
        if (existing) updateProcessed(existing.id, { taskStatus, ts: Date.now() });
        else upsertProcessed({ id: `task:${taskId || Date.now()}`, kind: "task", title: "后台命令", taskId, taskStatus, ts: Date.now() });
      }
      const agentTaskFinished = event.kind === "reminder" && Boolean(event.task?.id);
      if (agentTaskFinished && event.task?.id) {
        upsertProcessed({
          id: `task:${event.task.id}`,
          kind: "task",
          title: event.task.label || "后台任务",
          taskId: event.task.id,
          taskStatus: normalizeTaskStatus(event.task.status) ?? "completed",
          ts: Date.now(),
        });
      }
      if (commandStarted || commandFinished || agentTaskFinished) void refreshRunningTasks();
    });
  } catch { /* sidecar unavailable */ }
  try { unWidgets = await onWidgets((result) => (widgets.value = result?.widgets ?? [])); } catch { /* sidecar unavailable */ }
});

/** 上下文行的 mono 短标签：文件取后缀，其他类型给短码（替代单字胶囊） */
function kindTag(row: ContextRow): string {
  if (row.kind === "memory") return "mem";
  if (row.kind === "screen") return "scrn";
  if (row.kind === "conversation") return "chat";
  const m = row.title.match(/\.([a-zA-Z0-9]+)$/);
  return m ? `.${m[1].toLowerCase()}` : "file";
}

onUnmounted(() => {
  unFeed?.();
  unBrain?.();
  unWidgets?.();
  unApprovals?.();
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
            <label v-if="canRememberSkill(approval.skill)" class="approval-remember">
              <input type="checkbox" :checked="approvalRemember(approval.id)" @change="setApprovalRemember(approval.id, $event)" />
              <span>{{ rememberLabelForSkill(approval.skill) }}</span>
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
  height: auto;
  gap: 8px;
  overflow: visible;
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
