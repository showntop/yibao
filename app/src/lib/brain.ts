// 封装与大脑 sidecar 的通信（经 Tauri Rust 桥）。
import { invoke } from "@tauri-apps/api/core";
import { emit, listen, once, type UnlistenFn } from "@tauri-apps/api/event";
import type { WebviewPayload } from "./webview-source";

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
  skill_id?: string;
  description?: string;
  params?: Record<string, unknown>;
  risk?: number;
  /** 过程展示短标签（sidecar 从技能 label 填，回退 skill_id） */
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
  /** 反应式渲染原料：agents 任务完成事件携带 */
  task?: { id?: string; status?: string; label?: string; prompt?: string };
  /** watch_command 完成事件携带（completed/failed/timed_out/cancelled） */
  status?: string;
  exit_code?: number;
  task_id?: string;
  name?: string;
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

// ---- 会话分流（v2 §5）：run/语音/面板调用带 surface 标签，大脑透传回事件流与历史 ----
// 模块级当前 surface：宠物窗恒 pet；面板窗随焦点插件变化（PanelApp setSurface）。
let _surface = "pet";

/** 设置本窗口后续请求的 surface（面板窗焦点变化时调用）。 */
export function setSurface(s: string): void {
  _surface = s;
}

/**
 * 发送用户输入，触发大脑一次 run。surface 显式传参优先（大窗多页共享 JS 上下文，模块级 _surface 不够分）。
 * conversationId（M3 会话归属）：大窗/小窗各自传自己的会话 id，随 run 贯穿事件流，AI 回复按它落库；
 * 面板工作台（surface=panel:xxx 瞬时输入）不传——不持久化。
 */
export function runInput(text: string, surface?: string, conversationId?: string): Promise<void> {
  return invoke("run_input", { text, surface: surface ?? _surface, conversationId: conversationId ?? null });
}

/** 截图唤起（v1.1）：⌘⇧Y 唤起主窗时通知大脑抓屏描述，下次 run 注入屏幕上下文（静默失败）。 */
export function invokeContext(): Promise<void> {
  return invoke("invoke_context");
}

/** 批量回复确认：一次回 N 条裁决（Task 4 Rust `confirm_batch` 命令）。
 *  items 里 id = confirmation_needed 事件中每条 action.id（攒批场景）。
 *  本窗口作答的卡立即从共享队列乐观出队；IPC 失败时恢复，允许用户重试。 */
export function sendConfirmBatch(
  items: { id: string; approved: boolean; remember?: boolean }[],
): Promise<void> {
  const ids = new Set(items.map((item) => item.id));
  const removed = _pc.filter((item) => ids.has(item.id));
  for (const it of items) _pcRemove(it.id);
  return invoke<void>("confirm_batch", { items }).catch((error) => {
    _pcRestore(removed);
    throw error;
  });
}

/** 触发语音输入：sidecar 录音→STT→run→TTS 播报（Plan 4a 最小语音）。
 *  continuous=true 进连续会话：答完自动再听，退出语/打断收尾（二期）。 */
export function voiceStart(surface?: string, continuous?: boolean, conversationId?: string): Promise<void> {
  return invoke("voice_start", {
    surface: surface ?? _surface,
    continuous: continuous ?? false,
    conversationId: conversationId ?? "",
  });
}

/** 打断进行中的生成/播报（Plan 4b：停 TTS + 终止 LLM + 清队列）。
 *  并发对话（spec §E）：带 conversationId → 只打断该会话槽（别窗的 run 不受波及）；
 *  不带 → 全停（旧行为）。 */
export function interrupt(conversationId?: string): Promise<void> {
  return invoke("interrupt", { conversationId: conversationId ?? null });
}

/** 面板动作：调 api.toml 白名单内的方法（id 毫秒取模，一次请求一个够唯一；webview 桥传自有 id 做回包关联）。 */
export function panelAction(
  method: string,
  params: Record<string, unknown>,
  id?: number,
  surface?: string,
): Promise<void> {
  return invoke("panel_action", { id: id ?? Date.now() % 2 ** 31, method, params, surface: surface ?? _surface });
}

/** 唤起条动作广播（invoke-bar 窗 emit）：解释/翻译/存素材。 */
export function onInvokeAction(cb: (action: string) => void): Promise<UnlistenFn> {
  return listen<{ action: string }>("invoke-action", (e) => cb(e.payload.action));
}

