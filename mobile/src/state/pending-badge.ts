import { ref, type Ref } from "vue";
import type { ConnConfig } from "../api/connection";
import type { StreamLike } from "../api/events";
import { getJson } from "../api/http";

/**
 * 待批角标（M2 从 useChat 上移）：TabBar 审批项与 Chat/Approvals 页是兄弟组件，
 * provide/inject 只能沿祖先链下传，跨兄弟共享取模块级单例 ref 最小改动——
 * TabBar 直接 import 本 ref，页面侧经 usePendingBadge 挂监听/同步。
 */
export const pendingCount: Ref<number> = ref(0);

async function pull(conn: ConnConfig, fetchImpl: typeof fetch): Promise<void> {
  const body = await getJson(conn, "/v1/state", fetchImpl);
  if (!body) return; // 拉取失败不动计数：角标宁可滞后不误清
  const data = body as { pending?: unknown[] };
  pendingCount.value = data.pending?.length ?? 0;
}

// debounce 状态放模块级：usePendingBadge 每次页面挂载都会重挂帧监听（Chat/Approvals
// 各自的事件流），重放帧触发的校准必须收敛到「最新一次挂载」的拉取闭包上。
const DEBOUNCE_MS = 300;
let syncTimer: ReturnType<typeof setTimeout> | undefined;
let latestSync: (() => void) | null = null;

/**
 * 挂角标（任何持有事件流的页面调用）：confirmation_needed 帧只当「有变化」提示——
 * 广播帧无 surface 信封且断线重连/Tab 重挂载会 replay 环形缓冲里的历史帧，本地 +1
 * 必虚增（M1 遗留语义被 TabBar 放大，M2 评审 Important）。改为 300ms debounce 合并
 * 多帧只拉一次 /v1/state：计数恒为服务端事实，重放多少帧都只多拉一次。
 * 构造即全量拉一次；sync() 供审批处理完当场收敛（并吸收未到点的 debounce）。
 */
export function usePendingBadge(
  stream: StreamLike,
  conn: ConnConfig,
  fetchImpl: typeof fetch = fetch,
): { count: Ref<number>; sync: () => Promise<void> } {
  const sync = () => {
    // 手动 sync 即时拉取即是最新的服务端事实，pending 中的 debounce 已无意义
    if (syncTimer !== undefined) clearTimeout(syncTimer);
    syncTimer = undefined;
    return pull(conn, fetchImpl);
  };
  latestSync = sync;
  stream.on("confirmation_needed", () => {
    if (syncTimer !== undefined) clearTimeout(syncTimer); // 尾沿 debounce：窗口内多帧只留最后一拉
    syncTimer = setTimeout(() => {
      syncTimer = undefined;
      latestSync?.(); // 用最新挂载的闭包（conn/fetchImpl 以最后进页的为准）
    }, DEBOUNCE_MS);
  });
  void sync(); // 构造时拉一次（run_done 等帧不动计数，只靠帧提示的 debounce 与 sync 收敛）
  return { count: pendingCount, sync };
}
