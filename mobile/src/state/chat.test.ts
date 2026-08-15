import { describe, expect, it, vi } from "vitest";
import { useChat } from "./chat";
import type { EventSourceLike } from "../api/events";
import type { ConnConfig } from "../api/connection";

function mkChat() {
  const listeners = new Map<string, (e: { data: string }) => void>();
  const es: EventSourceLike = {
    addEventListener: (k, cb) => listeners.set(k, cb),
    close: vi.fn(),
    onopen: () => {},
    onerror: () => {},
  };
  const posts: any[] = [];
  const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
    posts.push({ url, body: JSON.parse(String(init?.body)) });
    return new Response(JSON.stringify({ ok: true, run_id: "mob_1", conversation_id: "" }), { status: 200 });
  });
  const chat = useChat(
    { host: "http://x", token: "t" } as ConnConfig,
    () => "u",
    () => es,
    fetchImpl as unknown as typeof fetch,
  );
  return { chat, emit: (k: string, data: unknown) => listeners.get(k)?.({ data: JSON.stringify(data) }), posts };
}

describe("useChat", () => {
  it("发送→chunk 流式拼接→final_reply 收口→run_done 置 done", async () => {
    const { chat, emit, posts } = mkChat();
    await chat.send("你好");
    expect(posts[0].body).toEqual({ text: "你好", conversation_id: chat.conversationId.value });
    expect(chat.messages.value.map((m) => m.role)).toEqual(["user", "assistant"]);
    emit("final_reply_chunk", { kind: "final_reply_chunk", text: "嗨", surface: "mobile" });
    emit("final_reply_chunk", { kind: "final_reply_chunk", text: "，我是译宝", surface: "mobile" });
    emit("final_reply_chunk", { kind: "final_reply_chunk", text: "桌面的", surface: "pet" }); // 桌面帧不进手机气泡
    expect(chat.messages.value[1].text).toBe("嗨，我是译宝");
    emit("final_reply", { kind: "final_reply", text: "嗨，我是译宝", surface: "mobile" });
    emit("run_done", { id: "mob_1" });
    expect(chat.messages.value[1].text).toBe("嗨，我是译宝");
    expect(chat.messages.value[1].done).toBe(true);
    expect(chat.busy.value).toBe(false);
  });

  it("interrupt → interrupted 帧收口；新对话换 conversation_id 且清空", async () => {
    const { chat, emit } = mkChat();
    await chat.send("长任务");
    chat.interrupt();
    emit("interrupted", { kind: "interrupted", surface: "mobile" });
    emit("run_done", { id: "mob_1" });
    const last = chat.messages.value[1];
    expect(last.done).toBe(true);
    expect(last.interrupted).toBe(true);
    const oldId = chat.conversationId.value;
    chat.newChat();
    expect(chat.conversationId.value).not.toBe(oldId);
    expect(chat.messages.value).toHaveLength(0);
  });

  it("run_done 按 run_id 匹配：桌面轮 id 不误收口，自己的 id 才置 done", async () => {
    const { chat, emit } = mkChat();
    await chat.send("排队中的手机请求");
    emit("final_reply_chunk", { kind: "final_reply_chunk", text: "半截", surface: "mobile" });
    emit("run_done", { id: 11 }); // 桌面轮结束广播，id 不匹配
    expect(chat.messages.value[1].done).toBe(false);
    expect(chat.busy.value).toBe(true);
    emit("run_done", { id: "mob_1" }); // 自己的轮结束
    expect(chat.messages.value[1].done).toBe(true);
    expect(chat.busy.value).toBe(false);
  });
});