/** 框选完成广播（finish_snip 后 Rust emit）：主窗展开 + chip 提示提问。 */
export function onSnipCaptured(cb: (r: { width: number; height: number }) => void): Promise<UnlistenFn> {
  return listen<{ width: number; height: number }>("snip-captured", (e) => cb(e.payload));
}

/** 截图即问：问题 → 大脑（暂存的区域截图 + vision 直答，不走 run）。 */
export function visionQuery(question: string): Promise<void> {
  return invoke("vision_query", { question });
}

/** 面板焦点（v2 §5 focus）：面板内容/选中条目变化时上报，null = 面板关闭。
 *  大脑把它注入 LLM 上下文，「这个/它」等指代有解。 */
export interface PanelFocus {
  plugin: string;
  panel: string;
  item?: { id?: unknown; title?: unknown; status?: unknown } | null;
}
export function reportPanelContext(focus: PanelFocus | null): Promise<void> {
  return invoke("report_panel_context", { focus });
}

/** 读取近期持久化会话，用于主屏/能力工作面恢复 UI；只读，不会重放工具。 */
export function getConversationHistory(limit = 80): Promise<ConversationHistoryMessage[]> {
  return invoke("get_conversation_history", { limit });
}

/** 订阅大脑事件流，返回取消监听函数。 */
export function onBrainEvent(cb: (e: BrainEvent) => void): Promise<UnlistenFn> {
  return listen<BrainEvent>("brain-event", (ev) => cb(ev.payload));
}

