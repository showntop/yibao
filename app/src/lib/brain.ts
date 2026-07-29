// 封装与大脑 sidecar 的通信（经 Tauri Rust 桥）。
import { invoke } from "@tauri-apps/api/core";
import { listen, once, type UnlistenFn } from "@tauri-apps/api/event";

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
  | "panel";

export interface BrainAction {
  id?: string;
  skill_id?: string;
  description?: string;
  params?: Record<string, unknown>;
  risk?: number;
  /** 过程展示短标签（sidecar 从技能 label 填，回退 skill_id） */
  label?: string;
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
  /** webview 面板：插件 HTML 文本（父侧注入桥 JS 后以 iframe srcdoc 渲染）；schema 面板无此字段 */
  webview?: { html?: string } | null;
  data?: Record<string, unknown>;
}

export interface BrainEvent {
  kind: BrainEventKind;
  text?: string;
  action?: BrainAction;
  result?: BrainResult;
  confirmation_id?: string;
  payload?: PanelPayload;
  /** 会话分流：本次 run 的发起场景（pet / panel:<plugin>）；空 = 全局事件（两窗都处理） */
  surface?: string;
  /** 自主权档位（reminder 类主动事件；缺省按 full 处理，兼容旧 sidecar） */
  level?: "quiet" | "bubble" | "full";
}

// ---- 会话分流（v2 §5）：run/语音/面板调用带 surface 标签，大脑透传回事件流与历史 ----
// 模块级当前 surface：宠物窗恒 pet；面板窗随焦点插件变化（PanelApp setSurface）。
let _surface = "pet";

/** 设置本窗口后续请求的 surface（面板窗焦点变化时调用）。 */
export function setSurface(s: string): void {
  _surface = s;
}

/** 发送用户输入，触发大脑一次 run。surface 显式传参优先（大窗多页共享 JS 上下文，模块级 _surface 不够分）。 */
export function runInput(text: string, surface?: string): Promise<void> {
  return invoke("run_input", { text, surface: surface ?? _surface });
}

/** 回复高风险确认（Rust 命令参数 confirmation_id 在 JS 侧为 camelCase）。
 *  remember=true：本会话不再询问这个技能（大脑侧会话级记忆，重启失效）。 */
export function sendConfirm(confirmationId: string, approved: boolean, remember = false): Promise<void> {
  _pcRemove(confirmationId); // 待批准队列出队（本窗口作答；其他窗口靠 action_result/error 事件出队）
  return invoke("confirm", { confirmationId, approved, remember });
}

/** 触发语音输入：sidecar 录音→STT→run→TTS 播报（Plan 4a 最小语音）。
 *  continuous=true 进连续会话：答完自动再听，退出语/打断收尾（二期）。 */
export function voiceStart(surface?: string, continuous?: boolean): Promise<void> {
  return invoke("voice_start", { surface: surface ?? _surface, continuous: continuous ?? false });
}

/** 打断进行中的生成/播报（Plan 4b：停 TTS + 终止 LLM + 清队列）。 */
export function interrupt(): Promise<void> {
  return invoke("interrupt");
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

/** 触发系统授权引导弹窗（which = "ax" | "screen"）。 */
export function promptPermission(which: "ax" | "screen"): Promise<void> {
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
}

export interface FeedStats {
  pending_reminders: number;
  running_tasks: number;
  done_24h: number;
}

export interface FeedResponse {
  items: FeedItem[];
  stats: FeedStats;
}

const EMPTY_FEED: FeedResponse = {
  items: [],
  stats: { pending_reminders: 0, running_tasks: 0, done_24h: 0 },
};

/** 订阅主屏 Feed 响应（get_feed 查询的回包，经 Rust 转发）。 */
export function onFeed(cb: (r: FeedResponse) => void): Promise<UnlistenFn> {
  return listen<FeedResponse>("brain-feed", (ev) => cb(ev.payload));
}

/** 查询主屏 Feed（响应经 brain-feed 事件回来）。 */
export function fetchFeed(limit = 60): Promise<void> {
  return invoke("get_feed", { limit });
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

// ---- 主屏 widget（OS 感 §4.2：插件一瞥卡，schema 协议的 widget 类型）----

export interface WidgetPayload {
  panel: string;        // ref（pid:name）
  title: string;        // 插件名 · 卡片名
  schema: unknown;      // schema 面板 JSON（SchemaPanel 直接渲染）
  data: unknown;        // method 取回的数据
  open?: string | null; // 点击跳转的 api.toml 方法（null = 不可点击）
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

// ---- 待批准队列（OS 感 §4.5 收件箱 Question 面：连环弹窗的替代）----
// confirmation_needed 进队；任一窗口 sendConfirm（见上）/ action_result / error 出队；
// 大脑掉线清空（未答确认随进程死）。HomeChat 的 ConfirmDialog 本来就靠事件自清，两边天然一致。

export interface PendingConfirm {
  id: string; // confirmation_id（= action.id）
  skill: string;
  label: string; // 技能短标签（回退 skill_id）
  desc: string;
  risk?: number;
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

/** 订阅待批准队列（立即回当前值；返回取消订阅函数）。 */
export function onPendingConfirms(cb: (l: PendingConfirm[]) => void): () => void {
  _pcSubs.add(cb);
  cb([..._pc]);
  return () => {
    _pcSubs.delete(cb);
  };
}

void listen<BrainEvent>("brain-event", (ev) => {
  const e = ev.payload;
  if (e.kind === "confirmation_needed" && e.confirmation_id) {
    if (_pc.some((p) => p.id === e.confirmation_id)) return;
    _pc = [
      ..._pc,
      {
        id: e.confirmation_id,
        skill: e.action?.skill_id ?? "",
        label: e.action?.label ?? e.action?.skill_id ?? "",
        desc: e.action?.description ?? "",
        risk: e.action?.risk,
      },
    ];
    _pcEmit();
  } else if ((e.kind === "action_result" || e.kind === "error") && e.action?.id) {
    _pcRemove(e.action.id);
  }
});

void listen<BrainStatusMsg>("brain-status", (ev) => {
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
  "perception.master": boolean;
  "perception.app": boolean;
  "perception.activity": boolean;
  "perception.model_access": boolean;
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
