import { ref, type Ref } from "vue";
import type { ConnConfig } from "../api/connection";

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
    try {
      const r = await fetchImpl(`${conn.host}/v1/reminders`, {
        headers: { "X-Yibao-Token": conn.token },
      });
      if (!r.ok) return;
      const body = (await r.json()) as { items?: ReminderItem[] };
      items.value = body.items ?? [];
    } catch { /* 断线/超时：保留旧列表 */ }
  }

  async function cancel(id: string): Promise<void> {
    error.value = "";
    try {
      const r = await fetchImpl(`${conn.host}/v1/reminders/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Yibao-Token": conn.token },
        body: JSON.stringify({ id }),
      });
      if (!r.ok) {
        const body = (await r.json().catch(() => ({}))) as { error?: string };
        error.value = `取消失败：${body.error || r.status}`;
        return; // 失败不动列表，靠再次 refresh 收敛
      }
      items.value = items.value.filter((i) => i.id !== id); // 成功即除，不等 refresh
    } catch (e) {
      error.value = `取消失败：${e instanceof Error ? e.message : "网络错误"}`;
    }
  }

  return { items, error, refresh, cancel };
}
