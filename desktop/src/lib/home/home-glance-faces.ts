/** 主屏 glance 的脸：有没有内容、怎么写成一眼，不碰装配。 */
import { resolve, type SchemaDoc } from "../schema";
import { truncate } from "../text";
import type { FeedItem, MemItem, PerceptionItem, PendingConfirm, RunningTask, WidgetPayload } from "../brain";

export type RemindFace = { text: string; when: string; tight: boolean };
export type TaskFace = { id: string; label: string; stuck: "等你" | "在跑" };
export type SparkPick = { id: string; text: string };
export type GlimpseFace = { app: string; title: string };
export type CatchFace = { kind: "clipboard" | "note"; text: string };

const NINETY_MIN = 90 * 60 * 1000;
const GLIMPSE_FRESH = 8 * 60 * 1000;
const CATCH_FRESH = 3 * 60 * 1000;

export function remindTight(when: string, now = new Date()): boolean {
  const match = when.match(/(\d{1,2}):(\d{2})/);
  if (!match) return false;
  const due = new Date(now);
  due.setHours(Number(match[1]), Number(match[2]), 0, 0);
  const diff = due.getTime() - now.getTime();
  return diff >= 0 && diff <= NINETY_MIN;
}

export function remindFaces(
  widgetRows: ReadonlyArray<{ text?: unknown; when?: unknown }> | undefined,
  feed: readonly FeedItem[],
  now = new Date(),
): RemindFace[] {
  const fromWidget = (widgetRows ?? [])
    .map((row) => ({
      text: String(row.text ?? "").trim(),
      when: String(row.when ?? "").trim(),
    }))
    .filter((row) => row.text)
    .slice(0, 3)
    .map((row) => ({ ...row, tight: remindTight(row.when, now) }));
  if (fromWidget.length) return fromWidget;
  return feed
    .filter((item) => item.kind === "reminder" && item.status !== "ignore")
    .slice(0, 3)
    .map((item) => ({ text: item.text, when: "", tight: false }));
}

export function taskFaces(
  tasks: readonly RunningTask[],
  approvals: readonly PendingConfirm[],
): TaskFace[] {
  const waiting = approvals.length > 0;
  return tasks.slice(0, 2).map((task) => ({
    id: task.id,
    label: task.label,
    stuck: waiting ? "等你" as const : "在跑" as const,
  }));
}

