import { ref, type Ref } from "vue";
import type { ConnConfig } from "../api/connection";
import type { StreamLike } from "../api/events";
import { getJsonResult } from "../api/http";

/** 服务端 /v1/state 的待批项（confirm_meta 逐字段透传） */
export interface PendingConfirm {
  id: string;
  skill_id: string;
  summary: string;
  risk: number;
  created_at: number;
}

/**
 * 审批页状态：/v1/state 全量拉取为准（SSE 帧只是「该刷了」的信号，不解析帧内容——
 * 广播帧无 surface 信封，桌面发起的确认手机也要看到）。
 * decide 三态（打磨批）：POST /v1/confirm；200→"ok"；404→"gone"（桌面已处理/过期）；
 * 网络/其他异常→"fail"——error 写「发送失败（网络）」且不 refresh，与「已处理」
 * 语义彻底分开（此前复用 "gone" 收口会把断网点批准误报成「桌面已处理」）。
 */
export function useApprovals(
  conn: ConnConfig,
  stream: StreamLike,
  fetchImpl: typeof fetch = fetch,
) {
  const pendings: Ref<PendingConfirm[]> = ref([]);
  const loading = ref(false);
  const error = ref("");

  async function refresh(): Promise<void> {
    loading.value = true;
    error.value = "";
    const res = await getJsonResult(conn, "/v1/state", fetchImpl);
    if (res.error) {
      error.value = `拉取待批失败：${res.error}`;
    } else {
      const body = res.data as { pending?: PendingConfirm[] } | null;
      pendings.value = body?.pending ?? [];
    }
    loading.value = false;
  }

  async function decide(id: string, approved: boolean, remember: boolean): Promise<"ok" | "gone" | "fail"> {
    try {
      const r = await fetchImpl(`${conn.host}/v1/confirm`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Yibao-Token": conn.token },
        body: JSON.stringify({ id, approved, remember }),
      });
      if (r.status === 404) {
        await refresh();
        return "gone"; // 桌面已处理/过期
      }
      if (!r.ok) throw new Error(`confirm ${r.status}`);
    } catch {
      // 网络/服务端异常：发送失败，不是「桌面已处理」。不 refresh——断网时 state
      // 同样到不了（失败还会把这条更准的提示覆盖成「拉取待批失败」）；列表保持
      // 原样，用户重试或下次进页靠 refresh 收敛
      error.value = "审批发送失败（网络）";
      return "fail";
    }
    await refresh();
    return "ok";
  }

  // 桌面/面板发起的新待批 → 全量刷新（不依赖帧内容）
  stream.on("confirmation_needed", () => void refresh());

  return { pendings, loading, error, refresh, decide };
}
