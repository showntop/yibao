// 与大脑 sidecar 通信的协议契约（单一事实源）：事件 kind、事件/响应载荷类型。
// 纯类型 + 常量，不依赖 Tauri runtime。
import type { WebviewPayload } from "../lib/webview-source";

export type BrainEventKind =
  | "thought"
  | "action_proposed"
  | "confirmation_needed"
  | "action_result"
  | "final_reply"
  | "final_reply_chunk"
  | "interrupted"
  | "error"
  | "listening"
  | "listening_done"
  | "speaking"
  | "speaking_done"
  | "reminder"
  | "notice"
  | "panel"
  | "panel_data";

export interface BrainAction {
  id?: string;
  tool_id?: string;
  description?: string;
  params?: Record<string, unknown>;
  risk?: number;
  /** 过程展示短标签（sidecar 从技能 label 填，回退 tool_id） */
  label?: string;
  /** 发起面板（coding 审批经广播通道时顶层 surface 为空，action 自带） */
  surface?: string;
}

export interface BrainResult {
  success?: boolean;
  data?: Record<string, unknown>;
  error?: string;
  panel?: string | null;
}

/** kind="panel" 事件的 payload：面板引用 + schema（找不到为 null，前端降级）+ webview HTML + 注入数据。 */
export interface PanelPayload {
  panel?: string;
  /** 面板显示名（插件名 · 面板 label，sidecar 注入；缺省退化用 panel ref） */
  title?: string;
  schema?: unknown;
  /** webview 面板载荷：module 面板为 {url,v}（R4 插件运行时），旧 html 面板为 {html}（srcdoc 渲染）；schema 面板无此字段 */
  webview?: WebviewPayload | null;
  data?: Record<string, unknown>;
  // ---- 能力表面提示（Phase 1）：sidecar 透传的技能建议，宿主裁决用；缺省按老规则 ----
  /** 技能建议的展示级别（建议非命令）；旧插件缺省 null */
  presentation?: "inline" | "peek" | "stage" | "focus" | null;
  /** 注意力级别；旧插件缺省 "suggest" */
  attention?: "quiet" | "suggest" | "focus";
  /** 用户明确意图信号（sidecar 透传，如对话点名「听 XX/看 XX」→ fun 直达方法置 True）：宿主裁决视为 explicit */
  explicit?: boolean;
  /** 跨应用接力对象 {type,id,title}；不依赖面板 DOM */
  object?: { type?: string; id?: string; title?: string } | null;
  /** 面板声明的支持表面范围（manifest [[panel]].surfaces）；缺省 = 全档 */
  surfaces?: string[];
  /** 面板声明的最小宽度（px）；宿主窄窗降级用 */
  min_width?: number;
  /** 面板输入安排（manifest [[panel]].input 四模式）:handoff/none 时壳输入条让位;缺省 inherit */
  input?: "inherit" | "coexist" | "handoff" | "none";
  /** 发起动作 id：前端把表面锚定到对应的过程行 */
  origin?: string;
}

/** final_reply 携带的 run 统计（sidecar loop 聚合：token/费用/耗时）。 */
export interface RunMetrics {
  prompt_tokens: number;
  completion_tokens: number;
  /** 命中缓存的输入 token（详情页标「缓存命中」） */
  cached_tokens: number;
  total_tokens: number;
  /** 费用（元）；未知模型/计费不可靠为 null */
  cost: number | null;
  elapsed_ms: number;
  model: string;
}

/** final_reply 的 payload：正常是 PanelPayload；带 metrics 时含 run 统计 */
export type ReplyPayload = PanelPayload & { metrics?: RunMetrics };

