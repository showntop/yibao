import { describe, expect, it, beforeEach } from "vitest";
import { MemoryKVStore, TABLES } from "../persist-engine";
import { ConversationDomain } from "./conversation";
import { MemoryConversationBackend } from "./conversation-backend";
import type { MessageInput } from "./conversation";
import type { Message } from "../types";

function makeDomain(overrides: { maxMessages?: number; maxConversations?: number } = {}) {
  const backend = new MemoryConversationBackend();
  const store = new MemoryKVStore();
  const domain = new ConversationDomain(backend, store, {
    maxMessagesPerConversation: overrides.maxMessages,
    maxConversations: overrides.maxConversations,
  });
  return { backend, store, domain };
}

const user = (text: string): MessageInput => ({ role: "user", payload: { text } });
const ai = (text: string): MessageInput => ({ role: "ai", payload: { text } });

function msg(id: string, convId: string, seq: number, role: Message["role"], text: string): Message {
  return { id, conversationId: convId, seq, role, payload: { text }, ts: seq, ephemeral: false };
}

describe("conversation domain", () => {
  let backend: MemoryConversationBackend;
  let store: MemoryKVStore;
  let domain: ConversationDomain;

  beforeEach(() => {
    const ctx = makeDomain();
    backend = ctx.backend;
    store = ctx.store;
    domain = ctx.domain;
  });

  it("creates conversation and appends messages with monotonic seq", async () => {
    const conv = await domain.createConversation();
    expect(conv.title).toBe("新对话");
    const m1 = domain.appendMessage(conv.id, user("你好"));
    const m2 = domain.appendMessage(conv.id, ai("嗨"));
    expect(m1.seq).toBe(0);
    expect(m2.seq).toBe(1);
    expect(domain.getMessages(conv.id).map((m) => m.seq)).toEqual([0, 1]);
  });

  it("createConversation registers in backend and sets active", async () => {
    const conv = await domain.createConversation();
    expect(domain.getActiveConversationId()).toBe(conv.id);
    const list = await backend.listConversations();
    expect(list.map((c) => c.id)).toContain(conv.id);
  });

  it("updates meta on user message: preview, title, messageCount", async () => {
    const conv = await domain.createConversation();
    domain.appendMessage(conv.id, user("帮我写一首诗"));
    const meta = domain.getConversation(conv.id)!;
    expect(meta.preview).toBe("帮我写一首诗");
    expect(meta.title).toBe("帮我写一首诗");
    expect(meta.messageCount).toBe(1);
  });

  it("does not change title once renamed", async () => {
    const conv = await domain.createConversation();
    domain.updateMetaTitle(conv.id, "我的重要对话");
    domain.appendMessage(conv.id, user("后续消息"));
    expect(domain.getConversation(conv.id)!.title).toBe("我的重要对话");
  });

  it("hydrates messages from backend (Rust) on re-instantiation", async () => {
    // 模拟 Rust 已落库的对话：新 domain hydrate 应从 backend 拉到
    backend.seedConversation({ id: "c1", title: "旧会话", preview: "", createdAt: 1, updatedAt: 2, messageCount: 2 });
    backend.seedMessages("c1", [msg("m1", "c1", 0, "user", "第一条"), msg("m2", "c1", 1, "ai", "回复")]);
    backend.seedActive("c1");

    const fresh = new ConversationDomain(backend, store);
    await fresh.hydrate();
    const msgs = fresh.getMessages("c1");
    expect(msgs).toHaveLength(2);
    expect(msgs[0].payload.text).toBe("第一条");
    expect(msgs[1].payload.text).toBe("回复");
    expect(fresh.getActiveConversationId()).toBe("c1");
  });

  it("hydrate only pulls active conversation messages eagerly; others lazy", async () => {
    backend.seedConversation({ id: "c1", title: "活跃", preview: "", createdAt: 1, updatedAt: 3, messageCount: 1 });
    backend.seedMessages("c1", [msg("m1", "c1", 0, "user", "活跃消息")]);
    backend.seedConversation({ id: "c2", title: "其他", preview: "", createdAt: 1, updatedAt: 2, messageCount: 1 });
    backend.seedMessages("c2", [msg("m9", "c2", 0, "user", "其他消息")]);
    backend.seedActive("c1");

    const fresh = new ConversationDomain(backend, store);
    await fresh.hydrate();
    expect(fresh.getMessages("c1")).toHaveLength(1); // 活跃会话已拉
    expect(fresh.getMessages("c2")).toHaveLength(0); // 非活跃未拉
    await fresh.loadMessages("c2"); // 切到再拉
    expect(fresh.getMessages("c2")).toHaveLength(1);
  });

  it("appended messages continue seq after hydrated ones", async () => {
    backend.seedConversation({ id: "c1", title: "", preview: "", createdAt: 1, updatedAt: 2, messageCount: 1 });
    backend.seedMessages("c1", [msg("m1", "c1", 41, "user", "旧")]);
    backend.seedActive("c1");
    const fresh = new ConversationDomain(backend, store);
    await fresh.hydrate();
    const appended = fresh.appendMessage("c1", ai("新"));
    expect(appended.seq).toBe(42); // 接续已恢复消息的最大 seq，而非 list.length
  });

  it("trims messages beyond maxMessagesPerConversation (memory view)", async () => {
    const ctx = makeDomain({ maxMessages: 3 });
    const conv = await ctx.domain.createConversation();
    for (let i = 0; i < 5; i++) ctx.domain.appendMessage(conv.id, ai(`m${i}`));
    const texts = ctx.domain.getMessages(conv.id).map((m) => m.payload.text);
    expect(texts).toEqual(["m2", "m3", "m4"]);
  });

  it("truncateMessages keeps first N in memory and calls backend", async () => {
    const conv = await domain.createConversation();
    for (let i = 0; i < 4; i++) domain.appendMessage(conv.id, ai(`m${i}`));
    domain.truncateMessages(conv.id, 2);
    await domain.flush();
    expect(domain.getMessages(conv.id).map((m) => m.payload.text)).toEqual(["m0", "m1"]);
    expect(domain.getConversation(conv.id)!.messageCount).toBe(2);
  });

  it("removeConversation clears memory and calls backend cascade", async () => {
    const conv = await domain.createConversation();
    domain.appendMessage(conv.id, user("x"));
    domain.setDraft(conv.id, "草稿");
    await domain.removeConversation(conv.id);
    await domain.flush();
    expect(domain.getConversation(conv.id)).toBeUndefined();
    expect(domain.getMessages(conv.id)).toEqual([]);
    expect((await backend.listConversations()).map((c) => c.id)).not.toContain(conv.id);
    // UIState 也清
    expect(store.dump(TABLES.conversationUi).get(conv.id)).toBeUndefined();
  });

  it("evicts oldest conversation beyond maxConversations", async () => {
    const ctx = makeDomain({ maxConversations: 2 });
    const c1 = await ctx.domain.createConversation("一");
    await ctx.domain.createConversation("二");
    await ctx.domain.createConversation("三");
    await ctx.domain.flush();
    const ids = ctx.domain.listConversations().map((c) => c.id);
    expect(ids).toHaveLength(2);
    expect(ids).not.toContain(c1.id); // 最老被驱逐
  });

  it("UIState (draft) persists to local store and hydrates back", async () => {
    const conv = await domain.createConversation();
    domain.setDraft(conv.id, "未发送的草稿");
    await domain.flush();
    const fresh = new ConversationDomain(backend, store);
    await fresh.hydrate();
    expect(fresh.getUIState(conv.id).draft).toBe("未发送的草稿");
  });

  it("clearAll wipes backend + store + memory", async () => {
    const conv = await domain.createConversation();
    domain.appendMessage(conv.id, user("x"));
    domain.setDraft(conv.id, "d");
    await domain.clearAll();
    await domain.flush();
    expect(domain.listConversations()).toHaveLength(0);
    expect(domain.getActiveConversationId()).toBeNull();
    expect(await backend.listConversations()).toHaveLength(0);
    expect(Object.keys(store.dump(TABLES.conversationUi))).toHaveLength(0);
  });

  it("degrades to memory when backend fails (non-Tauri resilience)", async () => {
    const failing = new MemoryConversationBackend();
    failing.createConversation = () => Promise.reject(new Error("rust down"));
    const d = new ConversationDomain(failing, new MemoryKVStore(), { onWriteFailed: () => {} });
    const conv = await d.createConversation(); // 不应抛
    expect(conv.id).toBeTruthy();
    d.appendMessage(conv.id, user("离线消息"));
    expect(d.getMessages(conv.id)).toHaveLength(1);
  });

  it("loadMessages force-refreshes stale cache (fixes: reply lost after switching away and back)", async () => {
    // 场景：在会话 A 流式回复中切走，Rust 侧继续落库；切回时若用过时内存缓存会看不到完整回复
    backend.seedConversation({ id: "a", title: "A", preview: "", createdAt: 1, updatedAt: 2, messageCount: 1 });
    backend.seedMessages("a", [msg("m1", "a", 0, "ai", "首片")]);
    backend.seedActive("a");
    const fresh = new ConversationDomain(backend, store);
    await fresh.hydrate();
    expect(fresh.getMessages("a")[0].payload.text).toBe("首片");
    // 模拟 Rust 在后台把流式消息更新为终态（别的窗口/在途run 写入）
    backend.seedMessages("a", [msg("m1", "a", 0, "ai", "完整回复内容")]);
    // 默认 force=true：从 Rust 重拉，看到终态
    const reloaded = await fresh.loadMessages("a");
    expect(reloaded[0].payload.text).toBe("完整回复内容");
    // force=false 时保留缓存（惰性路径语义）
    backend.seedMessages("a", [msg("m1", "a", 0, "ai", "又变了")]);
    const cached = await fresh.loadMessages("a", false);
    expect(cached[0].payload.text).toBe("完整回复内容");
  });

  it("loadMessages keeps memory view when backend unavailable (no blank screen)", async () => {
    const conv = await domain.createConversation();
    domain.appendMessage(conv.id, ai("内存里的消息"));
    const broken = { ...backend, getMessages: () => Promise.reject(new Error("rust down")) } as unknown as MemoryConversationBackend;
    const d = new ConversationDomain(broken, store, { onWriteFailed: () => {} });
    // 先塞入内存视图，再模拟后端挂掉的重拉：不应清空已有内容
    d.appendMessage(conv.id, ai("已渲染内容"));
    const kept = await d.loadMessages(conv.id);
    expect(kept.length).toBeGreaterThan(0);
  });

  it("refreshConversations syncs list/active created by another window", async () => {
    // 场景：小窗新建了会话（Rust 侧），大窗需要同步列表与活跃指针（hydrate 幂等不覆盖此路径）
    const fresh = new ConversationDomain(backend, store);
    await fresh.hydrate();
    expect(fresh.listConversations()).toHaveLength(0);
    backend.seedConversation({ id: "petnew", title: "小窗建的", preview: "", createdAt: 1, updatedAt: 5, messageCount: 0 });
    backend.seedActive("petnew");
    await fresh.refreshConversations();
    expect(fresh.listConversations().map((c) => c.id)).toEqual(["petnew"]);
    expect(fresh.getActiveConversationId()).toBe("petnew");
  });

  it("refreshConversations drops caches of conversations deleted elsewhere", async () => {
    backend.seedConversation({ id: "gone", title: "将被删", preview: "", createdAt: 1, updatedAt: 2, messageCount: 1 });
    backend.seedMessages("gone", [msg("m1", "gone", 0, "ai", "x")]);
    backend.seedActive("gone");
    const fresh = new ConversationDomain(backend, store);
    await fresh.hydrate();
    expect(fresh.getMessages("gone")).toHaveLength(1);
    await backend.removeConversation("gone"); // 别的窗口删了
    await fresh.refreshConversations();
    expect(fresh.getMessages("gone")).toHaveLength(0);
    expect(fresh.getConversation("gone")).toBeUndefined();
  });

  it("Rust-persisted message shape renders identically after hydrate (cross-process consistency)", async () => {
    // 锚点：Rust EventRecorder 落库的 payload 结构必须与 webview 渲染期望一致。
    // 模拟 Rust 侧各事件产生的持久化消息，hydrate 后字段可被 webview 正确消费。
    backend.seedConversation({ id: "c1", title: "", preview: "", createdAt: 1, updatedAt: 9, messageCount: 5 });
    backend.seedMessages("c1", [
      { id: "m1", conversationId: "c1", seq: 0, role: "user", payload: { text: "帮我查系统" }, ts: 1, ephemeral: false },
      // proc 进行中 → 收尾（Rust update_message_payload 后的终态）
      { id: "m2", conversationId: "c1", seq: 1, role: "sys", payload: { text: "", proc: { label: "查系统", done: true, ok: true } }, ts: 2, ephemeral: false },
      // AI 回复带溯源引用
      { id: "m3", conversationId: "c1", seq: 2, role: "ai", payload: { text: "你是 macOS", refs: [{ label: "查系统", detail: "macOS", ok: true }] }, ts: 3, ephemeral: false },
      // panelLink 协作信号
      { id: "m4", conversationId: "c1", seq: 3, role: "ai", payload: { text: "⇢ 正在和「看板」协作", panelLink: true }, ts: 4, ephemeral: false },
      // interrupted 半成品
      { id: "m5", conversationId: "c1", seq: 4, role: "ai", payload: { text: "说了半句", halted: true }, ts: 5, ephemeral: false },
    ]);
    backend.seedActive("c1");
    const fresh = new ConversationDomain(backend, store);
    await fresh.hydrate();
    const msgs = fresh.getMessages("c1");
    expect(msgs).toHaveLength(5);
    expect(msgs[1].payload.proc).toEqual({ label: "查系统", done: true, ok: true });
    expect(msgs[2].payload.refs?.[0].detail).toBe("macOS");
    expect(msgs[3].payload.panelLink).toBe(true);
    expect(msgs[4].payload.halted).toBe(true);
  });
});
