// 大脑 sidecar 通信客户端：Tauri IPC（invoke）封装 + 事件订阅 + 通用「请求→回包」工具。
// 组件不得直接 invoke，一律走这里的函数（错误处理在此收敛）。
import { invoke } from "@tauri-apps/api/core";
import { emit, listen, once, type UnlistenFn } from "@tauri-apps/api/event";
import {
  EMPTY_DOCK,
  EMPTY_FEED,
  EMPTY_MEM,
  EMPTY_PERCEPTION,
  EMPTY_STATS,
  EMPTY_WIDGETS,
  type BrainEvent,
  type BrainPermissions,
  type BrainStatusMsg,
  type ClearKind,
  type ConversationHistoryMessage,
  type DistillDay,
  type DistillNowResponse,
  type DockListResponse,
  type DockPinSet,
  type FeedResponse,
  type HttpPairInfo,
  type MemDeleted,
  type MemEdited,
  type MemListResponse,
  type PanelFocus,
  type PerceptionCleared,
  type PetMessage,
  type PerceptionDeleted,
  type PerceptionResponse,
  type SettingsValues,
  type SetupConfig,
  type TrustStats,
  type WidgetsResponse,
} from "../protocol/brain-types";
import { pcRemoveMany, pcRestore } from "../state/pending";

// ---- 会话分流（v2 §5）：run/语音/面板调用带 surface 标签，大脑透传回事件流与历史 ----
// 模块级当前 surface：宠物窗恒 pet；面板窗随焦点插件变化（PanelApp setSurface）。
let _surface = "pet";

/** 设置本窗口后续请求的 surface（面板窗焦点变化时调用）。 */
export function setSurface(s: string): void {
  _surface = s;
}

// ---- 通用「请求 → 等下一条回包事件」工具（收敛 XxxOnce 样板）----

/**
 * 发请求并等下一条匹配回包事件；大脑不在线/超时返回 fallback。
 * match 可选：回包含 id 等判别字段时用于过滤（如 mem_delete 只收该 id 的回执）。
 */
function onceWithTimeout<T>(
  event: string,
  send: () => Promise<void>,
  fallback: T,
  timeoutMs: number,
  match?: (payload: T) => boolean,
): Promise<T> {
  const holder: { un: (() => void) | null } = { un: null };
  let settled = false;
  const resp = new Promise<T>((resolve) => {
    void once<T>(event, (ev) => {
      if (!match || match(ev.payload)) resolve(ev.payload);
    }).then((u) => {
      holder.un = u;
      if (settled) u();
    });
  });
  const timeout = new Promise<T>((resolve) => setTimeout(() => resolve(fallback), timeoutMs));
  return (async () => {
    try {
      await send();
    } catch {
      /* 大脑不在线：走超时/回包兜底 */
    }
    const result = await Promise.race([resp, timeout]);
    settled = true;
    holder.un?.();
    return result;
  })();
}

