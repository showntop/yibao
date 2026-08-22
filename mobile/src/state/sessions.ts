import { ref, type Ref } from "vue";
import type { ConnConfig } from "../api/connection";
import { getJsonResult } from "../api/http";

// 会话列表条目（服务端 /v1/conversations 的 items 形状；preview=末条 assistant 文本前 50 字）
export interface SessionItem {
  id: string;
  preview: string;
  turns: number; // 该桶消息数（一轮对话可含多条 tool 轨迹，非「对话轮数」）
}

// 历史轮（服务端 /v1/history 的 items 形状）——role 可能是 user/assistant/tool，
// tool 轮与面板前缀的清洗在 chat.loadHistory 做（与服务端落史格式解耦）
export interface HistoryItem {
  role: string;
  text: string;
}

// 会话只读面（M1 抽屉）：拉会话列表、取单桶历史。历史回显定位是「最近上下文」
// （每桶服务端只留最近 10 轮），不做完整存档承诺。
export function useSessions(conn: ConnConfig, fetchImpl: typeof fetch = fetch) {
  const list: Ref<SessionItem[]> = ref([]);
  const loading = ref(false);
  // 错误态（M2 评审移交）：首拉失败不能只留「还没有历史会话」的空态文案——
  // 「没数据」与「没拉到」要分开；有数据后的失败保留旧列表（抽屉非关键路径）。
  const error = ref("");

  async function refresh(): Promise<void> {
    loading.value = true;
    error.value = "";
    const res = await getJsonResult(conn, "/v1/conversations", fetchImpl);
    if (res.error) {
      // 非 200（含 503 未接线）/断线：亮错误态，保留旧列表（抽屉非关键路径，不弹错打扰）
      error.value = `拉取会话列表失败（${res.error}）`;
    } else {
      const body = res.data as { items?: SessionItem[] } | null;
      list.value = body?.items ?? [];
    }
    loading.value = false;
  }

  // 取某会话的历史轮；cid 为空 = 服务端默认桶。失败（非 200/断线）返回 null——
  // 与「真·空桶 []」分开（M3）：空历史可静默重建，失败若冒充空会清掉当前消息，
  // 调用方（pickSession）据此亮错误保留现场，不切换。
  async function open(cid: string): Promise<HistoryItem[] | null> {
    const res = await getJsonResult(conn, `/v1/history?conversation_id=${encodeURIComponent(cid)}`, fetchImpl);
    if (res.error) return null;
    const body = res.data as { items?: HistoryItem[] } | null;
    return body?.items ?? [];
  }

  return { list, loading, error, refresh, open };
}
