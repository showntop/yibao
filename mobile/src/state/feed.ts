import { ref, type Ref } from "vue";
import type { ConnConfig } from "../api/connection";
import { getJson } from "../api/http";

/** 动态条目（服务端 /v1/feed 的 items；feed.py recent() 逐字段透传，倒序） */
export interface FeedItem {
  id: number;
  ts: number; // unix 秒
  kind: "task" | "reminder" | "event"; // task=任务收尾；reminder=提醒触发；event=其它主动事件
  text: string;
  meta: Record<string, unknown>;
  read: number; // 0|1
  status: string; // none|follow|ignore
}

/** 问候统计（服务端 _feed_stats；手机端只读展示，字段全可选防御旧版服务） */
export interface FeedStats {
  pending_reminders?: number;
  running_tasks?: number;
  done_24h?: number;
  unread?: number;
  ignored?: number;
}

/** 进行中任务摘要（服务端 _running_tasks） */
export interface RunningTask {
  id: string;
  kind: string;
  label: string;
  prompt?: string;
  status: string;
  created_at: number;
}

/**
 * 动态流（mobile M2，只读）：拉一次 /v1/feed。失败静默——Feed 是增强面，
 * 断线/非 200 保留旧列表不弹错（空态文案由页面兜）。
 * M3 增 30s 轮询（auto 默认开）：running_tasks（进行中区块）靠它近实时——
 * 完成态转动态流有服务端事件可推，但本页无事件流连接，轮询是零新端点的最省方案。
 * start/stop 供页面挂卸载钩子：interval 不清会跨页存留（泄漏）。
 */
export function useFeed(
  conn: ConnConfig,
  fetchImpl: typeof fetch = fetch,
  opts: { auto?: boolean } = {},
) {
  const items: Ref<FeedItem[]> = ref([]);
  const stats: Ref<FeedStats | null> = ref(null);
  const running: Ref<RunningTask[]> = ref([]);

  async function refresh(): Promise<void> {
    const body = await getJson(conn, "/v1/feed?limit=60", fetchImpl);
    if (!body) return; // 断线/非 200（含 503 未接线）：保留旧值
    const data = body as { items?: FeedItem[]; stats?: FeedStats; running_tasks?: RunningTask[] };
    items.value = data.items ?? [];
    stats.value = data.stats ?? null;
    running.value = data.running_tasks ?? [];
  }

  const POLL_MS = 30_000;
  let timer: ReturnType<typeof setInterval> | undefined;

  function stop(): void {
    if (timer !== undefined) clearInterval(timer);
    timer = undefined;
  }
  function start(): void {
    stop(); // 重复 start 不叠 interval
    timer = setInterval(() => void refresh(), POLL_MS);
  }
  if (opts.auto !== false) start();

  return { items, stats, running, refresh, start, stop };
}
