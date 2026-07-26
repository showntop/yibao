// 封装与大脑 sidecar 的通信（经 Tauri Rust 桥）。
import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

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
}

// ---- 会话分流（v2 §5）：run/语音/面板调用带 surface 标签，大脑透传回事件流与历史 ----
// 模块级当前 surface：宠物窗恒 pet；面板窗随焦点插件变化（PanelApp setSurface）。
let _surface = "pet";

/** 设置本窗口后续请求的 surface（面板窗焦点变化时调用）。 */
export function setSurface(s: string): void {
  _surface = s;
}

/** 发送用户输入，触发大脑一次 run。 */
export function runInput(text: string): Promise<void> {
  return invoke("run_input", { text, surface: _surface });
}

/** 回复高风险确认（Rust 命令参数 confirmation_id 在 JS 侧为 camelCase）。 */
export function sendConfirm(confirmationId: string, approved: boolean): Promise<void> {
  return invoke("confirm", { confirmationId, approved });
}

/** 触发语音输入：sidecar 录音→STT→run→TTS 播报（Plan 4a 最小语音）。 */
export function voiceStart(): Promise<void> {
  return invoke("voice_start", { surface: _surface });
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
): Promise<void> {
  return invoke("panel_action", { id: id ?? Date.now() % 2 ** 31, method, params, surface: _surface });
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
