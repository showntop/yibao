/**
 * conversation 域的持久化后端抽象。
 *
 * 架构：消息/会话的权威存储在 Rust 主进程（SQLite，Rust 是唯一写者）。
 * webview 的"写"分两类：
 * - 事件驱动消息（AI 回复 / proc / notice…）：Rust EventRecorder 在事件流处
 *   统一落库，webview 不写——从架构上消灭多窗双写。
 * - 会话管理 / 截断（用户操作，单窗口发起）：webview 经本后端调 Rust command。
 * 读：启动 hydrate 时从本后端拉取。
 *
 * Tauri 环境用 TauriConversationBackend（invoke Rust command）；
 * 测试 / 非 Tauri 环境用 MemoryConversationBackend（内存降级）。
 */
import { invoke } from "@tauri-apps/api/core";
import type { ConversationMeta, Message } from "../types";

function newId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export interface ConversationBackend {
  listConversations(): Promise<ConversationMeta[]>;
  getMessages(id: string): Promise<Message[]>;
  getActiveConversationId(): Promise<string | null>;
  setActiveConversationId(id: string): Promise<void>;
  createConversation(title?: string): Promise<ConversationMeta>;
  removeConversation(id: string): Promise<void>;
  updateConversationTitle(id: string, title: string): Promise<void>;
  truncateMessages(id: string, keepCount: number): Promise<void>;
  clearAll(): Promise<void>;
}

function isTauri(): boolean {
  return typeof window !== "undefined" && Boolean(
    (window as unknown as { __TAURI_INTERNALS__?: { transformCallback?: unknown } })
      .__TAURI_INTERNALS__?.transformCallback,
  );
}

/** 生产后端：调 Rust command（Rust SQLite 是权威）。 */
export class TauriConversationBackend implements ConversationBackend {
  listConversations(): Promise<ConversationMeta[]> {
    return invoke("list_conversations");
  }
  getMessages(id: string): Promise<Message[]> {
    return invoke("get_conversation_messages", { id, limit: 500 });
  }
  getActiveConversationId(): Promise<string | null> {
    return invoke("get_active_conversation");
  }
  setActiveConversationId(id: string): Promise<void> {
    return invoke("set_active_conversation", { id });
  }
  createConversation(title?: string): Promise<ConversationMeta> {
    return invoke("create_conversation", { title });
  }
  removeConversation(id: string): Promise<void> {
    return invoke("delete_conversation", { id });
  }
  updateConversationTitle(id: string, title: string): Promise<void> {
    return invoke("update_conversation_title", { id, title });
  }
  truncateMessages(id: string, keepCount: number): Promise<void> {
    return invoke("truncate_conversation_messages", { id, keepCount });
  }
  clearAll(): Promise<void> {
    return invoke("clear_conversations");
  }
}

/** 内存后端：测试 / 非 Tauri 环境降级（单测断言 + 浏览器 QA 不崩）。 */
export class MemoryConversationBackend implements ConversationBackend {
  private metas = new Map<string, ConversationMeta>();
  private messages = new Map<string, Message[]>();
  private active: string | null = null;
  private clock = 0; // 单调时钟：同毫秒连续创建保证 updatedAt 递增（驱逐排序正确）

  private now(): number {
    this.clock = Math.max(Date.now(), this.clock + 1);
    return this.clock;
  }

  listConversations(): Promise<ConversationMeta[]> {
    return Promise.resolve([...this.metas.values()].sort((a, b) => b.updatedAt - a.updatedAt));
  }
  getMessages(id: string): Promise<Message[]> {
    return Promise.resolve(this.messages.get(id) ?? []);
  }
  getActiveConversationId(): Promise<string | null> {
    return Promise.resolve(this.active);
  }
  setActiveConversationId(id: string): Promise<void> {
    this.active = id || null;
    return Promise.resolve();
  }
  createConversation(title = "新对话"): Promise<ConversationMeta> {
    const now = this.now();
    const meta: ConversationMeta = { id: newId(), title, preview: "", createdAt: now, updatedAt: now, messageCount: 0 };
    this.metas.set(meta.id, meta);
    this.messages.set(meta.id, []);
    this.active = meta.id;
    return Promise.resolve(meta);
  }
  removeConversation(id: string): Promise<void> {
    this.metas.delete(id);
    this.messages.delete(id);
    if (this.active === id) this.active = null;
    return Promise.resolve();
  }
  updateConversationTitle(id: string, title: string): Promise<void> {
    const m = this.metas.get(id);
    if (m) m.title = title;
    return Promise.resolve();
  }
  truncateMessages(id: string, keepCount: number): Promise<void> {
    const list = this.messages.get(id);
    if (list) this.messages.set(id, list.slice(0, keepCount));
    return Promise.resolve();
  }
  clearAll(): Promise<void> {
    this.metas.clear();
    this.messages.clear();
    this.active = null;
    return Promise.resolve();
  }

  // ---- 测试辅助：模拟 Rust EventRecorder 落库的结果（生产由 Rust 写，webview 不写） ----

  /** 预置会话元数据 */
  seedConversation(meta: ConversationMeta): void {
    this.metas.set(meta.id, meta);
    this.messages.set(meta.id, []);
  }

  /** 预置消息（模拟 Rust 已落库的对话） */
  seedMessages(id: string, messages: Message[]): void {
    this.messages.set(id, [...messages]);
  }

  /** 预置活跃会话指针 */
  seedActive(id: string | null): void {
    this.active = id;
  }

  /** 测试读后端消息数（断言会话管理/截断是否写到了后端） */
  backendMessageCount(id: string): number {
    return this.messages.get(id)?.length ?? 0;
  }
}

/** 环境自适应：Tauri 用 Rust 后端，否则内存降级。 */
export function createConversationBackend(): ConversationBackend {
  return isTauri() ? new TauriConversationBackend() : new MemoryConversationBackend();
}