export interface BrainEvent {
  kind: BrainEventKind;
  text?: string;
  action?: BrainAction;
  /** Task 2 攒批载荷：confirmation_needed 一轮多 CONFIRM 的全部待批 action。
   *  旧 sidecar 单条路径仍用 action；前端无 actions 时退化为 [action]。 */
  actions?: BrainAction[];
  result?: BrainResult;
  confirmation_id?: string;
  payload?: PanelPayload;
  /** 会话分流：本次 run 的发起场景（pet / panel:<plugin>）；空 = 全局事件（两窗都处理） */
  surface?: string;
  /** M3 会话归属：run 发起时确定的会话 id，随事件透传（流式中切会话不影响归属）。
   *  空 = 无归属事件（panel/notice 等全局信号，两窗都渲染） */
  conversationId?: string;
  /** 自主权档位（reminder 类主动事件；缺省按 full 处理，兼容旧 sidecar） */
  level?: "quiet" | "bubble" | "full";
  /** morning_recap 深链接：反刍提醒携带 type/day 供 deep-link */
  type?: string;
  day?: string;
  /** ambient 在场陪伴三信号标识（greeting 首活跃/welcome 回归/milestone 专注里程碑），壳侧配宠物反应 */
  signal?: string;
  /** 反应式渲染原料：agents 任务完成事件携带 */
  task?: { id?: string; status?: string; label?: string; prompt?: string };
  /** watch_command 完成事件携带（completed/failed/timed_out/cancelled） */
  status?: string;
  exit_code?: number;
  task_id?: string;
  name?: string;
}

/** 小窗（宠物会话）历史消息行（Rust get_conversation_messages 返回形状；字段为 Rust 序列化 camelCase）。 */
export interface PetMessage {
  id: string;
  conversationId: string;
  seq: number;
  role: string;
  payload: { text: string; halted?: boolean; icon?: string };
  ts: number;
  ephemeral?: boolean;
}

/** sidecar 已落盘的近期会话消息。仅用于恢复壳层时间线；不会触发任何动作重放。 */
export interface ConversationHistoryMessage {
  role: "user" | "assistant" | "tool";
  content: string;
  surface?: string;
  tool_call_id?: string;
  tool_calls?: Array<{
    id?: string;
    function?: { name?: string; arguments?: string };
  }>;
}

/** 面板焦点（v2 §5 focus）：面板内容/选中条目变化时上报，null = 面板关闭。
 *  大脑把它注入 LLM 上下文，「这个/它」等指代有解。 */
export interface PanelFocus {
  plugin: string;
  panel: string;
  item?: { id?: unknown; title?: unknown; status?: unknown } | null;
}

// ---- 守护状态 + 权限引导 ----

export type BrainStatus = "up" | "down" | "restarting";

export interface BrainStatusMsg {
  status: BrainStatus;
  attempt?: number;
  detail?: string;
}

export interface BrainPermissions {
  ax: boolean;
  screen: boolean;
  input: boolean;
}

// ---- 设置页 ----

export interface SetupConfig {
  has_key: boolean;
  model: string;
  base_url: string;
  voice: string;
  /** 语音总开关（YIBAO_VOICE："0"=关，缺省=开） */
  voice_enabled: boolean;
}

export type ClearKind = "memory" | "history" | "all";

// ---- 主屏 Feed（OS 感 §4.2：「它在我不看的时候干了什么」）----

export interface FeedItem {
  id: number;
  ts: number;
  kind: "task" | "reminder" | "event";
  text: string;
  meta?: Record<string, unknown>;
  read: number; // 0=未读 1=已读（sidecar Task 1/6 落库 + Rust 透传）
  status: "none" | "follow" | "ignore"; // C 子项目：处置态（与 read 正交）
}

export interface RunningTask {
  id: string;
  kind: "agent" | "script";
  label: string;
  prompt: string;
  status: "running";
  created_at: number;
}

export interface FeedStats {
  pending_reminders: number;
  running_tasks: number;
  done_24h: number;
  unread: number; // 未读动态数（sidecar stats.unread，Rust 透传）
  ignored: number; // 已忽略数（C 子项目，折叠提示用）
}

export interface FeedResponse {
  items: FeedItem[];
  stats: FeedStats;
  running_tasks: RunningTask[];
}

export const EMPTY_FEED: FeedResponse = {
  items: [],
  stats: { pending_reminders: 0, running_tasks: 0, done_24h: 0, unread: 0, ignored: 0 },
  running_tasks: [],
};

