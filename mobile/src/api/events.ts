import { ref, type Ref } from "vue";
import type { ConnConfig } from "./connection";

// 订阅的服务端事件名（P1 定的 SSE kind；未列的 kind 被丢弃——事件面小，列全即可）
export const KNOWN_KINDS = [
  "final_reply_chunk", "final_reply", "run_done", "interrupted", "error",
  "notice", "thinking", "speaking", "speaking_done", "confirmation_needed", "reminder",
] as const;

export interface EventSourceLike {
  addEventListener(k: string, cb: (e: { data: string }) => void): void;
  close(): void;
  onopen: (() => void) | null;
  onerror: (() => void) | null;
}

export function buildEventsUrl(c: ConnConfig): string {
  // token 走 query：EventSource 不能设自定义 header（P1 协议决定，TLS/局域网下可接受）
  return `${c.host}/v1/events?token=${encodeURIComponent(c.token)}`;
}

export function useEventStream(
  url: () => string,
  makeES: (u: string) => EventSourceLike = (u) => new EventSource(u) as unknown as EventSourceLike,
) {
  const state: Ref<"idle" | "connecting" | "open" | "error"> = ref("idle");
  let es: EventSourceLike | null = null;
  const handlers = new Map<string, Set<(data: any) => void>>();

  function on(kind: string, fn: (data: any) => void): () => void {
    if (!handlers.has(kind)) handlers.set(kind, new Set());
    handlers.get(kind)!.add(fn);
    return () => handlers.get(kind)?.delete(fn);
  }

  function start(): void {
    stop();
    es = makeES(url()); // 每次 start 重新取 url()：host/token 可能刚配对完
    state.value = "connecting";
    es.onopen = () => (state.value = "open");
    es.onerror = () => (state.value = "error"); // 原生 EventSource 自动重连，连上会再触发 onopen
    for (const kind of KNOWN_KINDS) {
      es.addEventListener(kind, (e) => {
        try {
          const data = JSON.parse(e.data);
          handlers.get(kind)?.forEach((fn) => fn(data));
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

  return { state, on, start, stop };
}
