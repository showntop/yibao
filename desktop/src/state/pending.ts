// 待批准队列（OS 感 §4.5 收件箱 Question 面：连环弹窗的替代）状态机。
// confirmation_needed 进队；任一窗口 sendConfirmBatch / action_result / error 出队；
// 大脑掉线清空（未答确认随进程死）。大窗由会话检查器呈现，小窗/面板按 surface 快批。
import { listen } from "@tauri-apps/api/event";
import type { BrainEvent, BrainStatusMsg, PendingConfirm } from "../protocol/brain-types";

/** 当前确认链支持：普通技能按 skill；后台命令按 command + cwd 精确记忆。 */
export function canRememberTool(skill: string): boolean {
  // coding 审批不经 invoker.apply_verdict（裁决走 confirmation_needed 直兑 future），
  // remember 勾了也不生效——复选框对 coding 隐藏，防误导（P2 审批统一 L2）
  if (skill === "coding") return false;
  return Boolean(skill);
}

export function rememberLabelForTool(skill: string): string {
  return skill === "watch_command" ? "本会话允许相同命令" : "本会话不再询问";
}

let _pc: PendingConfirm[] = [];
const _pcSubs = new Set<(l: PendingConfirm[]) => void>();
const hasTauriBridge = Boolean(
  (window as unknown as { __TAURI_INTERNALS__?: { transformCallback?: unknown } })
    .__TAURI_INTERNALS__?.transformCallback,
);

function _pcEmit(): void {
  const l = [..._pc];
  _pcSubs.forEach((cb) => cb(l));
}

function _pcRemove(id: string): void {
  const n = _pc.filter((p) => p.id !== id);
  if (n.length !== _pc.length) {
    _pc = n;
    _pcEmit();
  }
}

/** IPC 失败时恢复刚才乐观移除的卡；去重避免另一窗口的队列更新造成重复。 */
export function pcRestore(items: PendingConfirm[]): void {
  const existing = new Set(_pc.map((item) => item.id));
  const restore = items.filter((item) => !existing.has(item.id));
  if (restore.length) {
    _pc = [...restore, ..._pc];
    _pcEmit();
  }
}

/** 乐观移除一批卡（返回被移除的，供失败时 pcRestore 恢复）。 */
export function pcRemoveMany(items: { id: string }[]): PendingConfirm[] {
  const ids = new Set(items.map((item) => item.id));
  const removed = _pc.filter((item) => ids.has(item.id));
  for (const it of items) _pcRemove(it.id);
  return removed;
}

/** 订阅待批准队列（立即回当前值；返回取消订阅函数）。 */
export function onPendingConfirms(cb: (l: PendingConfirm[]) => void): () => void {
  _pcSubs.add(cb);
  cb([..._pc]);
  return () => {
    _pcSubs.delete(cb);
  };
}

// ---- 模块级副作用：消费大脑事件流维护队列 ----

// 普通浏览器只用于本地 UI QA，没有 Tauri event bridge；避免模块加载时产生无意义的未处理错误。
if (hasTauriBridge) void listen<BrainEvent>("brain-event", (ev) => {
  const e = ev.payload;
  if (e.kind === "confirmation_needed") {
    // Task 2 攒批：一轮可能多 CONFIRM，actions 带全部待批 action；
    // 兼容旧单条载荷——无 actions 时退化为 [action]（旧 sidecar 里 confirmation_id = action.id）。
    const actions = e.actions?.length ? e.actions : e.action ? [e.action] : [];
    const fresh = actions
      .filter((a) => a?.id && !_pc.some((p) => p.id === a.id))
      .map((a) => ({
        id: a.id as string,
        tool_id: a.tool_id ?? "",
        label: a.label ?? a.tool_id ?? "",
        desc: a.description ?? "",
        params: a.params,
        risk: a.risk,
        // coding 审批经 ProactiveDispatcher 广播时顶层 surface 为 null，action 自带 surface 优先
        surface: a.surface ?? e.surface,
        // 会话归属随信封（并发对话 spec §B）：确认卡按它过滤/定向出队
        conversationId: e.conversationId,
      }));
    if (fresh.length) {
      _pc = [..._pc, ...fresh];
      _pcEmit();
    }
  } else if ((e.kind === "action_result" || e.kind === "error") && e.action?.id) {
    _pcRemove(e.action.id);
  } else if (e.kind === "interrupted") {
    // F1（Task 2 review Important）：cancel-during-CONFIRM 从 error 改为 interrupted 后，
    // 旧逻辑只认 action_result/error 出队 → 待批卡会滞留。打断即出队。
    // 并发对话（spec §C）：只出队该会话的卡——A 会话被打断不清 B 会话的待批确认；
    // 双方都无归属（旧 sidecar/无会话路径）时退化为整批清空（旧语义）。
    const n = _pc.filter((p) => p.conversationId !== e.conversationId);
    if (n.length !== _pc.length) {
      _pc = n;
      _pcEmit();
    }
  }
});

if (hasTauriBridge) void listen<BrainStatusMsg>("brain-status", (ev) => {
  if (ev.payload.status === "up") return;
  if (_pc.length) {
    _pc = [];
    _pcEmit();
  }
});