/** 手动提炼回包（distill_now 的响应，经 brain-distill-now 事件回来）。 */
export interface DistillNowResponse {
  ok: boolean;
  reason?: string;
  result?: {
    status: string;
    day?: string;
    patterns?: number;
    insights?: number;
    events?: number;
    projected?: number;
    error?: string;
  };
}

export interface DistillDay {
  day: string;
  status: string;        // ok | failed | no_data | pending
  stats: { app_seconds?: Record<string, number>; active_ranges?: number[][]; [k: string]: unknown };
  items: { id: number; kind: string; text: string; confidence?: number }[];
}

/** 信任统计（v1.1 收口）：近 N 天主动行为聚合，对应 sidecar FeedStore.stats()。 */
export interface TrustStats {
  days: number;
  since: number;
  total: number;
  by_kind: Record<string, number>;
  by_day: Array<{ day: string; kind: string; count: number }>;
  read_rate: number;
  ignored_rate: number;
}

export const EMPTY_STATS: TrustStats = {
  days: 7, since: 0, total: 0, by_kind: {}, by_day: [], read_rate: 0, ignored_rate: 0,
};

/** tier 三分级（按 kind 自动推导）：task→Review、reminder/event→Notify。 */
export type FeedTier = "Notify" | "Review";
export function feedTierOf(kind: FeedItem["kind"]): FeedTier {
  return kind === "task" ? "Review" : "Notify";
}

// ---- 主屏 widget（OS 感 §4.2：插件一瞥卡，schema 协议的 widget 类型）----

export interface WidgetPayload {
  panel: string;        // ref（pid:name）
  title: string;        // 插件名 · 卡片名
  schema: unknown;      // schema 面板 JSON（SchemaPanel 直接渲染）
  data: unknown;        // method 取回的数据
  open?: string | null; // 点击跳转的 api.toml 方法（null = 不可点击）
  reason?: string;      // 与当前会话的相关性说明（sidecar 推断；无则前端弱化呈现）
}

export interface WidgetsResponse {
  widgets: WidgetPayload[];
}

export const EMPTY_WIDGETS: WidgetsResponse = { widgets: [] };

// ---- 主屏 Dock（OS 感 §4.3：常驻插件快捷条，pinned 优先 + 频率补齐）----

export interface DockItem {
  id: string; // 插件 pid
  name: string; // 显示名
  pinned: boolean;
}

/** brain-dock-list / brain-dock-pin-set 载荷里的 dock 数组包装（匹配 sidecar {dock:[...]}）。 */
export interface DockListResponse {
  dock: DockItem[];
}

/** brain-dock-pin-set 载荷：操作回执 + 最新 dock 数组（前端原子刷新）。 */
export interface DockPinSet {
  pid: string;
  ok: boolean;
  dock: DockItem[];
}

export const EMPTY_DOCK: DockListResponse = { dock: [] };

// ---- 待批准队列（OS 感 §4.5 收件箱 Question 面：连环弹窗的替代）----
// 状态机实现见 state/pending.ts。

export interface PendingConfirm {
  id: string; // confirmation_id（= action.id）
  tool_id: string;
  label: string; // 技能短标签（回退 tool_id）
  desc: string;
  /** 确认卡只读取与决策有关的公开参数（如 command/cwd），不在 UI 展示未知字段。 */
  params?: Record<string, unknown>;
  risk?: number;
  /** 产生确认的会话面：小窗/面板只消费自己的确认，大窗收件箱展示全部。 */
  surface?: string;
  /** 产生确认的会话 id（并发对话 spec §B）：同 surface 的多会话（大窗/小窗都是 pet）
   *  靠它区分归属；A 会话被打断时只出队该会话的待批卡，不误清 B 会话的。 */
  conversationId?: string;
  /** 收件箱分层（C 子项目预留）：通知 / 问答 / 复核。本 task 不实装 tier 分流逻辑。 */
  tier?: "Notify" | "Question" | "Review";
}

// ---- 记忆管理（OS 感 §4.4：「它记得我什么」必须可见、可删）----

