// 按印闸门卡（对话流内联）的归属过滤。
// 大窗与小窗共享 surface="pet"（HomeChat.vue 注：勿用 surface 判定来自哪个窗口），
// 归属只能靠 conversationId 细分；无归属信息的卡（旧 sidecar 路径）保持可见——
// 宁可两窗重复，不可漏达（漏达 = 流程死等）。
import type { PendingConfirm } from "../../protocol/brain-types";

export function gateItemsFor(
  items: readonly PendingConfirm[],
  sessionId: string,
): PendingConfirm[] {
  return items.filter((item) => {
    if (item.surface && item.surface !== "pet") return false;
    if (!item.conversationId || !sessionId) return true;
    return item.conversationId === sessionId;
  });
}