export function todayBands(input: {
  done: number;
  chats: number;
  mems: number;
  appSeconds?: Record<string, number>;
}): { values: number[]; empty: boolean } {
  const apps = Object.values(input.appSeconds ?? {});
  const appTotal = apps.reduce((sum, n) => sum + Math.max(0, n), 0);
  if (appTotal > 0) {
    const top = [...apps].sort((a, b) => b - a).slice(0, 8);
    const peak = Math.max(...top, 1);
    const values = Array.from({ length: 8 }, (_, i) => Number(((top[i] ?? 0) / peak).toFixed(3)));
    return { values, empty: false };
  }
  const raw = [input.done, input.chats, input.mems].map((n) => Math.max(0, n));
  const peak = Math.max(...raw, 0);
  if (!peak) return { values: [0, 0, 0, 0, 0, 0, 0, 0], empty: true };
  const tri = raw.map((n) => n / peak);
  const values = [tri[0], tri[0], tri[1], tri[1], tri[1], tri[2], tri[2], (tri[0] + tri[1] + tri[2]) / 3]
    .map((n) => Number(n.toFixed(3)));
  return { values, empty: false };
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

export function pluginGlanceLine(schema: unknown, data: unknown): string {
  const doc = schema as SchemaDoc | null;
  const root = asRecord(data) ?? {};
  const bind = doc && "bind" in doc ? doc.bind?.items ?? "$data.rows" : "$data.rows";
  const items = resolve(bind, { data: root });
  const list = Array.isArray(items) ? items : [];
  const first = asRecord(list[0]);
  if (!first) return "";
  const titleExpr = doc && "item" in doc ? doc.item?.title ?? "$item.text" : "$item.text";
  const subExpr = doc && "item" in doc ? doc.item?.subtitle ?? "" : "";
  const title = String(resolve(titleExpr, { data: root, item: first }) ?? "").trim();
  const sub = subExpr ? String(resolve(subExpr, { data: root, item: first }) ?? "").trim() : "";
  if (!title) return "";
  return sub ? `${title} · ${sub}` : title;
}

export function pluginHasGlance(widget: Pick<WidgetPayload, "schema" | "data">): boolean {
  return Boolean(pluginGlanceLine(widget.schema, widget.data));
}

function tokensOf(text: string): string[] {
  const words = text
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .split(/\s+/)
    .filter((token) => token.length >= 2);
  const grams = [...words];
  for (const word of words) {
    if (!/[\u3400-\u9fff]/.test(word)) continue;
    for (let i = 0; i < word.length - 1; i += 1) grams.push(word.slice(i, i + 2));
  }
  return grams;
}

export function pickSpark(
  memories: readonly MemItem[],
  focus: string,
  dismissedId?: string | null,
): SparkPick | null {
  const pool = memories.filter((item) => item.id !== dismissedId && item.text.trim());
  if (!pool.length) return null;
  const keys = new Set(tokensOf(focus));
  const scored = keys.size
    ? pool
      .map((item) => {
        const hit = tokensOf(item.text).filter((token) => keys.has(token)).length;
        return { item, hit };
      })
      .filter((row) => row.hit > 0)
      .sort((a, b) => b.hit - a.hit)
    : [];
  const chosen = scored[0]?.item ?? (keys.size ? null : pool[0]);
  if (!chosen) return null;
  const text = chosen.text.trim();
  return { id: chosen.id, text: truncate(text, 72) };
}

export function glimpseFace(
  items: readonly PerceptionItem[],
  now = Date.now(),
): GlimpseFace | null {
  const latest = items
    .filter((item) => item.source === "app" && item.payload.app)
    .sort((a, b) => b.ts - a.ts)[0];
  if (!latest) return null;
  if (now - latest.ts * 1000 > GLIMPSE_FRESH) return null;
  return {
    app: String(latest.payload.app ?? ""),
    title: String(latest.payload.title ?? ""),
  };
}

export function catchFace(
  items: readonly PerceptionItem[],
  noteLine: string,
  now = Date.now(),
): CatchFace | null {
  const clip = items
    .filter((item) => item.source === "clipboard")
    .sort((a, b) => b.ts - a.ts)[0];
  const clipText = clip ? String(clip.payload.text ?? clip.payload.preview ?? "").trim() : "";
  if (clip && clipText && now - clip.ts * 1000 <= CATCH_FRESH) {
    return { kind: "clipboard", text: truncate(clipText, 80) };
  }
  const note = noteLine.trim();
  if (note) return { kind: "note", text: truncate(note, 80) };
  return null;
}

export const SCRATCH_KEY = "yibao-scratch";
export const SCRATCH_TINT_KEY = "yibao-scratch-tint";
export const SPARK_DISMISS_KEY = "yibao-spark-dismissed";
export const SCRATCH_TINTS = ["amber", "moss", "rose"] as const;
export type ScratchTint = (typeof SCRATCH_TINTS)[number];

export function readScratch(storage: Pick<Storage, "getItem"> | null): string {
  try { return storage?.getItem(SCRATCH_KEY) ?? ""; } catch { return ""; }
}

export function writeScratch(text: string, storage: Pick<Storage, "setItem"> | null): void {
  try { storage?.setItem(SCRATCH_KEY, text); } catch { /* ignore quota */ }
}

export function isScratchTint(v: unknown): v is ScratchTint {
  return v === "amber" || v === "moss" || v === "rose";
}

export function readScratchTint(storage: Pick<Storage, "getItem"> | null): ScratchTint {
  try {
    const raw = storage?.getItem(SCRATCH_TINT_KEY);
    return isScratchTint(raw) ? raw : "amber";
  } catch {
    return "amber";
  }
}

export function writeScratchTint(tint: ScratchTint, storage: Pick<Storage, "setItem"> | null): void {
  try { storage?.setItem(SCRATCH_TINT_KEY, tint); } catch { /* ignore quota */ }
}

export function readSparkDismiss(storage: Pick<Storage, "getItem"> | null, day: string): string | null {
  try {
    const raw = storage?.getItem(SPARK_DISMISS_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { id?: string; day?: string };
    return parsed.day === day ? parsed.id ?? null : null;
  } catch { return null; }
}

export function writeSparkDismiss(id: string, day: string, storage: Pick<Storage, "setItem"> | null): void {
  try { storage?.setItem(SPARK_DISMISS_KEY, JSON.stringify({ id, day })); } catch { /* ignore */ }
}

// ---- 提醒卡（wb-prototype home.png 右列）：圆圈状态只从时间推导，不编造完成态 ----

export type RemindCardState = "done" | "due" | "daily";

export interface RemindCardItem {
  text: string;
  when: string;
  state: RemindCardState;
}

function sameLocalDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

/** 提醒卡行：feed 里今天的真实提醒（已响/待响）+ 提醒库存量行（每天/周期，不标状态）。 */
export function remindCardFaces(
  widgetRows: ReadonlyArray<{ text?: unknown; when?: unknown }> | undefined,
  feed: readonly FeedItem[],
  now = new Date(),
): RemindCardItem[] {
  const pad = (n: number) => String(n).padStart(2, "0");
  const todays = feed
    .filter((it) => it.kind === "reminder" && it.status !== "ignore")
    .filter((it) => sameLocalDay(new Date(it.ts), now))
    .sort((a, b) => a.ts - b.ts)
    .map((it) => {
      const d = new Date(it.ts);
      return {
        text: it.text,
        when: `${pad(d.getHours())}:${pad(d.getMinutes())}`,
        state: (it.ts <= now.getTime() ? "done" : "due") as RemindCardState,
      };
    });
  const recurring = (widgetRows ?? [])
    .map((row) => ({ text: String(row.text ?? "").trim(), when: String(row.when ?? "").trim() }))
    .filter((row) => row.text)
    .map((row) => {
      // 带时刻的周期提醒同样推导状态（每天 HH:MM vs 现在），没时刻的才不标
      const match = row.when.match(/(\d{1,2}):(\d{2})/);
      if (!match) {
        return { text: row.text, when: row.when || "周期", state: "daily" as RemindCardState };
      }
      const due = new Date(now);
      due.setHours(Number(match[1]), Number(match[2]), 0, 0);
      const state: RemindCardState = due.getTime() <= now.getTime() ? "done" : "due";
      const prefix = row.when.includes("每天") ? "每天 " : "";
      return { text: row.text, when: `${prefix}${match[1].padStart(2, "0")}:${match[2]}`, state };
    });
  return [...todays, ...recurring];
}
