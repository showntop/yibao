import { ref, type Ref } from "vue";
import type { ConnConfig } from "./connection";

// 订阅的服务端事件名（P1 定的 SSE kind；未列的 kind 被丢弃——事件面小，列全即可）
export const KNOWN_KINDS = [
  "final_reply_chunk", "final_reply", "run_done", "interrupted", "error",
  "notice", "thinking", "speaking", "speaking_done", "confirmation_needed", "reminder",
] as const;

export interface EventSourceLike {
  addEventListener(k: string, cb: (e: { data: string; lastEventId: string }) => void): void;
  close(): void;
  onopen: (() => void) | null;
  onerror: (() => void) | null;
}

export function buildEventsUrl(c: ConnConfig, lastEventId = 0): string {
  // token 走 query：EventSource 不能设自定义 header（P1 协议决定，TLS/局域网下可接受）
  const base = `${c.host}/v1/events?token=${encodeURIComponent(c.token)}`;
  // 断点续传：手动重连是「新建」EventSource，带不上 Last-Event-ID header → query 是唯一通道
  return lastEventId > 0 ? `${base}&last_event_id=${lastEventId}` : base;
}

export function useEventStream(
  url: () => string,
  makeES: (u: string) => EventSourceLike = (u) => new EventSource(u) as unknown as EventSourceLike,
) {
  const state: Ref<"idle" | "connecting" | "open" | "error"> = ref("idle");
  // 最近收到的帧 seq（服务端全局单调）：手动重连续传 URL 的断点来源；只增不减，
  // 乱序旧帧不回退（重连断点取已见最大值，宁可多补一帧由 chat 去重，不可漏帧）
  const lastSeq = ref(0);
  let es: EventSourceLike | null = null;
  const handlers = new Map<string, Set<(data: any, seq: number) => void>>();

  function on(kind: string, fn: (data: any, seq: number) => void): () => void {
    if (!handlers.has(kind)) handlers.set(kind, new Set());
    handlers.get(kind)!.add(fn);
    return () => handlers.get(kind)?.delete(fn);
  }

  function start(): void {
    stop();
    es = makeES(url()); // 每次 start 重新取 url()：host/token 可能刚配对完，或带上 lastEventId 断点
    state.value = "connecting";
    es.onopen = () => (state.value = "open");
    es.onerror = () => (state.value = "error"); // 原生 EventSource 自动重连，连上会再触发 onopen
    for (const kind of KNOWN_KINDS) {
      es.addEventListener(kind, (e) => {
        try {
          const data = JSON.parse(e.data);
          // SSE 帧 id 行 → e.lastEventId（服务端每帧必带）；解析失败按 0（无序号帧，不参与去重）
          const seq = parseInt(e.lastEventId, 10) || 0;
          if (seq > lastSeq.value) lastSeq.value = seq;
          handlers.get(kind)?.forEach((fn) => fn(data, seq));
        } catch {
          // 非 JSON data（理论不会发生）：静默丢弃
        }
      });
    }
  }

  function stop(): void {
    es?.close();
    es = null;
    state.value = "idle";
  }

  return { state, lastSeq, on, start, stop };
}