export interface MemItem {
  id: string;
  text: string;
  ns: string;    // 命名空间（"" = 底座译宝）
  label: string; // 显示名（译宝 / 插件名）
  created_at?: string; // ISO 时间；list_all 已按其倒序
}

export interface MemListResponse {
  items: MemItem[];
  ready: boolean;  // 记忆后端就绪（false = 懒加载中/已降级）
  failed: boolean; // 降级（本次运行记不住事）
}

export const EMPTY_MEM: MemListResponse = { items: [], ready: true, failed: false };

export interface MemDeleted {
  id: string;
  ok: boolean;
  error?: string;
}

export interface MemEdited {
  id: string;
  ok: boolean;
  error?: string;
}

// ---- 用户设置（自主权旋钮等；数据目录 settings.json，即时生效免重启）----

export interface SettingsValues {
  proactive_voice: boolean; // 主动开口：提醒触发时语音播报
  "proactive.level": "quiet" | "bubble" | "full"; // 自主权旋钮：触达强度三档
  "tts.provider": "edge" | "cosyvoice" | "cosyvoice_cloud"; // TTS 引擎（重启生效）
  "watch.enabled": boolean; // 健康节律，即时生效
  "watch.screen_enabled": boolean; // 屏幕建议，即时生效
  "watch.cadence": number;
  "watch.idle_warn_minutes": number;
  "watch.quiet_hours": string;
  "watch.observe_apps": string[];
  "watch.look_min_gap": number;
  "watch.look_max_per_hour": number;
  "watch.look_max_per_day": number;
  "watch.status": {
    running: boolean;
    health_enabled: boolean;
    health_available: boolean;
    screen_enabled: boolean;
    screen_available: boolean;
    last_error: string;
  };
  "perception.master": boolean;
  "perception.app": boolean;
  "perception.activity": boolean;
  "perception.model_access": boolean;
  "perception.screen"?: boolean;
  "perception.distill"?: boolean;
  "http.token": string; // 浏览器扩展桥共享 token（设置页「浏览器扩展」展示供复制）
  "http.mobile_token": string; // 手机伴生端访问 token（设置页可热重置）
  "http.bind": string; // HTTP 面绑定地址："0.0.0.0" 局域网 / "127.0.0.1" 仅本机（重启大脑生效）
  "search.provider": "browser" | "ddg" | "searxng" | "brave" | "tavily" | "serper"; // 联网搜索通道（即时生效）
  "search.searxng_url": string; // 自建 SearXNG 实例地址
  "search.keys"?: { brave?: string; tavily?: string; serper?: string }; // 商用搜索 API key（覆盖 .env）
  [k: string]: unknown;
}

/** 手机伴生端配对信息（sidecar 回 {"type":"http_pair_info",...} 整体转发，字段在顶层）。 */
export interface HttpPairInfo {
  lan_ip: string;
  port: number;
  bind: string;
}

// ---- 感知（默认关闭、payload 加密落盘；这里只接收 sidecar 解密后的短暂 UI 数据）----

export interface PerceptionItem {
  id: number;
  ts: number;
  source: "app" | "activity" | "screen" | "clipboard" | "environment";
  kind: string;
  payload: Record<string, unknown>;
  sensitivity: "S0" | "S1" | "S2" | "S3";
}

export interface PerceptionResponse {
  items: PerceptionItem[];
  sources: string[];
  available: boolean;
  error?: string;
}

export const EMPTY_PERCEPTION: PerceptionResponse = { items: [], sources: [], available: false };

export interface PerceptionDeleted {
  id: number;
  ok: boolean;
  error?: string;
}

export interface PerceptionCleared {
  count: number;
  error?: string;
}

/** 前端 Avatar 状态机共享集（brain 事件流的 UI 投影）：各窗口/组件的会话状态单一事实源。
 *  渲染全集（宠物扩展态 notify/drowsy/stretch）见 composables/usePetState 的 PetAvatarState。 */
export type AvatarState = "idle" | "listen" | "think" | "work" | "say" | "success" | "error";
