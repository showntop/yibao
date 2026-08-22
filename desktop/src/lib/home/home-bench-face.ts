/** 整桌「工位」：编码会话或智能体正在跑的那一件。 */

import { truncate } from "../text";
import type { RunningTask, WidgetPayload } from "../brain";

export type BenchKind = "coding" | "agent";

export type BenchFace = {
  kind: BenchKind;
  label: string;
  who: string;
  state: "在跑";
  method: string;
  params: Record<string, unknown>;
  surface: string;
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function rowsOf(data: unknown, key: string): Record<string, unknown>[] {
  const root = asRecord(data);
  const rows = root && Array.isArray(root[key]) ? root[key] : [];
  return rows.map(asRecord).filter((row): row is Record<string, unknown> => Boolean(row));
}

function isRunning(status: unknown): boolean {
  return String(status ?? "").trim().toLowerCase() === "running";
}

function cwdName(cwd: unknown): string {
  const path = String(cwd ?? "").trim().replace(/\/+$/, "");
  if (!path) return "";
  return path.split(/[/\\]/).filter(Boolean).pop() ?? "";
}

function codingWho(agent: unknown): string {
  const name = String(agent ?? "").toLowerCase();
  if (name.includes("codex")) return "Codex";
  return "编码";
}

function agentWho(agent: unknown): string {
  const name = String(agent ?? "").trim();
  return name || "智能体";
}

export function benchFace(input: {
  coding?: unknown;
  widgets?: ReadonlyArray<Pick<WidgetPayload, "panel" | "data">>;
  feed?: readonly RunningTask[];
}): BenchFace | null {
  const coding = rowsOf(input.coding, "sessions").find((row) => isRunning(row.status) || row.live);
  if (coding) {
    const id = String(coding.id ?? "").trim();
    const prompt = String(coding.prompt ?? "").trim();
    const folder = cwdName(coding.cwd);
    return {
      kind: "coding",
      label: truncate(prompt || folder || "编码任务", 40),
      who: codingWho(coding.agent),
      state: "在跑",
      method: id ? "coding.attach" : "coding.studio",
      params: id ? { session_id: id } : {},
      surface: "panel:coding",
    };
  }

  const agents = input.widgets?.find((widget) => widget.panel.startsWith("agents:"));
  const agentRow = rowsOf(agents?.data, "rows").find((row) => isRunning(row.status));
  if (agentRow) {
    const prompt = String(agentRow.prompt ?? "").trim();
    return {
      kind: "agent",
      label: truncate(prompt || "派出去的活", 40),
      who: agentWho(agentRow.agent),
      state: "在跑",
      method: "agents.task_list",
      params: {},
      surface: "panel:agents",
    };
  }

  const feed = input.feed?.[0];
  if (feed) {
    return {
      kind: "agent",
      label: truncate(feed.label || feed.prompt || "派出去的活", 40),
      who: feed.kind === "script" ? "脚本" : "智能体",
      state: "在跑",
      method: "agents.task_list",
      params: {},
      surface: "panel:agents",
    };
  }

  return null;
}
