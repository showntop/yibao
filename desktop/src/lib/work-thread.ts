/** 一次工作（用户一句之后、模型边说边调用工具）在对话流里合成一条线索。 */
import { isDeskPathCloseLine } from "./home/home-desk-presence.ts";

export type WorkBubble = {
  role: "user" | "ai" | "sys";
  text: string;
  panelLink?: boolean;
  proc?: { done: boolean };
  icon?: string;
  halted?: boolean;
  metrics?: unknown;
  refs?: unknown[];
};

export type ThreadItem =
  | { type: "day"; index: number }
  | { type: "user"; index: number }
  | { type: "misc"; index: number }
  | { type: "run"; start: number; indices: number[] };

/** 纸上的一页：一句用户话 + 随后一轮工作。提醒挂在当前页上。 */
export type PaperPage = {
  userIndex: number | null;
  runIndices: number[];
  miscIndices: number[];
};

/** 过程行、或普通 AI 正文（提醒/告警/委派卡不算进同一轮工作）。 */
export function isOrphanDeskStamp(bubble: WorkBubble): boolean {
  if (bubble.panelLink) return false;
  const text = bubble.text.trim();
  return /^(已请|已走|摊开|收起|用了)\s/.test(text) && text.includes("·");
}

/** 插件任务收尾复用了 reminder 通道写 Feed，不是用户闹钟。 */
export function isTaskLogEvent(e: { task?: unknown; type?: string }): boolean {
  return Boolean(e.task) || e.type === "watch_command";
}

/** 已落库的任务收尾（沙箱/agent）带 clock 图标，文案是完成日志。
 *  emoji 前缀是协议事实：后端任务事件 text 保留 ✅/❌/⏰/⏹ 前缀（mobile raw text 依赖它），
 *  desktop 展示层另行剥离（lib/text.ts stripTaskStatusEmoji），此正则只用于识别、不用于展示。 */
export function isTaskLogBubble(bubble: WorkBubble): boolean {
  if (bubble.icon !== "clock") return false;
  const text = bubble.text.trim();
  return /^(✅|❌|⏰|⏹)/u.test(text) && /(完成|失败|超时|已停止)/.test(text);
}

export function isWorkPiece(bubble: WorkBubble): boolean {
  if (bubble.panelLink || /^(已请|已走|摊开|收起|用了)\s/.test(bubble.text.trim())) return false;
  if (isTaskLogBubble(bubble)) return false;
  if (bubble.proc) return true;
  return bubble.role === "ai" && !bubble.icon;
}

function isThreadSkip(bubble: WorkBubble): boolean {
  return isOrphanDeskStamp(bubble) || isDeskPathCloseLine(bubble.text) || isTaskLogBubble(bubble);
}

export function groupThread(
  bubbles: WorkBubble[],
  isNewDay: (index: number) => boolean,
): ThreadItem[] {
  const items: ThreadItem[] = [];
  let i = 0;
  while (i < bubbles.length) {
    if (isNewDay(i)) items.push({ type: "day", index: i });
    const bubble = bubbles[i];
    if (isThreadSkip(bubble)) {
      i += 1;
      continue;
    }
    if (bubble.role === "user") {
      items.push({ type: "user", index: i });
      i += 1;
      continue;
    }
    if (isWorkPiece(bubble)) {
      const start = i;
      const indices: number[] = [];
      while (i < bubbles.length) {
        if (indices.length > 0 && isNewDay(i)) break;
        if (isThreadSkip(bubbles[i])) {
          i += 1;
          continue;
        }
        if (!isWorkPiece(bubbles[i])) break;
        indices.push(i);
        i += 1;
      }
      items.push({ type: "run", start, indices });
      continue;
    }
    items.push({ type: "misc", index: i });
    i += 1;
  }
  return items;
}

export function runAnswer(bubbles: WorkBubble[], indices: number[]): string {
  return indices
    .map((i) => bubbles[i])
    .filter((bubble) => bubble.role === "ai" && !bubble.proc)
    .map((bubble) => bubble.text.trim())
    .filter(Boolean)
    .join("\n\n");
}

/** 会客只摊刚说的几句；过程行、提醒、空正文不进屋里。 */
export function talkTurns(bubbles: WorkBubble[], limit = 6): number[] {
  const idxs: number[] = [];
  for (let i = 0; i < bubbles.length; i += 1) {
    const bubble = bubbles[i];
    if (bubble.panelLink || bubble.proc || bubble.icon || isOrphanDeskStamp(bubble) || isDeskPathCloseLine(bubble.text)) continue;
    if (bubble.role !== "user" && bubble.role !== "ai") continue;
    if (!bubble.text.trim()) continue;
    idxs.push(i);
  }
  if (limit <= 0) return [];
  return idxs.slice(-limit);
}

