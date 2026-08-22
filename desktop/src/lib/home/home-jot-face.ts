/** 整桌「闪念」：闪念盘 notes:widget 最近一条。 */

import { truncate } from "../text";
import type { WidgetPayload } from "../brain";

export type JotFace = {
  text: string;
  when: string;
  open: string;
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

export function jotWhen(ts: number, now = new Date()): string {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  if (Number.isNaN(d.getTime())) return "";
  if (d.getFullYear() === now.getFullYear()) return `${d.getMonth() + 1}月${d.getDate()}日`;
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`;
}

export function jotFace(
  widgets: ReadonlyArray<Pick<WidgetPayload, "panel" | "data" | "open">>,
  now = new Date(),
): JotFace | null {
  const hit = widgets.find((widget) => widget.panel.startsWith("notes:"));
  const data = asRecord(hit?.data);
  const rows = data && Array.isArray(data.rows) ? data.rows : [];
  const first = asRecord(rows[0]);
  if (!first) return null;
  const text = String(first.text ?? "").trim();
  if (!text) return null;
  const ts = Number(first.created_at ?? 0);
  return {
    text: truncate(text, 80),
    when: jotWhen(Number.isFinite(ts) ? ts : 0, now),
    open: String(hit?.open ?? "notes.list"),
  };
}