// ---- 对话 run / 语音 / 打断 ----

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
  const removed = pcRemoveMany(items);
  return invoke<void>("confirm_batch", { items }).catch((error) => {
    pcRestore(removed);
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

/** 截图即问：问题 → 大脑（暂存的区域截图 + vision 直答，不走 run）。 */
export function visionQuery(question: string): Promise<void> {
  return invoke("vision_query", { question });
}

/** 面板焦点（v2 §5 focus）：面板内容/选中条目变化时上报，null = 面板关闭。 */
export function reportPanelContext(focus: PanelFocus | null): Promise<void> {
  return invoke("report_panel_context", { focus });
}

/** 读取近期持久化会话，用于主屏/能力工作面恢复 UI；只读，不会重放工具。 */
export function getConversationHistory(limit = 80): Promise<ConversationHistoryMessage[]> {
  return invoke("get_conversation_history", { limit });
}

// ---- 守护状态 + 权限引导 ----

/** 请求 sidecar 重新检测权限（结果经 brain-permissions 事件回来）。 */
export function checkPermissions(): Promise<void> {
  return invoke("check_permissions");
}

/** 触发系统授权引导弹窗。 */
export function promptPermission(which: "ax" | "screen" | "input"): Promise<void> {
  return invoke("prompt_permission", { which });
}

// ---- 设置页 ----

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

/** 关闭面板窗（PanelApp 右上角/快捷键）。 */
export function closePanelWindow(): Promise<void> {
  return invoke("close_panel_window");
}

/** 展开宠物聊天窗（定位 + clamp + 缩放一步完成，Rust 侧裁决）。 */
export function expandChat(): Promise<void> {
  return invoke("expand_chat");
}

/** 展开态同步给 Rust（全局热键在 Rust 侧决定 显示/展开/隐藏 的依据）。 */
export function setPetExpanded(expanded: boolean): Promise<void> {
  return invoke("set_pet_expanded", { expanded });
}

/** 收起大窗 = 隐藏 + 回小窗模式（Rust 侧还原宠物窗/面板浮窗）。 */
export function closeHomeWindow(): Promise<void> {
  return invoke("close_home_window");
}

/** 隐藏划词唤起条（动作受理后的兜底，条本身已自隐）。 */
export function hideInvokeBar(): Promise<void> {
  return invoke("hide_invoke_bar");
}

/** 确保存在活跃会话：无则新建并设为活跃（大窗首启直接输入时兜底；小窗走 ensurePetConversation）。 */
export function ensureActiveConversation(): Promise<{ id: string } | null> {
  return invoke("ensure_active_conversation");
}

/** 确保小窗固定会话存在（不镜像大窗活跃会话，两窗互不干扰）。 */
export function ensurePetConversation(): Promise<{ id: string } | null> {
  return invoke("ensure_pet_conversation");
}

/** 从 Rust 权威重拉指定会话消息（小窗恢复渲染；流式进行中调用方自行跳过）。 */
export function getConversationMessages(id: string, limit = 500): Promise<PetMessage[] | null> {
  return invoke("get_conversation_messages", { id, limit });
}

/** 插件清单（id/name/panels；插件启动器与技能 chip 数据源，与左栏技能同源）。 */
export function listPlugins(): Promise<{ id: string; name: string }[]> {
  return invoke("list_plugins");
}

/** 截图确认：overlay 把选区交给大脑（截图即问）。 */
export function finishSnip(rect: { left: number; top: number; width: number; height: number }): Promise<void> {
  return invoke("finish_snip", { rect });
}

/** 取消截图（单击/抖动选区 = 取消）。 */
export function cancelSnip(): Promise<void> {
  return invoke("cancel_snip");
}

// ---- 主屏 Feed ----

/** 查询主屏 Feed（响应经 brain-feed 事件回来）。 */
export function fetchFeed(limit = 60): Promise<void> {
  return invoke("get_feed", { limit });
}

/** 订阅主屏 Feed 响应（get_feed 查询的回包，经 Rust 转发）。 */
export function onFeed(cb: (r: FeedResponse) => void): Promise<UnlistenFn> {
  return listen<FeedResponse>("brain-feed", (ev) => cb(ev.payload));
}

/** 一次性取 Feed：发查询并等下一条 brain-feed；大脑不在线/超时返回空（主屏照常渲染空态）。 */
export function getFeedOnce(limit = 60, timeoutMs = 3000): Promise<FeedResponse> {
  return onceWithTimeout("brain-feed", () => fetchFeed(limit), EMPTY_FEED, timeoutMs);
}

/** 一次性手动提炼：发请求并等下一条 brain-distill-now；大脑不在线/超时返回 ok:false。 */
export function distillNow(timeoutMs = 90000): Promise<DistillNowResponse> {
  return onceWithTimeout("brain-distill-now", () => invoke("distill_now"), { ok: false, reason: "timeout" }, timeoutMs);
}

/** 反刍：开窗时 fire-and-forget 触发，大脑自行决定推不推。 */
export function recapCheck(): Promise<void> {
  return invoke("recap_check");
}

/** 每日回顾：发查询并等 brain-distill-timeline；大脑不在线/超时返回空。 */
export async function getDistillTimelineOnce(days = 14, timeoutMs = 3000): Promise<DistillDay[]> {
  const r = await onceWithTimeout<{ days: DistillDay[] }>(
    "brain-distill-timeline",
    () => invoke("get_distill_timeline", { days }),
    { days: [] },
    timeoutMs,
  );
  return r.days;
}

export function fetchDistillTimeline(days = 14): Promise<void> {
  return invoke("get_distill_timeline", { days });
}

/** 一次性取信任统计：发查询并等下一条 brain-feed-stats；大脑不在线/超时返回零值。 */
export async function getFeedStatsOnce(days = 7, timeoutMs = 3000): Promise<TrustStats> {
  const r = await onceWithTimeout<{ stats: TrustStats }>(
    "brain-feed-stats",
    () => invoke("get_feed_stats", { days }),
    { stats: EMPTY_STATS },
    timeoutMs,
  );
  return r.stats;
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

// ---- 主屏 widget / Dock ----

/** 查询主屏 widget（响应经 brain-widgets 事件回来）。 */
export function fetchWidgets(): Promise<void> {
  return invoke("get_widgets");
}

/** 订阅主屏 widget 响应（get_widgets 查询的回包，经 Rust 转发）。 */
export function onWidgets(cb: (r: WidgetsResponse) => void): Promise<UnlistenFn> {
  return listen<WidgetsResponse>("brain-widgets", (ev) => cb(ev.payload));
}

/** 一次性取 widget：发查询并等下一条 brain-widgets；大脑不在线/超时返回空（主屏不显示卡片区）。 */
export function getWidgetsOnce(timeoutMs = 3000): Promise<WidgetsResponse> {
  return onceWithTimeout("brain-widgets", () => fetchWidgets(), EMPTY_WIDGETS, timeoutMs);
}

/** 查询主屏 Dock（响应经 brain-dock-list 事件回来）。 */
export function fetchDockList(): Promise<void> {
  return invoke("dock_list");
}

/** 固定/取消固定一个插件（响应经 brain-dock-pin-set 事件来）。 */
export function setDockPin(pid: string, on: boolean): Promise<void> {
  return invoke("set_dock_pin", { pid, on });
}

/** 订阅 Dock 列表响应（dock_list 查询的回包，经 Rust 转发）。 */
export function onDockList(cb: (r: DockListResponse) => void): Promise<UnlistenFn> {
  return listen<DockListResponse>("brain-dock-list", (ev) => cb(ev.payload));
}

/** 订阅 Dock 固定/取消回执（含最新 dock 数组，供主屏整体刷新）。 */
export function onDockPinSet(cb: (p: DockPinSet) => void): Promise<UnlistenFn> {
  return listen<DockPinSet>("brain-dock-pin-set", (ev) => cb(ev.payload));
}

/** 一次性取 Dock：发查询并等下一条 brain-dock-list；大脑不在线/超时返回空（主屏渲染空 Dock）。 */
export function getDockListOnce(timeoutMs = 3000): Promise<DockListResponse> {
  return onceWithTimeout("brain-dock-list", () => fetchDockList(), EMPTY_DOCK, timeoutMs);
}

// ---- 记忆管理 ----

/** 一次性取记忆列表：发查询等 brain-mem-list；大脑不在线/超时返回空。 */
export function getMemListOnce(timeoutMs = 4000): Promise<MemListResponse> {
  return onceWithTimeout("brain-mem-list", () => invoke("get_mem_list"), EMPTY_MEM, timeoutMs);
}

/** 删除一条记忆，等该 id 的 brain-mem-deleted 回执；超时按失败处理。 */
export function memDelete(id: string, timeoutMs = 5000): Promise<MemDeleted> {
  return onceWithTimeout(
    "brain-mem-deleted",
    () => invoke("mem_delete", { id }),
    { id, ok: false, error: "删除超时（大脑不在线？）" },
    timeoutMs,
    (p) => p.id === id,
  );
}

/** 编辑一条记忆的文本，等该 id 的 brain-mem-edited 回执；超时按失败处理。 */
export function memEdit(id: string, text: string, timeoutMs = 5000): Promise<MemEdited> {
  return onceWithTimeout(
    "brain-mem-edited",
    () => invoke("mem_edit", { id, text }),
    { id, ok: false, error: "保存超时（大脑不在线？）" },
    timeoutMs,
    (p) => p.id === id,
  );
}

// ---- 用户设置 ----

/** 一次性取设置：发查询等 brain-settings；超时返回 null（调用方用默认）。 */
export async function getSettingsOnce(timeoutMs = 3000): Promise<SettingsValues | null> {
  const r = await onceWithTimeout<{ values: SettingsValues } | null>(
    "brain-settings",
    () => invoke("get_settings"),
    null,
    timeoutMs,
  );
  return r?.values ?? null;
}

/** 写设置（已知键才生效），等 brain-settings 回执；超时/null 按未生效处理。 */
export async function setSettings(values: Partial<SettingsValues>, timeoutMs = 3000): Promise<SettingsValues | null> {
  const r = await onceWithTimeout<{ values: SettingsValues } | null>(
    "brain-settings",
    () => invoke("set_settings", { values }),
    null,
    timeoutMs,
  );
  return r?.values ?? null;
}

/** 订阅设置变更回推（settings_set 生效后大脑广播 brain-settings）。 */
export function onSettings(cb: (s: SettingsValues) => void): Promise<UnlistenFn> {
  return listen<{ values: SettingsValues }>("brain-settings", (ev) => cb(ev.payload.values));
}

/** 一次性取手机伴生端配对信息；超时返回 null。 */
export function getHttpPairInfoOnce(timeoutMs = 3000): Promise<HttpPairInfo | null> {
  return onceWithTimeout(
    "brain-http-pair-info",
    () => invoke("get_http_pair_info"),
    null,
    timeoutMs,
  );
}

// ---- 感知 ----

/** 分页取感知日志；超时释放 listener 并返回不可用空态。 */
export function getPerceptionOnce(
  limit = 60,
  beforeId?: number,
  timeoutMs = 3000,
): Promise<PerceptionResponse> {
  return onceWithTimeout(
    "brain-perception",
    () => invoke("get_perception", { limit, beforeId }),
    EMPTY_PERCEPTION,
    timeoutMs,
  );
}

/** 删除单条观察；只接收匹配 id 的回执。 */
export function deletePerception(id: number, timeoutMs = 5000): Promise<PerceptionDeleted> {
  return onceWithTimeout(
    "brain-perception-deleted",
    () => invoke("perception_delete", { id }),
    { id, ok: false, error: "删除超时（大脑不在线？）" },
    timeoutMs,
    (p) => p.id === id,
  );
}

/** 清空观察日志并等待 sidecar 回执。 */
export function clearPerception(timeoutMs = 5000): Promise<PerceptionCleared> {
  return onceWithTimeout(
    "brain-perception-cleared",
    () => invoke("perception_clear"),
    { count: 0, error: "清空超时（大脑不在线？）" },
    timeoutMs,
  );
}

// ---- 事件订阅（全局信号）----

/** 订阅大脑事件流，返回取消监听函数。 */
export function onBrainEvent(cb: (e: BrainEvent) => void): Promise<UnlistenFn> {
  return listen<BrainEvent>("brain-event", (ev) => cb(ev.payload));
}

/** 订阅一次 run 完成信号。 */
export function onRunDone(cb: (v: unknown) => void): Promise<UnlistenFn> {
  return listen("brain-run-done", (ev) => cb(ev.payload));
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

/** 唤起条动作广播（invoke-bar 窗 emit）：解释/翻译/存素材。 */
export function onInvokeAction(cb: (action: string) => void): Promise<UnlistenFn> {
  return listen<{ action: string }>("invoke-action", (e) => cb(e.payload.action));
}

/** 框选完成广播（finish_snip 后 Rust emit）：主窗展开 + chip 提示提问。 */
export function onSnipCaptured(cb: (r: { width: number; height: number }) => void): Promise<UnlistenFn> {
  return listen<{ width: number; height: number }>("snip-captured", (e) => cb(e.payload));
}

/** deep-link：pet 窗气泡点击 → 通知 home 窗切回顾 mode + 跳当天。 */
export function emitRecapOpen(day: string): Promise<void> {
  return emit("recap-open", { day });
}

export function onRecapOpen(cb: (day: string) => void): Promise<UnlistenFn> {
  return listen<{ day: string }>("recap-open", (e) => cb(e.payload.day));
}
