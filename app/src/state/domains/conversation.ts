/**
 * conversation 域：全部窗口共享的对话呈现状态。
 *
 * 架构（Rust 统一持久化后）：
 * - 消息/会话元数据的权威存储在 Rust 主进程 SQLite（Rust 是唯一写者）。
 * - 事件驱动消息（AI 回复 / proc / panelLink / notice…）：Rust EventRecorder 在事件流处
 *   落库；webview 的 appendMessage/syncMessage/upsertPanelLink 只更新内存（实时渲染），不写库。
 * - 会话管理 / 截断（用户操作，单窗口发起）：经 ConversationBackend 调 Rust command。
 * - UIState（draft/scrollTop/filter/processed/pending）是单窗 UI 状态，仍走本地 KVStore。
 * - 内存缓存为渲染视图；启动 hydrate 从 Rust 拉取重建（恢复以 Rust 为准）。
 *
 * 权威声明：本域是 UI 呈现快照；sidecar history.json 是模型上下文权威。
 * 两者是投影关系，允许语义差异，不做强一致。
 */
import type { ConversationMeta, ConversationUIState, KVStore, Message, MessagePayload, MessageRole, PendingApproval, ProcessedItem } from "../types";
import { TABLES } from "../persist-engine";
import { registerDomain } from "../schema-registry";
import type { ConversationBackend } from "./conversation-backend";

export const MESSAGE_KEY_SEP = ":";

export interface ConversationOptions {
  /** 每会话消息截尾上限（内存视图对齐 Rust 截尾） */
  maxMessagesPerConversation?: number;
  /** 会话总数上限 */
  maxConversations?: number;
  /** 落库失败回调（默认 console.warn） */
  onWriteFailed?: (err: unknown) => void;
}

export type MessageInput = {
  /** 缺省自动生成（crypto.randomUUID 或 fallback） */
  id?: string;
  role: MessageRole;
  payload: MessagePayload;
  ts?: number;
  /** 敏感内容：只活内存，永不落盘 */
  ephemeral?: boolean;
};

export function newId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function isValidUIState(raw: unknown): raw is ConversationUIState {
  if (typeof raw !== "object" || raw === null) return false;
  const s = raw as Record<string, unknown>;
  return (
    typeof s.conversationId === "string" &&
    typeof s.draft === "string" &&
    typeof s.scrollTop === "number" &&
    typeof s.filter === "string" &&
    Array.isArray(s.processed) &&
    Array.isArray(s.pendingApprovals)
  );
}

function emptyUIState(conversationId: string): ConversationUIState {
  return { conversationId, draft: "", scrollTop: 0, filter: "", processed: [], pendingApprovals: [] };
}

/** conversation 域描述符（UIState 记录级校验；消息/会话元数据已由 Rust 校验） */
registerDomain("conversation:ui", {
  domain: "conversation",
  version: 1,
  ttl: null,
  validate: isValidUIState,
});

const DEFAULT_MAX_MESSAGES = 500;
const DEFAULT_MAX_CONVERSATIONS = 100;

export class ConversationDomain {
  private readonly backend: ConversationBackend;
  private readonly store: KVStore;
  private readonly maxMessages: number;
  private readonly maxConversations: number;
  private readonly onWriteFailed: (err: unknown) => void;

  /** 内存渲染视图：convId → 消息（seq 升序）。权威在 Rust，此为只读缓存 + 实时增量。 */
  private readonly messagesByConv = new Map<string, Message[]>();
  private readonly metas = new Map<string, ConversationMeta>();
  private readonly uiStates = new Map<string, ConversationUIState>();
  private activeConversationId: string | null = null;
  private hydrated = false;
  private lastTs = 0;
  private readonly pendingWrites = new Set<Promise<void>>();

  constructor(backend: ConversationBackend, store: KVStore, options: ConversationOptions = {}) {
    this.backend = backend;
    this.store = store;
    this.maxMessages = options.maxMessagesPerConversation ?? DEFAULT_MAX_MESSAGES;
    this.maxConversations = options.maxConversations ?? DEFAULT_MAX_CONVERSATIONS;
    this.onWriteFailed = options.onWriteFailed ?? ((err) => console.warn("[conversation] persist failed", err));
  }

