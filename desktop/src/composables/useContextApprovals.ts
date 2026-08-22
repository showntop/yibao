// 大窗上下文面板的审批域：待批准队列（过滤+决策）、已处理历史（localStorage 持久化）、
// 被打断确认快照、以及订阅（onPendingConfirms / onBrainEvent 的审批/任务收尾）。
// 依赖经 deps 注入（组件持有 props/emit），本 composable 不触碰展示层。
import { computed, ref, watch } from "vue";
import {
  onBrainEvent,
  onPendingConfirms,
  sendConfirmBatch,
  type PendingConfirm,
} from "../lib/brain";

export type ProcessedItem = {
  id: string;
  kind: "approval" | "task";
  title: string;
  decision?: "approved" | "rejected";
  remembered?: boolean;
  taskStatus?: "running" | "completed" | "failed" | "timed_out" | "cancelled";
  taskId?: string;
  ts: number;
};

export interface InterruptedApproval {
  id: string;
  skill: string;
  label: string;
  desc: string;
  risk?: number;
  surface?: string;
  createdAt: number;
}

export interface ContextApprovalsDeps {
  /** 当前会话 id（processed 历史按会话分桶；空用兜底 key） */
  sessionId: () => string | undefined;
  /** 重新处理被打断确认：把引导语当草稿带去对话页 */
  emitChat: (draft: string) => void;
}

const PROCESSED_KEY = "yb-session-processed-v1";
const PENDING_SNAPSHOT_KEY = "yb-session-pending-v1";

function loadJson<T>(key: string, fallback: T): T {
  try {
    const parsed = JSON.parse(localStorage.getItem(key) || "null");
    return parsed && typeof parsed === "object" ? (parsed as T) : fallback;
  } catch {
    return fallback;
  }
}

function saveJson(key: string, value: unknown) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* 存储不可用时保留本次窗口状态 */ }
}

const browserPreview = typeof window !== "undefined" && !("__TAURI_INTERNALS__" in window);
const previewDemo = browserPreview && new URLSearchParams(window.location.search).has("demo");
const previewInterrupted = browserPreview && new URLSearchParams(window.location.search).get("demo") === "interrupted";

export function useContextApprovals(deps: ContextApprovalsDeps) {
  const approvals = ref<PendingConfirm[]>([]);
  const decidingIds = ref<Set<string>>(new Set());
  const approvalErrors = ref<Record<string, string>>({});
  const rememberMap = ref<Record<string, boolean>>({});
  const previewApprovalDismissed = ref(false);
  const previewInterruptedDismissed = ref(false);
  const knownApprovals = new Map<string, PendingConfirm>();
  const preparedInterruptedIds = ref<Set<string>>(new Set());

  // ---- 已处理历史（会话分桶持久化）----
  const processedStore = ref<Record<string, ProcessedItem[]>>(loadJson(PROCESSED_KEY, {}));
  const processedSessionKey = computed(() => deps.sessionId() || (previewDemo ? "__preview__" : "__current__"));
  const processedHistory = computed(() => processedStore.value[processedSessionKey.value] ?? []);

  // ---- 被打断确认快照（会话分桶持久化）----
  const pendingSnapshotStore = ref<Record<string, InterruptedApproval[]>>(loadJson(PENDING_SNAPSHOT_KEY, {}));
  const pendingSnapshots = computed(() => pendingSnapshotStore.value[processedSessionKey.value] ?? []);

  watch(processedSessionKey, () => { /* 切换会话时展示态归零由组件自行处理 */ });

  function saveProcessedStore() {
    saveJson(PROCESSED_KEY, processedStore.value);
  }

  function savePendingSnapshotStore() {
    saveJson(PENDING_SNAPSHOT_KEY, pendingSnapshotStore.value);
  }

  function setPendingSnapshots(items: InterruptedApproval[]) {
    pendingSnapshotStore.value = {
      ...pendingSnapshotStore.value,
      [processedSessionKey.value]: items.sort((a, b) => b.createdAt - a.createdAt).slice(0, 20),
    };
    savePendingSnapshotStore();
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

  // ---- 展示派生 ----
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

  // ---- 审批辅助 ----
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
    deps.emitChat(interruptedPrompt(approval));
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

  // ---- 订阅：待批队列 + 审批/任务收尾事件（onMounted 调用，返回清理函数）----
  async function listen(): Promise<() => void> {
    const unsubs: (() => void)[] = [];
    try {
      unsubs.push(onPendingConfirms((list) => {
        const visible = list.filter((item) => !item.surface || item.surface === "pet");
        visible.forEach((item) => knownApprovals.set(item.id, item));
        rememberPendingSnapshots(visible);
        for (const item of visible) {
          const prepared = interruptedApprovals.value.find((old) => preparedInterruptedIds.value.has(old.id) && old.skill === item.skill);
          if (prepared) removePendingSnapshot(prepared.id);
        }
        approvals.value = visible;
      }));
    } catch { /* sidecar unavailable */ }
    try {
      unsubs.push(await onBrainEvent((event) => {
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
        if (commandStarted || commandFinished || agentTaskFinished) void refreshRunningTasks?.();
      }));
    } catch { /* sidecar unavailable */ }
    return () => { for (const u of unsubs) u(); };
  }

  /** 命令/任务收尾后刷新运行中任务（由组件传入，避免反向依赖）。 */
  let refreshRunningTasks: (() => void) | null = null;
  function setRefreshRunningTasks(fn: () => void) {
    refreshRunningTasks = fn;
  }

  return {
    approvals,
    processedHistory,
    pendingSnapshots,
    displayApprovals,
    interruptedApprovals,
    preparedInterruptedIds,
    decidingIds,
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
    listen,
  };
}
