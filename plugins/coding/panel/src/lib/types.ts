// coding 面板协议类型(与 plugins/coding/skills 的 _runner.py/_codex_runner.py normalize 输出对齐)。
// 容缺红线:agent 在 rewind/stop 兜底事件缺失;usage 可整体缺;cost_usd 恒 null(codex);
// history fallback 消息无 uuid;codex 会话无 user_msg/permission_*/rewind_ok。
// marker 例外：非 runner normalize 输出，由 coding.py _stream 直发（codex resume fallback 提示等流内留痕）。
export type AgentName = "claude-code" | "cc" | "codex";

export interface Usage {
  duration_ms?: number;
  cost_usd?: number | null;
  input_tokens?: number;
  output_tokens?: number;
}

export type CodingEvent =
  | { kind: "text_delta"; text: string }
  | { kind: "thinking"; text: string }
  | { kind: "tool_use"; tool: string; input: Record<string, unknown> }
  | { kind: "file_edit"; tool: string; path: string | null; old: string | null; new: string | null }
  | { kind: "tool_result"; text: string; is_error: boolean }
  | { kind: "user_msg"; uuid: string; text: string }
  | { kind: "permission_request"; rid: string; tool: string; input: Record<string, unknown> }
  | { kind: "permission_done"; rid: string; allow: boolean }
  | { kind: "rewind_ok"; text?: string }
  | { kind: "marker"; text?: string }
  | { kind: "done"; usage?: Usage }
  | { kind: "stopped"; text?: string }
  | { kind: "error"; text: string };

export interface PanelData {
  session_id?: string;
  agent?: string;
  event?: CodingEvent;
  attach?: boolean;
}

export interface SessionRow {
  id: string; agent: string; cwd: string; prompt: string;
  status: "running" | "done" | "stopped" | "failed";
  created_at: number; finished_at: number; cc_session_id: string;
  source: string; mode: string;
  live: "waiting" | "running" | "idle";
}

export interface DriverInfo { id: string; available: boolean; version?: string | null }
export interface HistoryMessage { role: "user" | "assistant" | "marker"; text: string; uuid?: string }

// coding.handoff_list 条目(Codex→CC 接续选择器;timestamp 兼容 ISO/epoch/已格式化)
export interface HandoffSessionItem { session_id: string; timestamp?: number | string | null; first_line?: string }

// coding.last_sessions 结果(接续浮层区 1「上次会话」跨源检测;整体失败降级 null)
export interface LastCcSession { ts?: number | string | null; summary?: string; message_count?: number | null; cc_session_id?: string }
export interface LastCodexSession { ts?: number | string | null; summary?: string; session_id?: string }
export interface LastSessions { cc?: LastCcSession | null; codex?: LastCodexSession | null }
