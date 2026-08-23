// 小窗待批确认域：确认卡队列（归属过滤）+ 快批裁决（单条/整批）+ 回执占位（approval-guard）。
// 依赖经 deps 注入（App.vue 持有气泡流/窗口状态），本 composable 不触碰其他窗口域。
import { computed, ref, type Ref } from "vue";
import { canRememberTool, onPendingConfirms, sendConfirmBatch, type PendingConfirm } from "../lib/brain";
import type { PetAvatarState } from "./usePetState";

export interface PetApprovalDeps {
  /** 小窗固定会话 id（归属过滤：只收本会话的确认卡） */
  petConvId: Ref<string>;
  state: Ref<PetAvatarState>;
  attentionNeeded: Ref<boolean>;
  expanded: Ref<boolean>;
  pushWarn: (text: string) => void;
  /** 多条待批时的常驻气泡提醒 */
  openBubbleSticky: (text: string) => void;
  expand: () => Promise<void>;
}

export function usePetApproval(deps: PetApprovalDeps) {
  const { petConvId, state, attentionNeeded, expanded, pushWarn, openBubbleSticky, expand } = deps;

  const pendingConfirms = ref<PendingConfirm[]>([]);
  const pending = computed(() => pendingConfirms.value[0] ?? null);
  const pendingCanRemember = computed(() => canRememberTool(pending.value?.tool_id ?? ""));
  const rememberPending = ref(false);
  /** 回执占位（吸收连点，避免同一位置瞬间变成「停止」） */
  const approvalGuard = ref<null | "allowed" | "denied">(null);
  let approvalGuardTimer: ReturnType<typeof setTimeout> | null = null;

  function beginApprovalGuard(approved: boolean) {
    if (approvalGuardTimer) clearTimeout(approvalGuardTimer);
    approvalGuardTimer = null;
    approvalGuard.value = approved ? "allowed" : "denied";
  }

  function releaseApprovalGuard(delay = 850) {
    if (approvalGuardTimer) clearTimeout(approvalGuardTimer);
    approvalGuardTimer = setTimeout(() => {
      approvalGuard.value = null;
      approvalGuardTimer = null;
    }, delay);
  }

  function clearApprovalGuard() {
    if (approvalGuardTimer) clearTimeout(approvalGuardTimer);
    approvalGuardTimer = null;
    approvalGuard.value = null;
  }

  async function decide(approved: boolean, remember = false) {
    if (!pending.value) return;
    const { id } = pending.value;
    state.value = "think";
    beginApprovalGuard(approved);
    try {
      await sendConfirmBatch([{ id, approved, remember: pendingCanRemember.value && remember }]);
      rememberPending.value = false;
      releaseApprovalGuard();
    } catch (err) {
      clearApprovalGuard();
      pushWarn("确认失败：" + String(err));
      state.value = "idle";
    }
  }

  async function decideAllPending(approved: boolean) {
    if (pendingConfirms.value.length < 2) return;
    const items = pendingConfirms.value.map(({ id }) => ({ id, approved, remember: false }));
    state.value = "think";
    beginApprovalGuard(approved);
    try {
      await sendConfirmBatch(items);
      releaseApprovalGuard();
    } catch (err) {
      clearApprovalGuard();
      pushWarn("批量确认失败：" + String(err));
      state.value = "idle";
    }
  }

  /** 订阅共享待批队列并按归属过滤（onMounted 时调用，返回取消订阅）。 */
  function listen(): () => void {
    return onPendingConfirms((items) => {
      const previousCount = pendingConfirms.value.length;
      // 归属过滤（并发对话 spec §B）：surface=pet 之外再按会话 id 区分——大窗会话的
      // 确认卡不再落到小窗快批（无归属的卡保持旧行为照收）。
      pendingConfirms.value = items.filter(
        (item) =>
          (!item.surface || item.surface === "pet") &&
          (!item.conversationId || !petConvId.value || item.conversationId === petConvId.value),
      );
      if (pendingConfirms.value.length === 0) {
        rememberPending.value = false;
        return;
      }
      state.value = "idle";
      if (pendingConfirms.value.length > 1) {
        attentionNeeded.value = true;
        if (!expanded.value) {
          openBubbleSticky(`${pendingConfirms.value.length} 项待批准，可在小窗全部处理`);
        }
      } else if (previousCount === 0 && !expanded.value) {
        // 单条直接展开快批；多条先以常驻气泡提醒，用户展开后可整批处理。
        void expand();
      }
    });
  }

  /** 卸载时清理定时器。 */
  function dispose() {
    if (approvalGuardTimer) clearTimeout(approvalGuardTimer);
    approvalGuardTimer = null;
  }

  return {
    pendingConfirms,
    pending,
    pendingCanRemember,
    rememberPending,
    approvalGuard,
    decide,
    decideAllPending,
    listen,
    dispose,
  };
}