  // ---- 生命周期 ----

  /** 恢复：消息/会话从 Rust 拉取（活跃会话立即拉，其余切到再拉），UIState 从本地 KVStore 拉取 */
  async hydrate(): Promise<void> {
    if (this.hydrated) return;
    const [metas, active, uiEntries] = await Promise.all([
      this.backend.listConversations().catch(() => [] as ConversationMeta[]),
      this.backend.getActiveConversationId().catch(() => null),
      this.store.entries<unknown>(TABLES.conversationUi).catch(() => [] as Array<{ key: string; value: unknown }>),
    ]);
    for (const meta of metas) this.metas.set(meta.id, meta);
    if (active) {
      this.activeConversationId = active;
      await this.ensureMessages(active);
    }
    for (const { value } of uiEntries) {
      if (isValidUIState(value)) this.uiStates.set(value.conversationId, value);
    }
    this.hydrated = true;
  }

  /** 补拉某会话消息。force=true 时无条件从 Rust 重拉（切会话/跨窗刷新：
   *  内存缓存可能已过时——期间该会话在 Rust 侧被别的窗口或在途 run 写入过）。 */
  private async ensureMessages(id: string, force = false): Promise<void> {
    if (!force && this.messagesByConv.has(id)) return;
    const msgs = await this.backend.getMessages(id).catch(() => null);
    if (msgs === null) {
      // 后端不可用：保留已有内存视图，不清空（降级不丢用户可见内容）
      if (!this.messagesByConv.has(id)) this.messagesByConv.set(id, []);
      return;
    }
    this.messagesByConv.set(id, msgs.sort((a, b) => a.seq - b.seq));
  }

  /** 组件切会话/跨窗刷新时调用：从 Rust 权威重拉（force 默认 true，避免过时缓存） */
  async loadMessages(id: string, force = true): Promise<Message[]> {
    await this.ensureMessages(id, force);
    return this.getMessages(id);
  }

  /** 重拉会话列表 + 活跃指针（Rust 侧新建会话 / 别的窗口改动后同步；hydrate 幂等不覆盖此路径） */
  async refreshConversations(): Promise<void> {
    const [metas, active] = await Promise.all([
      this.backend.listConversations().catch(() => null),
      this.backend.getActiveConversationId().catch(() => null),
    ]);
    if (metas) {
      this.metas.clear();
      for (const meta of metas) this.metas.set(meta.id, meta);
      // 清掉已不存在会话的消息缓存（别的窗口删过）
      for (const id of [...this.messagesByConv.keys()]) {
        if (!this.metas.has(id)) this.messagesByConv.delete(id);
      }
    }
    if (active !== null) this.activeConversationId = active || null;
  }

  // ---- 会话管理（用户操作，单窗口发起，经后端写 Rust）----

  listConversations(): ConversationMeta[] {
    return [...this.metas.values()].sort((a, b) => b.updatedAt - a.updatedAt);
  }

  getConversation(id: string): ConversationMeta | undefined {
    return this.metas.get(id);
  }

  /** 单调时钟：同一毫秒内多次创建/更新时保证 updatedAt 严格递增（会话排序/驱逐正确） */
  private monotonicNow(): number {
    this.lastTs = Math.max(Date.now(), this.lastTs + 1);
    return this.lastTs;
  }

  async createConversation(title = "新对话"): Promise<ConversationMeta> {
    let meta: ConversationMeta;
    try {
      meta = await this.backend.createConversation(title);
    } catch (err) {
      // 后端不可用（非 Tauri / Rust 未就绪）：降级本地生成，仅内存态
      this.onWriteFailed(err);
      const now = this.monotonicNow();
      meta = { id: newId(), title, preview: "", createdAt: now, updatedAt: now, messageCount: 0 };
    }
    this.metas.set(meta.id, meta);
    this.messagesByConv.set(meta.id, []);
    this.uiStates.set(meta.id, emptyUIState(meta.id));
    this.activeConversationId = meta.id; // 新会话即活跃（后端 create 已设指针，前端对齐内存）
    this.evictOldestConversations();
    this.writeUIState(this.uiStates.get(meta.id)!);
    return meta;
  }

