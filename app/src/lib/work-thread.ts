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

export function runIsLive(
  bubbles: WorkBubble[],
  indices: number[],
  streamingIdx: number | null,
): boolean {
  if (streamingIdx !== null && indices.includes(streamingIdx)) return true;
  return indices.some((i) => bubbles[i].proc && !bubbles[i].proc!.done);
}
