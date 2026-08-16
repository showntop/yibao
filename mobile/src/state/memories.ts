import { ref, type Ref } from "vue";
import type { ConnConfig } from "../api/connection";

/**
 * 记忆条目（服务端 /v1/memories 的 items；_mem_list 的底座+插件命名空间分组平铺）。
 * label 为命名空间展示名（「译宝」/各插件名），ns 空串 = 底座；created_at 为服务端
 * 拼好的字符串（mem0 原样，可能为空）——手机端不二次格式化，只做友好裁剪。
 */
export interface MemoryItem {
  id: string;
  text: string;
  ns: string;
  label: string;
  created_at: string;
}

/**
 * 记忆库（mobile M2，只读）：拉一次 /v1/memories。与 Feed 的「失败静默」不同——
 * 记忆库是专页（用户主动进来看），错误态必须亮出来且与空态文案分开；
 * 有数据后的失败保留旧列表（宁滞后勿清空）。
 */
export function useMemories(conn: ConnConfig, fetchImpl: typeof fetch = fetch) {
  const items: Ref<MemoryItem[]> = ref([]);
  const loading = ref(false);
  const error = ref("");

  async function refresh(): Promise<void> {
    loading.value = true;
    error.value = "";
    try {
      const r = await fetchImpl(`${conn.host}/v1/memories`, {
        headers: { "X-Yibao-Token": conn.token },
      });
      if (!r.ok) {
        // 非 200（含 503 未接线）：亮错误态，items 保留旧值
        error.value = `加载失败：${r.status}（大脑版本过旧或未接线？）`;
        return;
      }
      const body = (await r.json()) as { items?: MemoryItem[] };
      items.value = body.items ?? [];
    } catch (e) {
      error.value = `加载失败：${e instanceof Error ? e.message : "网络错误"}`;
    } finally {
      loading.value = false;
    }
  }

  return { items, loading, error, refresh };
}
