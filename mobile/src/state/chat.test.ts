import { describe, expect, it, vi } from "vitest";
import { useChat } from "./chat";
import type { EventSourceLike } from "../api/events";
import type { ConnConfig } from "../api/connection";

function mkChat() {
  // lastEventId 模拟 SSE 帧 id: 行（不传则 seq=0，视为「无序号帧」不参与去重）
  const listeners = new Map<string, (e: { data: string; lastEventId: string }) => void>();
  const es: EventSourceLike = {
    addEventListener: (k, cb) => listeners.set(k, cb),
    close: vi.fn(),
    onopen: () => {},
    onerror: () => {},
  };
  const posts: any[] = [];
  let runN = 0; // 每次 send 发新 run_id（多轮场景可区分旧轮/新轮的 run_done）
  const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
    posts.push({ url, body: JSON.parse(String(init?.body)) });
    runN += 1;
    return new Response(JSON.stringify({ ok: true, run_id: `mob_${runN}`, conversation_id: "" }), { status: 200 });
  });
  const chat = useChat(
    { host: "http://x", token: "t" } as ConnConfig,
    () => "u",
    () => es,
    fetchImpl as unknown as typeof fetch,
  );
  return {
    chat,
    emit: (k: string, data: unknown, lastEventId = "") =>
      listeners.get(k)?.({ data: JSON.stringify(data), lastEventId }),
    posts,
  };
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

  it("排队 notice：mobile 写入提示位，桌面 notice 不写", async () => {
    const { chat, emit } = mkChat();
    await chat.send("跨 surface 排队");
    emit("notice", { kind: "notice", surface: "mobile", text: "另一个窗口还在说，等它说完…" });
    expect(chat.error.value).toBe("另一个窗口还在说，等它说完…");
    emit("notice", { kind: "notice", surface: "desktop", text: "桌面 notice" });
    expect(chat.error.value).toBe("另一个窗口还在说，等它说完…");
  });

  // 待批角标已上移为独立 composable（M2 TabBar 用），用例见 pending-badge.test.ts

  it("seq 去重：已见 seq 的重放帧整帧丢弃（同文本不重复拼接、run_done 不误收口）", async () => {
    const { chat, emit } = mkChat();
    await chat.send("断线重连场景");
    emit("final_reply_chunk", { kind: "final_reply_chunk", text: "你好", surface: "mobile" }, "5");
    emit("final_reply_chunk", { kind: "final_reply_chunk", text: "你好", surface: "mobile" }, "5"); // 补帧重放已见 seq → 丢弃
    emit("final_reply_chunk", { kind: "final_reply_chunk", text: "，接着说", surface: "mobile" }, "6"); // 新帧照收
    expect(chat.messages.value[1].text).toBe("你好，接着说");
    emit("run_done", { id: "mob_1" }, "5"); // 已见 seq 的 run_done 重放 → 不收口
    expect(chat.messages.value[1].done).toBe(false);
    emit("run_done", { id: "mob_1" }, "7");
    expect(chat.messages.value[1].done).toBe(true);
  });

  it("服务重启 seq 归一：seenSeq 让位新纪元（不永久吞帧）", async () => {
    const { chat, emit } = mkChat();
    await chat.send("大脑重启场景");
    emit("final_reply_chunk", { kind: "final_reply_chunk", text: "旧纪元", surface: "mobile" }, "500");
    emit("final_reply_chunk", { kind: "final_reply_chunk", text: "新纪元", surface: "mobile" }, "1"); // 重启后 seq 从 1 重新计数
    expect(chat.messages.value[1].text).toBe("旧纪元新纪元"); // 新纪元首帧不被旧 seenSeq 吞掉
  });

  it("重启聋窗（泛化）：新纪元首帧 seq 非必为 1——任意后跳且非重复帧序都让位重计", async () => {
    const { chat, emit } = mkChat();
    await chat.send("重启重连场景");
    emit("final_reply_chunk", { kind: "final_reply_chunk", text: "旧纪元", surface: "mobile" }, "500");
    // 服务端重启 + 重连时已带上部分新纪元帧：首见新纪元帧 seq=3（非 1，且从未见过）
    emit("final_reply_chunk", { kind: "final_reply_chunk", text: "新纪元", surface: "mobile" }, "3");
    expect(chat.messages.value[1].text).toBe("旧纪元新纪元"); // 不被旧水位 500 吞掉
    emit("final_reply_chunk", { kind: "final_reply_chunk", text: "新纪元", surface: "mobile" }, "3"); // 同 seq 重放 → 丢弃
    expect(chat.messages.value[1].text).toBe("旧纪元新纪元");
  });

  it("newChat 清 myRunId：旧轮迟到的 run_done 不收口新气泡（send 在途窗口）", async () => {
    const { chat, emit } = mkChat();
    await chat.send("第一轮"); // run_id=mob_1，该轮已收口
    chat.newChat();
    const pending = chat.send("第二轮"); // 不 await：fetch 返回前 myRunId 尚未更新（旧值窗口）
    emit("run_done", { id: "mob_1" }); // 第一轮的迟到收口——myRunId 未清时会误收口新气泡
    expect(chat.messages.value[1].done).toBe(false);
    await pending;
    emit("final_reply_chunk", { kind: "final_reply_chunk", text: "新气泡", surface: "mobile" });
    expect(chat.messages.value[1].text).toBe("新气泡"); // 气泡仍活着收流
    emit("run_done", { id: "mob_2" }); // 自己的轮结束才收口
    expect(chat.messages.value[1].done).toBe(true);
  });

  it("loadHistory：过滤 tool 轮、剥 user 轮面板前缀、重建为已收口消息（busy 清零）", async () => {
    const { chat, emit } = mkChat();
    await chat.send("进行中的一轮"); // pending 气泡等 run_done → busy
    expect(chat.busy.value).toBe(true);
    chat.loadHistory([
      { role: "user", text: "【快捷 面板】帮我看看天气" }, // 面板场景 user 轮：落史时拼了 surface 标记前缀
      { role: "assistant", text: "" }, // 工具调用占位轮（无文本）：不回显
      { role: "tool", text: '{"temp": 30}' }, // 工具轨迹轮：UI 不展示
      { role: "assistant", text: "今天 30 度" },
      { role: "user", text: "普通问题【不是前缀" }, // 普通轮含【】不被误剥
      { role: "assistant", text: "普通回答" },
    ]);
    expect(chat.messages.value.map((m) => [m.role, m.text])).toEqual([
      ["user", "帮我看看天气"],
      ["assistant", "今天 30 度"],
      ["user", "普通问题【不是前缀"],
      ["assistant", "普通回答"],
    ]);
    expect(chat.messages.value.every((m) => m.done)).toBe(true);
    expect(chat.busy.value).toBe(false); // busy 由 messages 派生：全 done 即清零
  });

  it("historyMode：loadHistory 后旧轮迟到的 final_reply/run_done 不覆写历史", async () => {
    const { chat, emit } = mkChat();
    await chat.send("切走前的在途轮"); // mob_1，final_reply 尚未到达
    emit("final_reply_chunk", { kind: "final_reply_chunk", text: "半截", surface: "mobile" });
    chat.loadHistory([{ role: "user", text: "历史问题" }, { role: "assistant", text: "历史回答" }]);
    // 切会话后旧轮收尾帧迟到：final_reply 会覆写末条历史消息、run_done 误收口——historyMode 期间全丢弃
    emit("final_reply", { kind: "final_reply", text: "旧轮的完整回答", surface: "mobile" });
    emit("run_done", { id: "mob_1" });
    expect(chat.messages.value.map((m) => m.text)).toEqual(["历史问题", "历史回答"]);
    expect(chat.messages.value.every((m) => m.done)).toBe(true);
  });

  it("historyMode：新 send 清位，此后帧恢复正常消费（历史消息原样）", async () => {
    const { chat, emit } = mkChat();
    chat.loadHistory([{ role: "user", text: "历史问题" }, { role: "assistant", text: "历史回答" }]);
    await chat.send("接着历史聊");
    emit("final_reply_chunk", { kind: "final_reply_chunk", text: "新答", surface: "mobile" });
    emit("final_reply", { kind: "final_reply", text: "新答完整", surface: "mobile" });
    emit("run_done", { id: "mob_1" });
    expect(chat.messages.value).toHaveLength(4); // 历史 2 条 + 新问新答
    expect(chat.messages.value[3].text).toBe("新答完整");
    expect(chat.messages.value[3].done).toBe(true);
    expect(chat.messages.value[1].text).toBe("历史回答"); // 历史消息未被旧帧碰过
  });

  it("默认 url 工厂读 lastSeq：手动重连 start 时 URL 带上 last_event_id 断点", async () => {
    const listeners = new Map<string, (e: { data: string; lastEventId: string }) => void>();
    const es: EventSourceLike = {
      addEventListener: (k, cb) => listeners.set(k, cb),
      close: vi.fn(),
      onopen: () => {},
      onerror: () => {},
    };
    const urls: string[] = [];
    const fetchImpl = vi.fn(async () =>
      new Response(JSON.stringify({ ok: true, run_id: "mob_1", conversation_id: "" }), { status: 200 }));
    // 不传 url 工厂 → 默认工厂；makeES 记录每次 start 实际用的 URL
    const chat = useChat(
      { host: "http://x:19527", token: "t" } as ConnConfig,
      undefined,
      (u) => (urls.push(u), es),
      fetchImpl as unknown as typeof fetch,
    );
    expect(urls[0]).toBe("http://x:19527/v1/events?token=t"); // 首连无断点
    listeners.get("final_reply_chunk")?.({ data: JSON.stringify({ text: "a", surface: "mobile" }), lastEventId: "5" });
    expect(chat.stream.lastSeq.value).toBe(5);
    chat.stream.start(); // Chat.vue 的 5s 兜底重连走的正是这条路径
    expect(urls[1]).toBe("http://x:19527/v1/events?token=t&last_event_id=5"); // 续传断点生效
  });
});

describe("uuid（非安全上下文降级）", () => {
  it("getRandomValues 路径生成合法 UUID v4", async () => {
    // 动态 import 拿模块内 uuid 不可直达（未导出）——经由 useChat 间接验证：
    // 屏蔽 randomUUID 模拟手机 http 内网访问，构造 useChat 不应抛错且 id 形如 UUID
    const orig = crypto.randomUUID;
    (crypto as any).randomUUID = undefined;
    try {
      const { useChat } = await import("./chat");
      const fakeES = { addEventListener: () => {}, close: () => {}, onopen: null, onerror: null };
      const c = useChat({ host: "http://x", token: "t" }, () => "u", () => fakeES as never);
      expect(c.conversationId.value).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
      c.stream.stop();
    } finally {
      (crypto as any).randomUUID = orig;
    }
  });
});
