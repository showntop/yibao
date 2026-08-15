import { ref, type Ref } from "vue";
import type { ConnConfig } from "../api/connection";

/** 服务端 /v1/state 的待批项（confirm_meta 逐字段透传） */
export interface PendingConfirm {
  id: string;
  skill_id: string;
  summary: string;
  risk: number;
  created_at: number;
}

/** 事件流的最小面：useApprovals 只需要 on() 订阅 confirmation_needed */
interface StreamLike {
  on(kind: string, fn: (d: any) => void): unknown;
}

/**
 * 审批页状态：/v1/state 全量拉取为准（SSE 帧只是「该刷了」的信号，不解析帧内容——
 * 广播帧无 surface 信封，桌面发起的确认手机也要看到）。
 * decide：POST /v1/confirm；200→"ok"；404→"gone"（桌面已处理/过期）；其他/异常→
 * error 提示 + "gone"（fail-safe：不让按钮卡死，列表靠 refresh 收敛）。
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
    try {
      const r = await fetchImpl(`${conn.host}/v1/state`, {
        headers: { "X-Yibao-Token": conn.token },
      });
      if (!r.ok) throw new Error(`state ${r.status}`);
      const body = (await r.json()) as { pending?: PendingConfirm[] };
      pendings.value = body.pending ?? [];
    } catch (e) {
      error.value = `拉取待批失败：${e instanceof Error ? e.message : "网络错误"}`;
    } finally {
      loading.value = false;
    }
  }

  async function decide(id: string, approved: boolean, remember: boolean): Promise<"ok" | "gone"> {
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
    } catch (e) {
      error.value = `审批发送失败：${e instanceof Error ? e.message : "网络错误"}`;
      await refresh();
      return "gone"; // 异常也按 gone 收口：列表以 refresh 后的事实为准
    }
    await refresh();
    return "ok";
  }

  // 桌面/面板发起的新待批 → 全量刷新（不依赖帧内容）
  stream.on("confirmation_needed", () => void refresh());

  return { pendings, loading, error, refresh, decide };
}
