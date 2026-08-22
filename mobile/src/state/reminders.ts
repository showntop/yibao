import { ref, type Ref } from "vue";
import type { ConnConfig } from "../api/connection";
import { getJson, postJsonResult } from "../api/http";

/**
 * 待触发提醒（服务端 /v1/reminders 的 items；plugins/reminders/tools/list.py 的 rows）。
 * when 是服务端拼好的展示串（「08月16日 15:00 / 每天 09:30 / 每周一 08:00」），
 * 线上不传原始时间戳——手机端直接展示，不二次格式化。
 */
export interface ReminderItem {
  id: string;
  text: string;
  when: string;
}

/**
 * 提醒管理（mobile M2）：浏览 + 取消。refresh 失败静默（保留旧列表）；
 * cancel 失败亮 error 且条目保留（服务端 500 带 error 字段），成功即本地移除。
 */
export function useReminders(conn: ConnConfig, fetchImpl: typeof fetch = fetch) {
  const items: Ref<ReminderItem[]> = ref([]);
  const error = ref("");

  async function refresh(): Promise<void> {
    const body = await getJson(conn, "/v1/reminders", fetchImpl);
    if (!body) return; // 断线/非 200：保留旧列表
    const data = body as { items?: ReminderItem[] };
    items.value = data.items ?? [];
  }

  async function cancel(id: string): Promise<void> {
    error.value = "";
    const res = await postJsonResult(conn, "/v1/reminders/cancel", { id }, fetchImpl);
    if (res.error) {
      // 失败（服务端 error 详情 / 状态码 / 断线）：亮错误，条目保留靠再次 refresh 收敛
      error.value = `取消失败：${res.error}`;
      return;
    }
    items.value = items.value.filter((i) => i.id !== id); // 成功即除，不等 refresh
  }

  return { items, error, refresh, cancel };
}