  /** 级联删除：后端删消息+会话，前端清内存 + 清 UIState */
  async removeConversation(id: string): Promise<void> {
    try {
      await this.backend.removeConversation(id);
    } catch (err) {
      this.onWriteFailed(err);
    }
    this.removeConversationCache(id);
    this.write({ table: TABLES.conversationUi, key: id, value: undefined });
  }

  private removeConversationCache(id: string): void {
    this.metas.delete(id);
    this.messagesByConv.delete(id);
    this.uiStates.delete(id);
    if (this.activeConversationId === id) this.activeConversationId = null;
  }

  /** 联动清理：清空整个对话域（clear_brain_data 前端对应侧） */
  async clearAll(): Promise<void> {
    try {
      await this.backend.clearAll();
    } catch (err) {
      this.onWriteFailed(err);
    }
    try {
      await this.store.clear(TABLES.conversationUi);
    } catch (err) {
      this.onWriteFailed(err);
    }
    this.metas.clear();
    this.messagesByConv.clear();
    this.uiStates.clear();
    this.activeConversationId = null;
  }

  private evictOldestConversations(): void {
    const sorted = this.listConversations();
    while (sorted.length > this.maxConversations) {
      const oldest = sorted[sorted.length - 1];
      if (!oldest) break;
      void this.removeConversation(oldest.id);
      sorted.pop();
    }
  }

  /** 显式重命名会话（组件触发；后续用户消息不覆盖）。fire-and-forget */
  updateMetaTitle(id: string, title: string): void {
    const meta = this.metas.get(id);
    if (!meta) return;
    const trimmed = title.trim();
    if (trimmed) meta.title = trimmed;
    meta.updatedAt = this.monotonicNow();
    this.track(this.backend.updateConversationTitle(id, trimmed || meta.title));
  }

  // ---- 活动会话 ----

  getActiveConversationId(): string | null {
    return this.activeConversationId;
  }

  async setActiveConversationId(id: string): Promise<void> {
    this.activeConversationId = id || null;
    try {
      await this.backend.setActiveConversationId(id);
    } catch (err) {
      this.onWriteFailed(err);
    }
  }

  // ---- 消息（事件驱动，webview 只更新内存；Rust EventRecorder 已落库）----

  getMessages(id: string): Message[] {
    return this.messagesByConv.get(id) ?? [];
  }

  /** 追加消息到内存视图（实时渲染）。持久化由 Rust 在事件流/run_input 统一负责。 */
  appendMessage(conversationId: string, input: MessageInput): Message {
    const list = this.messagesByConv.get(conversationId) ?? [];
    const lastSeq = list.length ? list[list.length - 1].seq : -1;
    const msg: Message = {
      id: input.id ?? newId(),
      conversationId,
      seq: lastSeq + 1,
      role: input.role,
      payload: input.payload,
      ts: input.ts ?? Date.now(),
    };
    list.push(msg);
    this.messagesByConv.set(conversationId, list);
    this.touchMeta(conversationId, msg);
    this.trim(conversationId);
    return msg;
  }

  /** 同步内存消息（流式结束调用）：按 id 查缓存，已存在 → 更新；不存在 → 追加。 */
  syncMessage(conversationId: string, input: MessageInput): Message {
    const list = this.messagesByConv.get(conversationId) ?? [];
    const existing = input.id ? list.find((m) => m.id === input.id) : undefined;
    if (existing) {
      existing.role = input.role;
      existing.payload = input.payload;
      existing.ts = input.ts ?? existing.ts;
      return existing;
    }
    return this.appendMessage(conversationId, input);
  }

  /** 协作信号查重（内存）：最近一条 panelLink 存在则原地更新文案，否则追加。 */
  upsertPanelLink(conversationId: string, text: string): Message {
    const list = this.messagesByConv.get(conversationId) ?? [];
    const last = [...list].reverse().find((m) => m.payload.panelLink === true);
    if (last) {
      last.payload.text = text;
      last.ts = Date.now();
      return last;
    }
    return this.appendMessage(conversationId, { role: "ai", payload: { text, panelLink: true } });
  }

