import { ref, type Ref } from "vue";
import type { ConnConfig } from "../api/connection";

/**
 * 待批角标（M2 从 useChat 上移）：TabBar 审批项与 Chat/Approvals 页是兄弟组件，
 * provide/inject 只能沿祖先链下传，跨兄弟共享取模块级单例 ref 最小改动——
 * TabBar 直接 import 本 ref，页面侧经 usePendingBadge 挂监听/同步。
 */
export const pendingCount: Ref<number> = ref(0);

/** 事件流的最小面：只订阅 confirmation_needed（与 approvals.ts 同款） */
interface StreamLike {
  on(kind: string, fn: (d: any) => void): unknown;
}

async function pull(conn: ConnConfig, fetchImpl: typeof fetch): Promise<void> {
  try {
    const r = await fetchImpl(`${conn.host}/v1/state`, {
      headers: { "X-Yibao-Token": conn.token },
    });
    if (!r.ok) return;
    const body = (await r.json()) as { pending?: unknown[] };
    pendingCount.value = body.pending?.length ?? 0;
  } catch { /* 拉取失败不动计数：角标宁可滞后不误清 */ }
}

/**
 * 挂角标（任何持有事件流的页面调用）：confirmation_needed 帧 +1 只做「有新增」
 * 提示（广播帧无 surface 信封，桌面发起的手机也要看到）；真实数目以 /v1/state
 * 全量为准——构造即拉一次，从审批页返回/切页时由 sync 收敛。
 */
export function usePendingBadge(
  stream: StreamLike,
  conn: ConnConfig,
  fetchImpl: typeof fetch = fetch,
): { count: Ref<number>; sync: () => Promise<void> } {
  stream.on("confirmation_needed", () => {
    pendingCount.value += 1;
  });
  const sync = () => pull(conn, fetchImpl);
  void sync(); // 构造时拉一次（run_done 等帧不动计数，只靠帧 +1 与 sync 收敛）
  return { count: pendingCount, sync };
}
