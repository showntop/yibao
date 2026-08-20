/** 一次工作（用户一句之后、模型边说边调用工具）在对话流里合成一条线索。 */

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

/** 过程行、或普通 AI 正文（提醒/告警/协作卡不算进同一轮工作）。 */
export function isWorkPiece(b: WorkBubble): boolean {
  if (b.panelLink) return false;
  if (b.proc) return true;
  return b.role === "ai" && !b.icon;
}

export function groupThread(
  bubbles: WorkBubble[],
  isNewDay: (index: number) => boolean,
): ThreadItem[] {
  const items: ThreadItem[] = [];
  let i = 0;
  while (i < bubbles.length) {
    if (isNewDay(i)) items.push({ type: "day", index: i });
    const b = bubbles[i];
    if (b.role === "user") {
      items.push({ type: "user", index: i });
      i += 1;
      continue;
    }
    if (isWorkPiece(b)) {
      const start = i;
      const indices: number[] = [];
      while (i < bubbles.length && isWorkPiece(bubbles[i])) {
        if (indices.length > 0 && isNewDay(i)) break;
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
    .filter((b) => b.role === "ai" && !b.proc)
    .map((b) => b.text.trim())
    .filter(Boolean)
    .join("\n\n");
}

export function runTailIndex(bubbles: WorkBubble[], indices: number[]): number {
  for (let k = indices.length - 1; k >= 0; k -= 1) {
    const b = bubbles[indices[k]];
    if (b.role === "ai" && !b.proc) return indices[k];
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