/** 订阅一次 run 完成信号。 */
export function onRunDone(cb: (v: unknown) => void): Promise<UnlistenFn> {
  return listen("brain-run-done", (ev) => cb(ev.payload));
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

/** 订阅大脑守护状态（up=在线 / down=掉线 / restarting=重启中）。 */
export function onBrainStatus(cb: (m: BrainStatusMsg) => void): Promise<UnlistenFn> {
  return listen<BrainStatusMsg>("brain-status", (ev) => cb(ev.payload));
}

/** 订阅面板窗关闭（隐藏）：宠物窗用它给「⇢ 协作中」关联气泡收尾。 */
export function onPanelClosed(cb: () => void): Promise<UnlistenFn> {
  return listen("panel-closed", () => cb());
}

/** 订阅 macOS 权限状态（hello / check_permissions / prompt_permission 都会触发）。 */
export function onBrainPermissions(cb: (p: BrainPermissions) => void): Promise<UnlistenFn> {
  return listen<BrainPermissions>("brain-permissions", (ev) => cb(ev.payload));
}

/** 请求 sidecar 重新检测权限（结果经 brain-permissions 事件回来）。 */
export function checkPermissions(): Promise<void> {
  return invoke("check_permissions");
}

/** 触发系统授权引导弹窗。 */
export function promptPermission(which: "ax" | "screen" | "input"): Promise<void> {
  return invoke("prompt_permission", { which });
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

/** 读取设置（合并 数据目录.env / sidecar.env / 真环境变量）。 */
export function getSetupConfig(): Promise<SetupConfig> {
  return invoke("get_setup_config");
}

/** 保存设置：upsert 数据目录 .env；key 留空 = 不改动。保存后需 restartBrain 才生效（大脑只在启动时读 .env）。 */
export function saveSetupConfig(cfg: {
  key: string;
  model: string;
  baseUrl: string;
  voice: string;
  voiceEnabled: boolean;
}): Promise<void> {
  return invoke("save_setup_config", cfg);
}

/** 手动重启大脑（计划内：不退避计数升级，1s 即回；大脑掉线/上线事件照常广播）。 */
export function restartBrain(): Promise<void> {
  return invoke("restart_brain");
}

export type ClearKind = "memory" | "history" | "all";

/** 清空大脑数据：memory=长期记忆 / history=对话历史 / all=两者。先停大脑→删→拉起，约几秒。 */
export function clearBrainData(kind: ClearKind): Promise<void> {
  return invoke("clear_brain_data", { kind });
}

/** 在 Finder 中打开数据目录。 */
export function openDataDir(): Promise<void> {
  return invoke("open_data_dir");
}

/** 打开/聚焦设置大窗（home；宠物窗 header「扩充」钮与托盘「设置…」共用）。 */
export function openHomeWindow(): Promise<void> {
  return invoke("open_home_window");
}



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

const EMPTY_FEED: FeedResponse = {
  items: [],
  stats: { pending_reminders: 0, running_tasks: 0, done_24h: 0, unread: 0, ignored: 0 },
  running_tasks: [],
};

/** 订阅主屏 Feed 响应（get_feed 查询的回包，经 Rust 转发）。 */
export function onFeed(cb: (r: FeedResponse) => void): Promise<UnlistenFn> {
  return listen<FeedResponse>("brain-feed", (ev) => cb(ev.payload));
}

/** 查询主屏 Feed（响应经 brain-feed 事件回来）。 */
export function fetchFeed(limit = 60): Promise<void> {
  return invoke("get_feed", { limit });
}

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

/** 一次性手动提炼：发请求并等下一条 brain-distill-now；大脑不在线/超时返回 ok:false。 */
export async function distillNow(timeoutMs = 90000): Promise<DistillNowResponse> {
  const resp = new Promise<DistillNowResponse>((resolve) => {
    void once<DistillNowResponse>("brain-distill-now", (ev) => resolve(ev.payload));
  });
  const timeout = new Promise<DistillNowResponse>((resolve) =>
    setTimeout(() => resolve({ ok: false, reason: "timeout" }), timeoutMs),
  );
  try {
    await invoke("distill_now");
  } catch { /* 大脑不在线：走超时兜底 */ }
  return Promise.race([resp, timeout]);
}

// ---- 晨间反刍 + 每日回顾（Task 9：recap 触发 + distill timeline 查询 + deep-link）----

/** 反刍：开窗时 fire-and-forget 触发，大脑自行决定推不推。 */
export function recapCheck(): Promise<void> {
  return invoke("recap_check");
}

export interface DistillDay {
  day: string;
  status: string;        // ok | failed | no_data | pending
  stats: { app_seconds?: Record<string, number>; active_ranges?: number[][]; [k: string]: unknown };
  items: { id: number; kind: string; text: string; confidence?: number }[];
}

/** 每日回顾：发查询并等 brain-distill-timeline；大脑不在线/超时返回空。 */
export async function getDistillTimelineOnce(days = 14, timeoutMs = 3000): Promise<DistillDay[]> {
  return new Promise((resolve) => {
    once<{ days: DistillDay[] }>("brain-distill-timeline", (ev) => resolve(ev.payload.days));
    void invoke("get_distill_timeline", { days });
    setTimeout(() => resolve([]), timeoutMs);
  });
}

export function fetchDistillTimeline(days = 14): Promise<void> {
  return invoke("get_distill_timeline", { days });
}

/** deep-link：pet 窗气泡点击 → 通知 home 窗切回顾 mode + 跳当天。 */
export function emitRecapOpen(day: string): Promise<void> {
  return emit("recap-open", { day });
}
export function onRecapOpen(cb: (day: string) => void): Promise<UnlistenFn> {
  return listen<{ day: string }>("recap-open", (e) => cb(e.payload.day));
}

/** 一次性取 Feed：发查询并等下一条 brain-feed；大脑不在线/超时返回空（主屏照常渲染空态）。 */
export async function getFeedOnce(limit = 60, timeoutMs = 3000): Promise<FeedResponse> {
  const resp = new Promise<FeedResponse>((resolve) => {
    void once<FeedResponse>("brain-feed", (ev) => resolve(ev.payload));
  });
  const timeout = new Promise<FeedResponse>((resolve) =>
    setTimeout(() => resolve(EMPTY_FEED), timeoutMs),
  );
  try {
    await fetchFeed(limit);
  } catch { /* 大脑不在线：走超时兜底 */ }
  return Promise.race([resp, timeout]);
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

const EMPTY_STATS: TrustStats = {
  days: 7, since: 0, total: 0, by_kind: {}, by_day: [], read_rate: 0, ignored_rate: 0,
};

/** 一次性取信任统计：发查询并等下一条 brain-feed-stats；大脑不在线/超时返回零值。 */
export async function getFeedStatsOnce(days = 7, timeoutMs = 3000): Promise<TrustStats> {
  const resp = new Promise<TrustStats>((resolve) => {
    void once<{ stats: TrustStats }>("brain-feed-stats", (ev) => resolve(ev.payload.stats));
  });
  const timeout = new Promise<TrustStats>((resolve) =>
    setTimeout(() => resolve(EMPTY_STATS), timeoutMs),
  );
  try {
    await invoke("get_feed_stats", { days });
  } catch { /* 大脑不在线：走超时兜底 */ }
  return Promise.race([resp, timeout]);
}

/** 点掉单条 Feed（sidecar 回执经 brain-feed-marked-read 事件来；UI 可乐观置 read=1）。 */
export function markFeedRead(id: number): Promise<void> {
  return invoke("feed_mark_read", { id });
}

/** 全部已读（sidecar 回执经 brain-feed-all-read 事件来，载荷含 n=归零条数）。 */
export function markAllFeedRead(): Promise<void> {
  return invoke("feed_mark_all_read");
}

/** 设置处置态：follow/ignore/none（C 子项目，与 read 正交）。前端走乐观更新。 */
export function markFeedStatus(id: number, status: "none" | "follow" | "ignore"): Promise<void> {
  return invoke("feed_mark_status", { id, status });
}

/** 误报反馈（信任仪表写侧）：👍/👎/none 落 meta.feedback；同类 24h≥2👎 的主动事件会被大脑降 quiet。 */
export function sendFeedFeedback(id: number, feedback: "up" | "down" | "none"): Promise<void> {
  return invoke("feed_feedback", { id, feedback });
}

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

const EMPTY_WIDGETS: WidgetsResponse = { widgets: [] };

/** 订阅主屏 widget 响应（get_widgets 查询的回包，经 Rust 转发）。 */
export function onWidgets(cb: (r: WidgetsResponse) => void): Promise<UnlistenFn> {
  return listen<WidgetsResponse>("brain-widgets", (ev) => cb(ev.payload));
}

/** 查询主屏 widget（响应经 brain-widgets 事件回来）。 */
export function fetchWidgets(): Promise<void> {
  return invoke("get_widgets");
}

/** 一次性取 widget：发查询并等下一条 brain-widgets；大脑不在线/超时返回空（主屏不显示卡片区）。 */
export async function getWidgetsOnce(timeoutMs = 3000): Promise<WidgetsResponse> {
  const resp = new Promise<WidgetsResponse>((resolve) => {
    void once<WidgetsResponse>("brain-widgets", (ev) => resolve(ev.payload));
  });
  const timeout = new Promise<WidgetsResponse>((resolve) =>
    setTimeout(() => resolve(EMPTY_WIDGETS), timeoutMs),
  );
  try {
    await fetchWidgets();
  } catch { /* 大脑不在线：走超时兜底 */ }
  return Promise.race([resp, timeout]);
}

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

const EMPTY_DOCK: DockListResponse = { dock: [] };

/** 订阅 Dock 列表响应（dock_list 查询的回包，经 Rust 转发）。 */
export function onDockList(cb: (r: DockListResponse) => void): Promise<UnlistenFn> {
  return listen<DockListResponse>("brain-dock-list", (ev) => cb(ev.payload));
}

/** 订阅 Dock 固定/取消回执（含最新 dock 数组，供主屏整体刷新）。 */
export function onDockPinSet(cb: (p: DockPinSet) => void): Promise<UnlistenFn> {
  return listen<DockPinSet>("brain-dock-pin-set", (ev) => cb(ev.payload));
}

/** 查询主屏 Dock（响应经 brain-dock-list 事件回来）。 */
export function fetchDockList(): Promise<void> {
  return invoke("dock_list");
}

/** 固定/取消固定一个插件（响应经 brain-dock-pin-set 事件来）。 */
export function setDockPin(pid: string, on: boolean): Promise<void> {
  return invoke("set_dock_pin", { pid, on });
}

/** 一次性取 Dock：发查询并等下一条 brain-dock-list；大脑不在线/超时返回空（主屏渲染空 Dock）。 */
export async function getDockListOnce(timeoutMs = 3000): Promise<DockListResponse> {
  const resp = new Promise<DockListResponse>((resolve) => {
    void once<DockListResponse>("brain-dock-list", (ev) => resolve(ev.payload));
  });
  const timeout = new Promise<DockListResponse>((resolve) =>
    setTimeout(() => resolve(EMPTY_DOCK), timeoutMs),
  );
  try {
    await fetchDockList();
  } catch { /* 大脑不在线：走超时兜底 */ }
  return Promise.race([resp, timeout]);
}

// ---- 待批准队列（OS 感 §4.5 收件箱 Question 面：连环弹窗的替代）----
// confirmation_needed 进队；任一窗口 sendConfirmBatch / action_result / error 出队；
// 大脑掉线清空（未答确认随进程死）。大窗由会话检查器呈现，小窗/面板按 surface 快批。

export interface PendingConfirm {
  id: string; // confirmation_id（= action.id）
  skill: string;
  label: string; // 技能短标签（回退 skill_id）
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

/** 当前确认链支持：普通技能按 skill；后台命令按 command + cwd 精确记忆。 */
export function canRememberSkill(skill: string): boolean {
  // coding 审批不经 invoker.apply_verdict（裁决走 confirmation_needed 直兑 future），
  // remember 勾了也不生效——复选框对 coding 隐藏，防误导（P2 审批统一 L2）
  if (skill === "coding") return false;
  return Boolean(skill);
}

export function rememberLabelForSkill(skill: string): string {
  return skill === "watch_command" ? "本会话允许相同命令" : "本会话不再询问";
}

let _pc: PendingConfirm[] = [];
const _pcSubs = new Set<(l: PendingConfirm[]) => void>();

function _pcEmit(): void {
  const l = [..._pc];
  _pcSubs.forEach((cb) => cb(l));
}

function _pcRemove(id: string): void {
  const n = _pc.filter((p) => p.id !== id);
  if (n.length !== _pc.length) {
    _pc = n;
    _pcEmit();
  }
}

/** IPC 失败时恢复刚才乐观移除的卡；去重避免另一窗口的队列更新造成重复。 */
function _pcRestore(items: PendingConfirm[]): void {
  const existing = new Set(_pc.map((item) => item.id));
  const restore = items.filter((item) => !existing.has(item.id));
  if (restore.length) {
    _pc = [...restore, ..._pc];
    _pcEmit();
  }
}

/** 订阅待批准队列（立即回当前值；返回取消订阅函数）。 */
export function onPendingConfirms(cb: (l: PendingConfirm[]) => void): () => void {
  _pcSubs.add(cb);
  cb([..._pc]);
  return () => {
    _pcSubs.delete(cb);
  };
}

// 普通浏览器只用于本地 UI QA，没有 Tauri event bridge；避免模块加载时产生无意义的未处理错误。
if ("__TAURI_INTERNALS__" in window) void listen<BrainEvent>("brain-event", (ev) => {
  const e = ev.payload;
  if (e.kind === "confirmation_needed") {
    // Task 2 攒批：一轮可能多 CONFIRM，actions 带全部待批 action；
    // 兼容旧单条载荷——无 actions 时退化为 [action]（旧 sidecar 里 confirmation_id = action.id）。
    const actions = e.actions?.length ? e.actions : e.action ? [e.action] : [];
    const fresh = actions
      .filter((a) => a?.id && !_pc.some((p) => p.id === a.id))
      .map((a) => ({
        id: a.id as string,
        skill: a.skill_id ?? "",
        label: a.label ?? a.skill_id ?? "",
        desc: a.description ?? "",
        params: a.params,
        risk: a.risk,
        // coding 审批经 ProactiveDispatcher 广播时顶层 surface 为 null，action 自带 surface 优先
        surface: a.surface ?? e.surface,
        // 会话归属随信封（并发对话 spec §B）：确认卡按它过滤/定向出队
        conversationId: e.conversationId,
      }));
    if (fresh.length) {
      _pc = [..._pc, ...fresh];
      _pcEmit();
    }
  } else if ((e.kind === "action_result" || e.kind === "error") && e.action?.id) {
    _pcRemove(e.action.id);
  } else if (e.kind === "interrupted") {
    // F1（Task 2 review Important）：cancel-during-CONFIRM 从 error 改为 interrupted 后，
    // 旧逻辑只认 action_result/error 出队 → 待批卡会滞留。打断即出队。
    // 并发对话（spec §C）：只出队该会话的卡——A 会话被打断不清 B 会话的待批确认；
    // 双方都无归属（旧 sidecar/无会话路径）时退化为整批清空（旧语义）。
    const n = _pc.filter((p) => p.conversationId !== e.conversationId);
    if (n.length !== _pc.length) {
      _pc = n;
      _pcEmit();
    }
  }
});

if ("__TAURI_INTERNALS__" in window) void listen<BrainStatusMsg>("brain-status", (ev) => {
  if (ev.payload.status === "up") return;
  if (_pc.length) {
    _pc = [];
    _pcEmit();
  }
});

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

const EMPTY_MEM: MemListResponse = { items: [], ready: true, failed: false };

/** 一次性取记忆列表：发查询等 brain-mem-list；大脑不在线/超时返回空。 */
export async function getMemListOnce(timeoutMs = 4000): Promise<MemListResponse> {
  const resp = new Promise<MemListResponse>((resolve) => {
    void once<MemListResponse>("brain-mem-list", (ev) => resolve(ev.payload));
  });
  const timeout = new Promise<MemListResponse>((resolve) =>
    setTimeout(() => resolve(EMPTY_MEM), timeoutMs),
  );
  try {
    await invoke("get_mem_list");
  } catch { /* 大脑不在线：走超时兜底 */ }
  return Promise.race([resp, timeout]);
}

export interface MemDeleted {
  id: string;
  ok: boolean;
  error?: string;
}

/** 删除一条记忆，等该 id 的 brain-mem-deleted 回执；超时按失败处理。 */
export async function memDelete(id: string, timeoutMs = 5000): Promise<MemDeleted> {
  const holder: { un: (() => void) | null } = { un: null }; // 对象持有：绕过 TS 对闭包赋值的窄化
  const resp = new Promise<MemDeleted>((resolve) => {
    void listen<MemDeleted>("brain-mem-deleted", (ev) => {
      if (ev.payload.id === id) resolve(ev.payload);
    }).then((u) => (holder.un = u));
  });
  const timeout = new Promise<MemDeleted>((resolve) =>
    setTimeout(() => resolve({ id, ok: false, error: "删除超时（大脑不在线？）" }), timeoutMs),
  );
  try {
    await invoke("mem_delete", { id });
  } catch (e) {
    holder.un?.();
    return { id, ok: false, error: String(e) };
  }
  const r = await Promise.race([resp, timeout]);
  holder.un?.();
  return r;
}

export interface MemEdited {
  id: string;
  ok: boolean;
  error?: string;
}

/** 编辑一条记忆的文本，等该 id 的 brain-mem-edited 回执；超时按失败处理。 */
export async function memEdit(id: string, text: string, timeoutMs = 5000): Promise<MemEdited> {
  const holder: { un: (() => void) | null } = { un: null };
  const resp = new Promise<MemEdited>((resolve) => {
    void listen<MemEdited>("brain-mem-edited", (ev) => {
      if (ev.payload.id === id) resolve(ev.payload);
    }).then((u) => (holder.un = u));
  });
  const timeout = new Promise<MemEdited>((resolve) =>
    setTimeout(() => resolve({ id, ok: false, error: "保存超时（大脑不在线？）" }), timeoutMs),
  );
  try {
    await invoke("mem_edit", { id, text });
  } catch (e) {
    holder.un?.();
    return { id, ok: false, error: String(e) };
  }
  const r = await Promise.race([resp, timeout]);
  holder.un?.();
  return r;
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

/** 一次性取设置：发查询等 brain-settings；超时返回 null（调用方用默认）。 */
export async function getSettingsOnce(timeoutMs = 3000): Promise<SettingsValues | null> {
  const resp = new Promise<{ values: SettingsValues }>((resolve) => {
    void once<{ values: SettingsValues }>("brain-settings", (ev) => resolve(ev.payload));
  });
  const timeout = new Promise<null>((resolve) => setTimeout(() => resolve(null), timeoutMs));
  try {
    await invoke("get_settings");
  } catch {
    return null;
  }
  const r = await Promise.race([resp, timeout]);
  return r ? r.values : null;
}

/** 写设置（已知键才生效），等 brain-settings 回执；超时/null 按未生效处理。 */
export async function setSettings(values: Partial<SettingsValues>, timeoutMs = 3000): Promise<SettingsValues | null> {
  const resp = new Promise<{ values: SettingsValues }>((resolve) => {
    void once<{ values: SettingsValues }>("brain-settings", (ev) => resolve(ev.payload));
  });
  const timeout = new Promise<null>((resolve) => setTimeout(() => resolve(null), timeoutMs));
  try {
    await invoke("set_settings", { values });
  } catch {
    return null;
  }
  const r = await Promise.race([resp, timeout]);
  return r ? r.values : null;
}

/** 订阅设置变更回推（settings_set 生效后大脑广播 brain-settings）。 */
export function onSettings(cb: (s: SettingsValues) => void): Promise<UnlistenFn> {
  return listen<{ values: SettingsValues }>("brain-settings", (ev) => cb(ev.payload.values));
}

/** 手机伴生端配对信息（sidecar 回 {"type":"http_pair_info",...} 整体转发，字段在顶层）。 */
export interface HttpPairInfo {
  lan_ip: string;
  port: number;
  bind: string;
}

/** 一次性取手机伴生端配对信息；超时返回 null。 */
export async function getHttpPairInfoOnce(timeoutMs = 3000): Promise<HttpPairInfo | null> {
  const resp = new Promise<HttpPairInfo | null>((resolve) => {
    void once<HttpPairInfo>("brain-http-pair-info", (ev) => resolve(ev.payload));
    setTimeout(() => resolve(null), timeoutMs);
  });
  try {
    await invoke("get_http_pair_info");
  } catch {
    return null;
  }
  return resp;
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

const EMPTY_PERCEPTION: PerceptionResponse = { items: [], sources: [], available: false };

/** 分页取感知日志；超时释放 listener 并返回不可用空态。 */
export async function getPerceptionOnce(
  limit = 60,
  beforeId?: number,
  timeoutMs = 3000,
): Promise<PerceptionResponse> {
  const holder: { un: (() => void) | null } = { un: null };
  let settled = false;
  const resp = new Promise<PerceptionResponse>((resolve) => {
    void listen<PerceptionResponse>("brain-perception", (ev) => resolve(ev.payload))
      .then((u) => {
        holder.un = u;
        if (settled) u();
      });
  });
  const timeout = new Promise<PerceptionResponse>((resolve) =>
    setTimeout(() => resolve(EMPTY_PERCEPTION), timeoutMs),
  );
  try {
    await invoke("get_perception", { limit, beforeId });
  } catch {
    settled = true;
    holder.un?.();
    return EMPTY_PERCEPTION;
  }
  const result = await Promise.race([resp, timeout]);
  settled = true;
  holder.un?.();
  return result;
}

export interface PerceptionDeleted {
  id: number;
  ok: boolean;
  error?: string;
}

/** 删除单条观察；只接收匹配 id 的回执。 */
export async function deletePerception(id: number, timeoutMs = 5000): Promise<PerceptionDeleted> {
  const holder: { un: (() => void) | null } = { un: null };
  let settled = false;
  const resp = new Promise<PerceptionDeleted>((resolve) => {
    void listen<PerceptionDeleted>("brain-perception-deleted", (ev) => {
      if (ev.payload.id === id) resolve(ev.payload);
    }).then((u) => {
      holder.un = u;
      if (settled) u();
    });
  });
  const timeout = new Promise<PerceptionDeleted>((resolve) =>
    setTimeout(() => resolve({ id, ok: false, error: "删除超时（大脑不在线？）" }), timeoutMs),
  );
  try {
    await invoke("perception_delete", { id });
  } catch (e) {
    settled = true;
    holder.un?.();
    return { id, ok: false, error: String(e) };
  }
  const result = await Promise.race([resp, timeout]);
  settled = true;
  holder.un?.();
  return result;
}

export interface PerceptionCleared {
  count: number;
  error?: string;
}

/** 清空观察日志并等待 sidecar 回执。 */
export async function clearPerception(timeoutMs = 5000): Promise<PerceptionCleared> {
  const holder: { un: (() => void) | null } = { un: null };
  let settled = false;
  const resp = new Promise<PerceptionCleared>((resolve) => {
    void listen<PerceptionCleared>("brain-perception-cleared", (ev) => resolve(ev.payload))
      .then((u) => {
        holder.un = u;
        if (settled) u();
      });
  });
  const timeout = new Promise<PerceptionCleared>((resolve) =>
    setTimeout(() => resolve({ count: 0, error: "清空超时（大脑不在线？）" }), timeoutMs),
  );
  try {
    await invoke("perception_clear");
  } catch (e) {
    settled = true;
    holder.un?.();
    return { count: 0, error: String(e) };
  }
  const result = await Promise.race([resp, timeout]);
  settled = true;
  holder.un?.();
  return result;
}
