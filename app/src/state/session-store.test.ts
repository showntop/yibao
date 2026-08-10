import { describe, expect, it } from "vitest";
import { MemoryKVStore } from "./persist-engine";
import { SessionStore } from "./session-store";
import { orchestrateRestore } from "./restore-orchestrator";
import { MemoryConversationBackend } from "./domains/conversation-backend";

describe("SessionStore 集成", () => {
  it("restore hydrates all domains and reports engine readiness", async () => {
    const store = new MemoryKVStore();
    const session = SessionStore.create({ engine: store });
    const report = await orchestrateRestore(session);
    expect(report.engineReady).toBe(true);
    expect(report.ok.conversation).toBe(true);
    expect(report.ok.surface).toBe(true);
    expect(report.ok.window).toBe(true);
  });

  it("full conversation flow survives re-instantiation", async () => {
    const store = new MemoryKVStore();
    const backend = new MemoryConversationBackend();
    const s1 = SessionStore.create({ engine: store, conversationBackend: backend });
    await s1.restore();
    const conv = await s1.conversation.createConversation();
    await s1.conversation.setActiveConversationId(conv.id);
    s1.conversation.setDraft(conv.id, "草稿");
    await s1.conversation.flush();
    // 模拟 Rust EventRecorder 已落库的对话消息（生产由 Rust 写，webview 不写）
    backend.seedMessages(conv.id, [
      { id: "m1", conversationId: conv.id, seq: 0, role: "user", payload: { text: "你好" }, ts: 1, ephemeral: false },
      { id: "m2", conversationId: conv.id, seq: 1, role: "ai", payload: { text: "⇢ 正在和「插件」协作", panelLink: true }, ts: 2, ephemeral: false },
    ]);

    // 模拟重启：新 SessionStore + 同引擎 + 同后端
    const s2 = SessionStore.create({ engine: store, conversationBackend: backend });
    const report = await orchestrateRestore(s2);
    expect(report.activeConversationId).toBe(conv.id);
    const msgs = s2.conversation.getMessages(conv.id);
    expect(msgs).toHaveLength(2);
    expect(msgs[1].payload.panelLink).toBe(true);
    expect(s2.conversation.getUIState(conv.id).draft).toBe("草稿");
  });

  it("surface chain: scene + panel restore for multi-window consistency", async () => {
    const store = new MemoryKVStore();
    const s1 = SessionStore.create({ engine: store });
    await s1.restore();
    s1.surface.setScene({ panel: "p:board", visible: true, presentation: "stage", tab: "home" });
    s1.surface.setPanel({ panel: "p:board", title: "面板", schema: null, data: { rows: [1] }, webview: null });
    await s1.surface.flush();

    const s2 = SessionStore.create({ engine: store });
    const report = await orchestrateRestore(s2);
    expect(report.scene?.panel).toBe("p:board");
    expect(report.panel?.title).toBe("面板");
    expect(s2.surface.getSnapshot().interact).toBeNull();
  });

  it("window domain reports per-window focus", async () => {
    const store = new MemoryKVStore();
    const session = SessionStore.create({ engine: store });
    await session.restore();
    session.window.updateState("main", { visible: true, focusedConversationId: "c1" });
    session.window.updateState("pet", { visible: true });
    await session.window.flush();
    expect(session.window.getAllStates().length).toBe(2);
    expect(session.window.getState("main")!.focusedConversationId).toBe("c1");
  });

  it("clearAll wipes every domain", async () => {
    const store = new MemoryKVStore();
    const session = SessionStore.create({ engine: store });
    await session.restore();
    const conv = await session.conversation.createConversation();
    session.conversation.appendMessage(conv.id, { role: "user", payload: { text: "x" } });
    session.surface.setScene({ panel: "p:x", visible: true, presentation: "stage", tab: "home" });
    session.window.updateState("main", { visible: true });
    await session.conversation.flush();
    await session.surface.flush();
    await session.window.flush();
    await session.clearAll();
    expect(session.conversation.listConversations()).toHaveLength(0);
    expect(session.surface.getScene()).toBeNull();
    expect(session.window.getAllStates()).toHaveLength(0);
  });

  it("engine failure degrades to memory-only without throwing", async () => {
    // 注入会抛错的引擎：restore 应返回 engineReady=false，不向上抛
    const broken: MemoryKVStore = new MemoryKVStore();
    const session = SessionStore.create({
      engine: {
        get: async () => { throw new Error("db down"); },
        put: async () => { throw new Error("db down"); },
        delete: async () => { throw new Error("db down"); },
        clear: async () => { throw new Error("db down"); },
        entries: async () => { throw new Error("db down"); },
        batch: async () => { throw new Error("db down"); },
      },
    });
    const report = await session.restore();
    expect(report.engineReady).toBe(false);
    void broken;
  });
});