  /** 截断到前 keepCount 条（重新生成/编辑重发：其后对话作废）。用户操作，经后端写 Rust。 */
  truncateMessages(conversationId: string, keepCount: number): void {
    const list = this.messagesByConv.get(conversationId);
    if (list && list.length > keepCount) {
      list.splice(keepCount);
      const meta = this.metas.get(conversationId);
      if (meta) {
        meta.messageCount = list.length;
        meta.updatedAt = this.monotonicNow();
      }
    }
    this.track(this.backend.truncateMessages(conversationId, keepCount));
  }

  /** 截尾：超上限裁剪最老（内存视图对齐；Rust 侧 db 自行截尾） */
  private trim(conversationId: string): void {
    const list = this.messagesByConv.get(conversationId);
    if (!list || list.length <= this.maxMessages) return;
    list.splice(0, list.length - this.maxMessages);
  }

  private touchMeta(conversationId: string, msg: Message): void {
    const meta = this.metas.get(conversationId);
    if (!meta) return;
    meta.updatedAt = this.monotonicNow();
    meta.messageCount = this.messagesByConv.get(conversationId)?.length ?? 0;
    const text = msg.payload.text.trim();
    if (text) {
      meta.preview = text.slice(0, 60); // preview 对齐 Rust 侧：取最后一条文本（不分角色）
      if (msg.role === "user" && meta.title === "新对话") meta.title = text.slice(0, 24); // 内存兜底；Rust 侧由组件 updateMetaTitle 写入
    }
  }

  // ---- UI 呈现状态（draft/scrollTop/filter/processed/pending；单窗本地 KVStore） ----

  getUIState(conversationId: string): ConversationUIState {
    let state = this.uiStates.get(conversationId);
    if (!state) {
      state = emptyUIState(conversationId);
      this.uiStates.set(conversationId, state);
    }
    return state;
  }

  updateUIState(conversationId: string, partial: Partial<Omit<ConversationUIState, "conversationId">>): void {
    const state = this.getUIState(conversationId);
    if (partial.draft !== undefined) state.draft = partial.draft;
    if (partial.scrollTop !== undefined) state.scrollTop = partial.scrollTop;
    if (partial.filter !== undefined) state.filter = partial.filter;
    if (partial.processed !== undefined) state.processed = partial.processed;
    if (partial.pendingApprovals !== undefined) state.pendingApprovals = partial.pendingApprovals;
    this.writeUIState(state);
  }

  setProcessed(conversationId: string, items: ProcessedItem[]): void {
    this.updateUIState(conversationId, { processed: items.slice(-200) });
  }

  setPendingApprovals(conversationId: string, items: PendingApproval[]): void {
    this.updateUIState(conversationId, { pendingApprovals: items.sort((a, b) => b.createdAt - a.createdAt).slice(0, 20) });
  }

  setDraft(conversationId: string, draft: string): void {
    this.updateUIState(conversationId, { draft });
  }

  setScrollTop(conversationId: string, scrollTop: number): void {
    this.updateUIState(conversationId, { scrollTop });
  }

  // ---- 内部写路径（UIState → KVStore；会话操作 → 后端。静默降级 + 失败回调） ----

  /** 等待所有在途写入完成（测试/退出前 flush；生产路径不阻塞 UI） */
  async flush(): Promise<void> {
    await Promise.allSettled([...this.pendingWrites]);
  }

  /** fire-and-forget 后端调用：失败回调 + 吞掉，跟踪完成（供 flush） */
  private track(p: Promise<unknown>): void {
    const tracked: Promise<void> = p.then(
      () => undefined,
      (err) => this.onWriteFailed(err),
    );
    this.pendingWrites.add(tracked);
    void tracked.finally(() => this.pendingWrites.delete(tracked));
  }

  private write(...ops: Array<{ table: string; key: string; value?: unknown }>): void {
    const p = this.store.batch(ops).catch((err) => this.onWriteFailed(err));
    this.pendingWrites.add(p);
    void p.finally(() => this.pendingWrites.delete(p));
  }

  private writeUIState(state: ConversationUIState): void {
    this.write({ table: TABLES.conversationUi, key: state.conversationId, value: state });
  }
}
