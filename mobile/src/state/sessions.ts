import { ref, type Ref } from "vue";
import type { ConnConfig } from "../api/connection";

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

  async function refresh(): Promise<void> {
    loading.value = true;
    try {
      const r = await fetchImpl(`${conn.host}/v1/conversations`, {
        headers: { "X-Yibao-Token": conn.token },
      });
      if (!r.ok) return; // 非 200（含 503 未接线）：保留旧列表
      const body = (await r.json()) as { items?: SessionItem[] };
      list.value = body.items ?? [];
    } catch {
      // 断线/超时：保留旧列表（抽屉非关键路径，不弹错打扰）
    } finally {
      loading.value = false;
    }
  }

  // 取某会话的历史轮；cid 为空 = 服务端默认桶。失败返回空数组（调用方按空历史回显）
  async function open(cid: string): Promise<HistoryItem[]> {
    try {
      const r = await fetchImpl(
        `${conn.host}/v1/history?conversation_id=${encodeURIComponent(cid)}`,
        { headers: { "X-Yibao-Token": conn.token } },
      );
      if (!r.ok) return [];
      const body = (await r.json()) as { items?: HistoryItem[] };
      return body.items ?? [];
    } catch {
      return [];
    }
  }

  return { list, loading, refresh, open };
}
