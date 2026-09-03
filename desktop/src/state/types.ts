/** SessionState 体系跨域公共类型 */

import type { WebviewPayload } from "../lib/webview-source";
import type { RunMetrics } from "../protocol/brain-types";

/** 状态域标识：域间平行，仅 surface 域内存在嵌套链 */
export type DomainId = "conversation" | "surface" | "window";

/** 域描述符：统一容错配置（SchemaRegistry 注册项） */
export interface DomainDescriptor<T> {
  readonly domain: DomainId;
  /** schema 版本：不符即视为损坏（该条记录降级/丢弃） */
  readonly version: number;
  /** 过期时间 ms；null = 不过期 */
  readonly ttl: number | null;
  /** 结构校验：非法返回 null */
  readonly validate: (raw: unknown) => T | null;
}

/** KV 存储抽象：引擎接口（IndexedDB / 内存 / localStorage 均可实现） */
export interface KVStore {
  get<T = unknown>(table: string, key: string): Promise<T | null>;
  put(table: string, key: string, value: unknown): Promise<void>;
  delete(table: string, key: string): Promise<void>;
  clear(table: string): Promise<void>;
  /** 表内全部条目（无顺序保证） */
  entries<T = unknown>(table: string): Promise<Array<{ key: string; value: T }>>;
  /** 原子批量写入：value 为 undefined 表示删除 */
  batch(ops: Array<{ table: string; key: string; value?: unknown }>): Promise<void>;
}

// ---- conversation 域 ----

export type MessageRole = "user" | "ai" | "sys";

/** proc 过程展示：只持久化投影（action/result 完整对象不落盘） */
export interface ProcProjection {
  label: string;
  done: boolean;
  ok?: boolean;
}

/** 能力边界卡投影（与 proc 同水位：完整 capability 摘要不落盘，只留卡面素材） */
export interface GapProjection {
  through: string;
  available: string[];
  missing: string[];
  note: string;
}

/** 溯源引用（仅 AI 消息） */
export interface RunRef {
  label: string;
  detail: string;
  ok: boolean;
}

/** run 统计（token/费用/耗时）：final_reply 的 AI 气泡挂 indicator bar。
 *  单一事实源在 protocol/brain-types（与 sidecar 事件载荷同形），此处 re-export 保持既有引用路径。 */
export type { RunMetrics } from "../protocol/brain-types";

/** 消息载荷：UI 呈现所需的最小字段集 */
export interface MessagePayload {
  text: string;
  panelLink?: boolean;
  proc?: ProcProjection;
  gap?: GapProjection;
  refs?: RunRef[];
  halted?: boolean;
  icon?: "clock" | "alert";
  metrics?: RunMetrics;
}

/** 持久化的消息记录（messages 表条目） */
export interface Message {
  id: string;
  conversationId: string;
  /** 会话内单调序号（排序不依赖 ts，防时钟回拨） */
  seq: number;
  role: MessageRole;
  payload: MessagePayload;
  ts: number;
  /** 敏感内容：只活内存，永不落盘（对齐 Rust 侧标记） */
  ephemeral?: boolean;
}

/** 会话元数据（conversations 表条目） */
export interface ConversationMeta {
  id: string;
  title: string;
  preview: string;
  createdAt: number;
  updatedAt: number;
  messageCount: number;
}

/** 待审批快照（重启后暂停、不自动执行） */
export interface PendingApproval {
  id: string;
  tool_id: string;
  label: string;
  detail?: string;
  createdAt: number;
}

/** 已办活动（隐私安全投影） */
export interface ProcessedItem {
  id: string;
  taskId?: string;
  tool_id: string;
  label: string;
  ok: boolean;
  at: number;
}

/** 会话级 UI 呈现状态（conversation-ui 表条目；高频小写） */
export interface ConversationUIState {
  conversationId: string;
  draft: string;
  scrollTop: number;
  /** 筛选/折叠态，按需扩展 */
  filter: string;
  processed: ProcessedItem[];
  pendingApprovals: PendingApproval[];
}

// ---- surface 域 ----

// scene 持久化的呈现档位只有公共三态；peek 是宿主对 Stage 的瞬态 compact placement
// （架构 §6.5），用完即收、不成为恢复点，因此永远不落盘——旧数据里的 "peek"
// 由 domains/surface.ts 读取时归一为 stage。
export type CapabilityPresentation = "inline" | "stage" | "focus";

/** scene：布局壳（嵌套链第一层） */
export interface SurfaceScene {
  panel: string;
  visible: boolean;
  presentation: CapabilityPresentation;
  tab: string;
}

/** panel：面板数据（嵌套链第二层；大载荷由 validate/容量截断守卫） */
export interface SurfacePanel {
  panel: string;
  title: string;
  schema: Record<string, unknown> | null;
  data: Record<string, unknown>;
  webview: WebviewPayload | null;
}

/** interact：面板内交互态（嵌套链第三层；乐观可失效） */
export interface SurfaceInteract {
  panel: string;
  expandedNodes: string[];
  searchQuery: string;
  activeTab: string;
}

// ---- window 域 ----

export type WindowId = "main" | "pet" | "panel";

/** 窗口状态：只存引用（展示什么），数据本体在 conversation/surface 域 */
export interface WindowState {
  windowId: WindowId;
  bounds: { x: number; y: number; width: number; height: number } | null;
  visible: boolean;
  alwaysOnTop: boolean;
  focusedConversationId: string | null;
  focusedPanelId: string | null;
}