/** 把一段长回复切成视觉小说的拍。 */
export function spokenPlain(text: string): string {
  return text
    .replace(/\*\*(.*?)\*\*/g, "$1")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/^[*-]\s+/gm, "")
    .replace(/`+/g, "")
    .trim();
}

export function talkBeats(text: string, max = 120): string[] {
  const clean = spokenPlain(text);
  if (!clean) return [];
  const lines = clean.split(/\n+/).map((line) => line.trim()).filter(Boolean);
  const beats: string[] = [];
  let buf = "";
  const flush = () => {
    if (buf) beats.push(buf);
    buf = "";
  };
  const pushLong = (line: string) => {
    const parts = line.split(/(?<=[。！？!?…])\s*/);
    for (const part of parts) {
      if (!part) continue;
      if (part.length > max) {
        flush();
        for (let i = 0; i < part.length; i += max) beats.push(part.slice(i, i + max));
        continue;
      }
      if (buf && buf.length + part.length > max) flush();
      buf += part;
    }
  };
  for (const line of lines) {
    if (line.length > max) {
      flush();
      pushLong(line);
      flush();
      continue;
    }
    if (buf && buf.length + 1 + line.length > max) flush();
    buf = buf ? `${buf}\n${line}` : line;
  }
  flush();
  return beats;
}

export function runTailIndex(bubbles: WorkBubble[], indices: number[]): number {
  for (let k = indices.length - 1; k >= 0; k -= 1) {
    const bubble = bubbles[indices[k]];
    if (bubble.role === "ai" && !bubble.proc) return indices[k];
  }
  return indices[indices.length - 1] ?? 0;
}

export function groupPages(bubbles: WorkBubble[]): PaperPage[] {
  const items = groupThread(bubbles, () => false);
  const pages: PaperPage[] = [];
  let current: PaperPage = { userIndex: null, runIndices: [], miscIndices: [] };

  const occupied = (p: PaperPage) =>
    p.userIndex !== null || p.runIndices.length > 0 || p.miscIndices.length > 0;
  const flush = () => {
    if (!occupied(current)) return;
    pages.push(current);
    current = { userIndex: null, runIndices: [], miscIndices: [] };
  };

  for (const item of items) {
    if (item.type === "day") continue;
    if (item.type === "user") {
      flush();
      current.userIndex = item.index;
      continue;
    }
    if (item.type === "run") {
      current.runIndices = item.indices;
      flush();
      continue;
    }
    if (occupied(current)) current.miscIndices.push(item.index);
    else if (pages.length) pages[pages.length - 1].miscIndices.push(item.index);
    else current.miscIndices.push(item.index);
  }
  flush();
  return pages;
}

export function runIsLive(
  bubbles: WorkBubble[],
  indices: number[],
  streamingIdx: number | null,
): boolean {
  if (streamingIdx !== null && indices.includes(streamingIdx)) return true;

  return indices.some((i) => bubbles[i].proc && !bubbles[i].proc!.done);
}

function lastWorkIndex(bubbles: WorkBubble[]): number {
  for (let i = bubbles.length - 1; i >= 0; i -= 1) {
    if (isWorkPiece(bubbles[i])) return i;
  }
  return -1;
}

/** 复制/赞/重写只属于已经收束的 AI 回答；大脑还在跑时，当前这轮不露工具栏。 */
export function runShowFooter(
  bubbles: WorkBubble[],
  indices: number[],
  streamingIdx: number | null,
  agentBusy = false,
): boolean {
  const hasAnswer = indices.some((i) => {
    const bubble = bubbles[i];
    return bubble?.role === "ai" && !bubble.proc && !bubble.icon;
  });
  if (!hasAnswer) return false;
  if (indices.some((i) => bubbles[i].halted)) return true;
  if (runIsLive(bubbles, indices, streamingIdx)) return false;
  if (!agentBusy) return true;
  const lastWork = lastWorkIndex(bubbles);
  return lastWork >= 0 && !indices.includes(lastWork);
}

/** 纸边图章：去重、至多几枚。过程行同名会堆成一列。 */
export function paperStamps(labels: readonly string[], limit = 3): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of labels) {
    const label = raw.trim();
    if (!label || seen.has(label)) continue;
    seen.add(label);
    out.push(label);
    if (out.length >= limit) break;
  }
  return out;
}

/** 纸上的出错说明。原始 JSON/协议文案进 detail，摘要给人读。 */
export function paperErrorNotice(text: string): { summary: string; detail: string } | null {
  const raw = text.trim();
  if (!raw) return null;
  const lower = raw.toLowerCase();
  const looksError = /大脑出错|error code|insufficient balance|invalid_request_error|unknown_error/.test(lower);
  if (!looksError) return null;
  if (/insufficient balance|error code:\s*402/.test(lower)) {
    return { summary: "大脑暂时没额度", detail: raw };
  }
  const stripped = raw.replace(/^大脑出错[:：]\s*/u, "").trim();
  const first = stripped.split(/[\n{]/)[0].replace(/\s*[-–:：]+\s*$/u, "").trim();
  const summary = first && first.length <= 42 && !/^error code/i.test(first) ? first : "大脑出错";
  return { summary, detail: raw };
}

