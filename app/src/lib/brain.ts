// 封装与大脑 sidecar 的通信（经 Tauri Rust 桥）。
import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

export type BrainEventKind =
  | "thought"
  | "action_proposed"
  | "confirmation_needed"
  | "action_result"
  | "final_reply"
  | "error"
  | "listening"
  | "listening_done"
  | "speaking";

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
}

export interface BrainEvent {
  kind: BrainEventKind;
  text?: string;
  action?: BrainAction;
  result?: BrainResult;
  confirmation_id?: string;
}

/** 发送用户输入，触发大脑一次 run。 */
export function runInput(text: string): Promise<void> {
  return invoke("run_input", { text });
}

/** 回复高风险确认（Rust 命令参数 confirmation_id 在 JS 侧为 camelCase）。 */
export function sendConfirm(confirmationId: string, approved: boolean): Promise<void> {
  return invoke("confirm", { confirmationId, approved });
}

/** 触发语音输入：sidecar 录音→STT→run→TTS 播报（Plan 4a 最小语音）。 */
export function voiceStart(): Promise<void> {
  return invoke("voice_start");
}

/** 订阅大脑事件流，返回取消监听函数。 */
export function onBrainEvent(cb: (e: BrainEvent) => void): Promise<UnlistenFn> {
  return listen<BrainEvent>("brain-event", (ev) => cb(ev.payload));
}

/** 订阅一次 run 完成信号。 */
export function onRunDone(cb: (v: unknown) => void): Promise<UnlistenFn> {
  return listen("brain-run-done", (ev) => cb(ev.payload));
}
