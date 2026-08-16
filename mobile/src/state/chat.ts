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

  // 只认 surface==="mobile" 的帧（P1 信封字段；桌面/面板事件不进手机气泡）
  const mine = (d: { surface?: string }) => d.surface === "mobile";

  // 当前 pending 气泡对应的 run_id（send 响应取回）：桌面轮结束广播的 run_done id 不同，不误收口
  let myRunId = "";

  // 历史浏览模式（M2 评审移交）：loadHistory 重建历史消息后置位——切走前在途轮的
  // final_reply 迟到会覆写末条历史消息（帧无 run_id 可比对，surface 过滤拦不住），
  // 期间 final_reply/run_done 帧全丢弃；本会话首个新 send 清位，恢复帧的正常消费。
  let historyMode = false;

  // 帧去重（M1 seq 补帧）：seq 单调（服务端环形缓冲，容量 256）；重连补帧会重放已见过的
  // 帧，已见 seq 的整帧丢弃（chunk 重放会导致文本重复拼接、run_done 重放会误收口）。
  // seq 后跳（小于水位且非已见帧序）= 服务端 seq 回卷（重启新纪元，首帧不必是 1）→
  // 水位让位重计，否则新纪元帧被旧水位永久吞掉（重启聋窗）。已见帧序缓存有界：补帧
  // 窗口是 256 帧缓冲，512 两倍冗余；窗口外的低位帧序不可能来自补帧，只会是新纪元。
  let seenSeq = 0;
  const seenFrames = new Set<number>();
  const REPLAY_WINDOW = 512;
  const fresh = (seq: number): boolean => {
    if (seq > 0 && seq < seenSeq && (seenSeq - seq > REPLAY_WINDOW || !seenFrames.has(seq))) {
      seenSeq = 0; // 新纪元：水位归零重计，旧纪元帧序缓存一并作废
      seenFrames.clear();
    }
    if (seq > 0 && seq <= seenSeq) return false;
    if (seq > seenSeq) {
      seenSeq = seq;
      if (seq > 0) {
        seenFrames.add(seq);
        // 防无界增长：窗口外的帧序不再参与判定，顺手清掉
        if (seenFrames.size > REPLAY_WINDOW)
          for (const s of seenFrames) if (s < seenSeq - REPLAY_WINDOW) seenFrames.delete(s);
      }
    }
    return true;
  };

  stream.on("final_reply_chunk", (d, seq) => {
    if (!fresh(seq) || !mine(d) || !d.text) return;
    const last = messages.value[messages.value.length - 1];
    if (last && last.role === "assistant" && !last.done) last.text += d.text;
  });
  stream.on("final_reply", (d, seq) => {
    if (!fresh(seq) || !mine(d)) return;
    if (historyMode) return; // 历史浏览中：旧轮迟到的 final_reply 不覆写历史消息
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
    if (historyMode) return; // 历史浏览中：旧轮收尾帧不碰重建后的消息
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
  // 新待批到达的帧驱动已上移 usePendingBadge（M2 TabBar 角标），此处不再消费

  async function send(text: string): Promise<void> {
    const t = text.trim();
    if (!t || busy.value) return;
    historyMode = false; // 本会话首个新 send：退出历史浏览模式，帧恢复正常消费
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

  // 新开对话：busy 时拒绝（最小方案，不自动 interrupt）——清空会撕掉 pending 气泡、
  // 旧轮收口帧从此无人认领，这一轮永远收不了口。返回 false 交 UI 提示
  // 「先等当前回复完成或点 ⏹」；成功清空返回 true。
  function newChat(): boolean {
    if (busy.value) return false;
    messages.value = [];
    conversationId.value = uuid();
    error.value = "";
    myRunId = ""; // 旧轮 run_done 迟到不得收口新气泡（send 在途窗口内 myRunId 还是旧值）
    return true;
  }

  // 历史回显（M1 会话抽屉）：把 /v1/history 的轮重建为已收口消息。服务端直读 LLM
  // 上下文桶（history.messages()），里面有两类非对话内容须清洗：
  // - role=tool 的工具轨迹轮（模型上下文要，UI 不要）
  // - 面板场景 user 轮的「【xx 面板】」前缀（落史时拼进 content 的 surface 标记）
  // - 空文本 assistant 轮（工具调用的占位轮，无话可显）
  // 注意每桶仅最近 10 轮（服务端 max_turns 裁剪）——回显是「最近上下文」非完整存档。
  function loadHistory(items: { role: string; text: string }[]): void {
    messages.value = items
      .filter((m) => (m.role === "user" || m.role === "assistant") && (m.role === "user" || m.text))
      .map((m) => ({
        role: m.role as "user" | "assistant",
        // 前缀只剥 user 轮（assistant 正文可能合法地以【】开头）
        text: m.role === "user" ? m.text.replace(/^【[^】]{0,24}面板】/, "") : m.text,
        done: true, // 历史轮均已收口；busy 由 messages 派生，全 done 即清零
      }));
    myRunId = ""; // 切了会话：旧轮的 run_done 不再属于当前（重建后的）气泡
    historyMode = true; // 历史浏览开始：旧轮迟到帧不得覆写重建后的消息
    error.value = "";
  }

  stream.start(); // 构造即连接（测试不显式 start；Chat.vue 重复调用无害——start 先 stop 再建）

  return { conn, stream, messages, busy, conversationId, error, send, interrupt, newChat, loadHistory };
}
