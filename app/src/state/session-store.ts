/**
 * SessionStore：SessionState 体系门面 —— 组件唯一入口。
 *
 * 组件不碰任何存储 API；所有会话状态读写收敛到这里。
 * 内部组合三个域（conversation / surface / window）+ 一个 KVStore 引擎。
 */
import type { KVStore } from "./types";
import { IdbKVStore } from "./persist-engine";
import { ConversationDomain } from "./domains/conversation";
import { createConversationBackend, type ConversationBackend } from "./domains/conversation-backend";
import { SurfaceDomain } from "./domains/surface";
import { WindowDomain } from "./domains/window";

export interface SessionStoreOptions {
  engine?: KVStore;
  /** 测试注入内存后端；缺省按环境自适应（Tauri → Rust，否则内存降级） */
  conversationBackend?: ConversationBackend;
  maxMessagesPerConversation?: number;
  maxConversations?: number;
  onWriteFailed?: (err: unknown) => void;
}

export class SessionStore {
  readonly conversation: ConversationDomain;
  readonly surface: SurfaceDomain;
  readonly window: WindowDomain;

  private constructor(
    readonly engine: KVStore,
    conversation: ConversationDomain,
    surface: SurfaceDomain,
    window: WindowDomain,
  ) {
    this.conversation = conversation;
    this.surface = surface;
    this.window = window;
  }

  /** 创建门面：缺省引擎为 IndexedDB；测试注入 MemoryKVStore + MemoryConversationBackend */
  static create(options: SessionStoreOptions = {}): SessionStore {
    const engine = options.engine ?? new IdbKVStore();
    const backend = options.conversationBackend ?? createConversationBackend();
    const onWriteFailed = options.onWriteFailed ?? ((err: unknown) => console.warn("[session-store] persist failed", err));
    // conversation 域：消息/会话权威在 Rust（backend），UIState 走 engine
    const conversation = new ConversationDomain(backend, engine, {
      maxMessagesPerConversation: options.maxMessagesPerConversation,
      maxConversations: options.maxConversations,
      onWriteFailed,
    });
    const surface = new SurfaceDomain(engine, { onWriteFailed });
    const window = new WindowDomain(engine, { onWriteFailed });
    return new SessionStore(engine, conversation, surface, window);
  }

  /** 恢复编排：三域并行 hydrate（域级独立容错，失败只降级该域） */
  async restore(): Promise<{ ok: Record<string, boolean>; engineReady: boolean }> {
    const ok: Record<string, boolean> = {};
    const settled = await Promise.all([
      this.conversation.hydrate().then(() => true).catch(() => false),
      this.surface.hydrate().then(() => true).catch(() => false),
      this.window.hydrate().then(() => true).catch(() => false),
    ]);
    ok.conversation = settled[0];
    ok.surface = settled[1];
    ok.window = settled[2];
    // engineReady 反映本地引擎（IndexedDB）可用性：surface/window 是纯 engine 域。
    // conversation 走 Rust backend（独立持久化），不计入此处。
    const engineReady = settled[1] || settled[2];
    return { ok, engineReady };
  }

  /** 联动清理：清空全部会话状态（与 clear_brain_data("history") 前端侧联动） */
  async clearAll(): Promise<void> {
    await Promise.all([this.conversation.clearAll(), this.surface.clearAll(), this.window.clearAll()]);
  }
}
