/** 吸附几何：画布摆放的网格对齐与磁吸（纯函数）。 */

export type FrameBox = {
  left?: number;
  top?: number;
  right?: number;
  bottom?: number;
  width?: number;
  height?: number;
  z?: number;
};

export type ResolvedFrame = {
  left: number;
  top: number;
  width: number;
  height: number;
  z: number;
};

export type StageSize = { width: number; height: number };
export const DEFAULT_STAGE: StageSize = { width: 1280, height: 800 };

export const SNAP_PITCH = 8;
export const SNAP_MAGNET = 24;
export const SNAP_STICKY = 12;
export const SNAP_GAP = 8;

export function snapValue(n: number, pitch = SNAP_PITCH): number {
  return Math.round(n / pitch) * pitch + 0;
}

export function snapToGuides(
  n: number,
  guides: readonly number[],
  magnet = SNAP_MAGNET,
): number {
  let best: number | null = null;
  let dist = magnet + 1;
  for (const g of guides) {
    const d = Math.abs(n - g);
    if (d <= magnet && d < dist) {
      best = g;
      dist = d;
    }
  }
  return best ?? n;
}

type SnapRect = { left: number; top: number; width: number; height: number };

export type SnapHit = {
  left: number;
  top: number;
  xs: number[];
  ys: number[];
};

type EdgeGuide = { value: number; role: "start" | "end" | "stage" };

function snapAxis(
  start: number,
  size: number,
  guides: readonly EdgeGuide[],
  magnet: number,
  gap: number,
): { pos: number; lines: number[] } {
  let bestPos = start;
  let bestLine: number | null = null;
  let dist = magnet + 1;
  const consider = (pos: number, line: number) => {
    const d = Math.abs(pos - start);
    if (d <= magnet && d < dist) {
      bestPos = pos;
      bestLine = line;
      dist = d;
    }
  };
  for (const g of guides) {
    if (g.role === "stage") {
      consider(g.value, g.value);
      consider(g.value - size, g.value);
      continue;
    }
    if (g.role === "start") {
      consider(g.value, g.value);
      consider(g.value - size - gap, g.value);
    } else {
      consider(g.value + gap, g.value);
      consider(g.value - size, g.value);
    }
  }
  return { pos: bestPos, lines: bestLine === null ? [] : [bestLine] };
}

function holdAxis(start: number, holdPos: number | undefined, holdLines: readonly number[] | undefined): { pos: number; lines: number[] } | null {
  if (holdPos === undefined || !holdLines?.length) return null;
  if (Math.abs(start - holdPos) > SNAP_MAGNET + SNAP_STICKY) return null;
  return { pos: holdPos, lines: [...holdLines] };
}

export function snapBox(
  box: SnapRect,
  others: readonly SnapRect[],
  stage?: StageSize,
  hold?: SnapHit,
): SnapHit {
  const xs: EdgeGuide[] = others.flatMap((o) => [
    { value: o.left, role: "start" },
    { value: o.left + o.width, role: "end" },
  ]);
  const ys: EdgeGuide[] = others.flatMap((o) => [
    { value: o.top, role: "start" },
    { value: o.top + o.height, role: "end" },
  ]);
  if (stage) {
    xs.push({ value: 0, role: "stage" }, { value: stage.width, role: "stage" });
    ys.push({ value: 0, role: "stage" }, { value: stage.height, role: "stage" });
  }
  const x = holdAxis(box.left, hold?.left, hold?.xs) ?? snapAxis(box.left, box.width, xs, SNAP_MAGNET, SNAP_GAP);
  const y = holdAxis(box.top, hold?.top, hold?.ys) ?? snapAxis(box.top, box.height, ys, SNAP_MAGNET, SNAP_GAP);
  return { left: x.pos, top: y.pos, xs: x.lines, ys: y.lines };
}

export function settleSnap(hit: SnapHit): SnapHit {
  return {
    left: hit.xs.length ? hit.left : snapValue(hit.left),
    top: hit.ys.length ? hit.top : snapValue(hit.top),
    xs: hit.xs,
    ys: hit.ys,
  };
}
