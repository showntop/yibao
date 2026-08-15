import { computed, ref, type Ref } from "vue";
import { buildEventsUrl, useEventStream, type EventSourceLike } from "../api/events";
import type { ConnConfig } from "../api/connection";

export interface Msg {
  role: "user" | "assistant";
  text: string;
  done: boolean;
  interrupted?: boolean;
}

function uuid(): string {
  // crypto.randomUUID 仅安全上下文（HTTPS/localhost）存在；手机浏览器走
  // http://<内网IP> 访问时没有它，降级 getRandomValues（非安全上下文也可用）
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40; // version 4
  b[8] = (b[8] & 0x3f) | 0x80; // variant 10xx
  const h = [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

export function useChat(
  conn: ConnConfig,
  url?: () => string,
  makeES: (u: string) => EventSourceLike = (u) => new EventSource(u) as unknown as EventSourceLike,
  fetchImpl: typeof fetch = fetch,
) {
  const messages: Ref<Msg[]> = ref([]);
  const conversationId = ref(uuid());
  const error = ref("");
  // 默认 url 工厂读 stream.lastSeq 生成带 last_event_id 的断点 URL：手动重连（Chat.vue
  // 5s 兜底 start）新建的 EventSource 带不上 Last-Event-ID header，query 是唯一续传通道。
  // 闭包延后解引用 stream：工厂只在 start 时调用，届时 stream 已初始化完毕。
  const stream = useEventStream(
    () => (url ? url() : buildEventsUrl(conn, stream.lastSeq.value)),
    makeES,
  );
  const busy = computed(() => messages.value.some((m) => m.role === "assistant" && !m.done));
  // 待批角标：confirmation_needed 广播帧无 surface 信封（桌面发起的手机也要看到），
  // 帧 +1 只做「有新增」的提示；真实数目以 syncPendingCount 的 /v1/state 全量为准
  const pendingCount = ref(0);

  async function syncPendingCount(): Promise<void> {
    try {
      const r = await fetchImpl(`${conn.host}/v1/state`, {
        headers: { "X-Yibao-Token": conn.token },
      });
      if (!r.ok) return;
      const body = (await r.json()) as { pending?: unknown[] };
      pendingCount.value = body.pending?.length ?? 0;
    } catch { /* 拉取失败不动计数：角标宁可滞后不误清 */ }
  }
  void syncPendingCount(); // 构造时拉一次（run_done 等帧不动计数，只靠帧 +1 与 sync 收敛）

  // 只认 surface==="mobile" 的帧（P1 信封字段；桌面/面板事件不进手机气泡）
  const mine = (d: { surface?: string }) => d.surface === "mobile";

  // 当前 pending 气泡对应的 run_id（send 响应取回）：桌面轮结束广播的 run_done id 不同，不误收口
  let myRunId = "";

  // 帧去重（M1 seq 补帧）：seq 全局单调（服务端环形缓冲）；重连补帧会重放已见过的帧，
  // seq<=seenSeq 的整帧丢弃（chunk 重放会导致文本重复拼接、run_done 重放会误收口）。
  // 无 seq（0）帧不参与去重（防御无 id 的源）；seq 归一说明服务端重启进入新纪元 → 让位重计。
  let seenSeq = 0;
  const fresh = (seq: number): boolean => {
    if (seq === 1 && seenSeq > 1) seenSeq = 0; // 新纪元首帧：服务重启 seq 从 1 重新计数
    if (seq > 0 && seq <= seenSeq) return false;
    if (seq > seenSeq) seenSeq = seq;
    return true;
  };

  stream.on("final_reply_chunk", (d, seq) => {
    if (!fresh(seq) || !mine(d) || !d.text) return;
    const last = messages.value[messages.value.length - 1];
    if (last && last.role === "assistant" && !last.done) last.text += d.text;
  });
  stream.on("final_reply", (d, seq) => {
    if (!fresh(seq) || !mine(d)) return;
    const last = messages.value[messages.value.length - 1];
    if (last && last.role === "assistant") last.text = d.text ?? last.text;
  });
  stream.on("interrupted", (d, seq) => {
    if (!fresh(seq) || !mine(d)) return;
    const last = messages.value[messages.value.length - 1];
    if (last && last.role === "assistant") last.interrupted = true;
  });
  stream.on("run_done", (d: { id?: string }, seq) => {
    if (!fresh(seq)) return;
    if (d.id !== myRunId) return; // 桌面/其他轮的 run_done 与我无关
    myRunId = "";
    const last = messages.value[messages.value.length - 1];
    if (last && last.role === "assistant") last.done = true;
  });
  stream.on("error", (d, seq) => {
    if (!fresh(seq) || !mine(d)) return;
    error.value = d.text ?? "大脑出错";
  });
  // 排队 notice（手机跨 surface 时 server 发「另一个窗口还在说…」）：写入提示位，气泡 pending 才有解释
  stream.on("notice", (d, seq) => {
    if (!fresh(seq) || !mine(d) || !d.text) return;
    error.value = d.text;
  });
  // 新待批到达（任意 surface 发起）：角标 +1（数目由 syncPendingCount 校准）
  stream.on("confirmation_needed", (_d, seq) => {
    if (!fresh(seq)) return;
    pendingCount.value += 1;
  });

  async function send(text: string): Promise<void> {
    const t = text.trim();
    if (!t || busy.value) return;
    error.value = "";
    messages.value.push({ role: "user", text: t, done: true }, { role: "assistant", text: "", done: false });
    try {
      const r = await fetchImpl(`${conn.host}/v1/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Yibao-Token": conn.token },
        body: JSON.stringify({ text: t, conversation_id: conversationId.value }),
      });
      if (!r.ok) throw new Error(`chat ${r.status}`);
      const body = (await r.json().catch(() => ({}))) as { run_id?: string };
      myRunId = body.run_id ?? "";
    } catch (e) {
      myRunId = "";
      error.value = `发送失败：${e instanceof Error ? e.message : "网络错误"}`;
      const last = messages.value[messages.value.length - 1];
      if (last?.role === "assistant") last.done = true;
    }
  }

  async function interrupt(): Promise<void> {
    try {
      await fetchImpl(`${conn.host}/v1/interrupt`, {
        method: "POST",
        headers: { "X-Yibao-Token": conn.token },
        body: "{}",
      });
    } catch { /* 状态由 interrupted/run_done 帧收敛 */ }
  }

  function newChat(): void {
    messages.value = [];
    conversationId.value = uuid();
    error.value = "";
  }

  stream.start(); // 构造即连接（测试不显式 start；Chat.vue 重复调用无害——start 先 stop 再建）

  return { conn, stream, messages, busy, conversationId, error, pendingCount, syncPendingCount, send, interrupt, newChat };
}
