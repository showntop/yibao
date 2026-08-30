/** 主屏装配解析与渲染几何：preset + 偏好 → Assembly（grid / canvas 两套落点，纯函数）。 */
import {
  getPartMeta,
  isPartId,
  isPluginPart,
  type DockEdge,
  type PartId,
  type PartKind,
} from "./parts";
import {
  DEFAULT_STAGE,
  SNAP_GAP,
  type FrameBox,
  type ResolvedFrame,
  type StageSize,
} from "./snap";
import {
  HOME_PRESETS,
  type Attach,
  type HomePreset,
  type HomePresetId,
  type PlaceItem,
  type PlaceKind,
  type Snapshot,
} from "./presets";

export type LayoutPrefs = {
  hidden?: readonly string[];
  layouts?: Partial<Record<string, {
    frames?: Partial<Record<string, FrameBox>>;
    attach?: Partial<Record<string, Attach | null>>;
  }>>;
};

export type ResolvedItem = {
  id: PartId;
  kind: PartKind;
  presentation: string;
  area?: string;
  grow?: boolean;
  pinEnd?: boolean;
  frame?: ResolvedFrame;
  attach?: Attach;
};

export type FoldSide = "start" | "end";

export type AssemblyFold = {
  name: string;
  side: FoldSide;
  folded: boolean;
};

export type ResolvedGrid = {
  pad: number;
  gap: number;
  rowGap: number;
  columnGap: number;
  ground?: "desk";
  justify: "stretch" | "center";
  align: "stretch" | "center";
  columns: string;
  rows: string;
  areas: string;
  stacks: Record<string, string[]>;
  fold: AssemblyFold[];
  bubbleMax?: string;
};

export type Assembly = {
  preset: HomePresetId;
  place: PlaceKind;
  items: ResolvedItem[];
  grid?: ResolvedGrid;
};

export const FOLD_HANDLE = { width: 24, height: 24 };

/** 折叠把手浮在舞台左/右上角，视觉降权由 HomeFrame 的 .fold-handle 负责。 */
export function foldHandleStyle(
  fold: AssemblyFold,
  stage: StageSize,
): Record<string, string> {
  const { width, height } = FOLD_HANDLE;
  const left = fold.side === "start" ? SNAP_GAP : stage.width - SNAP_GAP - width;
  return {
    position: "absolute",
    left: `${left}px`,
    top: `${SNAP_GAP}px`,
    width: `${width}px`,
    height: `${height}px`,
    zIndex: "20",
  };
}

export function resolveFrame(box: FrameBox, stage: StageSize): ResolvedFrame {
  const width = box.width ?? Math.max(0, stage.width - (box.left ?? 0) - (box.right ?? 0));
  const height = box.height ?? Math.max(0, stage.height - (box.top ?? 0) - (box.bottom ?? 0));
  const left = box.left ?? (box.right !== undefined ? stage.width - box.right - width : 0);
  const top = box.top ?? (box.bottom !== undefined ? stage.height - box.bottom - height : 0);
  return { left, top, width, height, z: box.z ?? 1 };
}

function attachFrame(host: ResolvedFrame, spec: Attach): ResolvedFrame {
  const gap = spec.gap ?? 0;
  const width = spec.width ?? host.height;
  const height = spec.height ?? host.height;
  if (spec.edge === "start") {
    return { left: host.left - gap - width, top: host.top, width, height, z: (host.z ?? 1) + 1 };
  }
  if (spec.edge === "end") {
    return { left: host.left + host.width + gap, top: host.top, width, height, z: (host.z ?? 1) + 1 };
  }
  if (spec.edge === "top") {
    return { left: host.left, top: host.top - gap - height, width: spec.width ?? host.width, height, z: (host.z ?? 1) + 1 };
  }
  return { left: host.left, top: host.top + host.height + gap, width: spec.width ?? host.width, height, z: (host.z ?? 1) + 1 };
}

function defaultPresentation(id: PartId, placed?: string): string {
  if (placed) return placed;
  return getPartMeta(id)?.presentations[0] ?? "tile";
}

/** 断点快照选择：自最紧档向下找第一个有定义的档，都没有则回 preset 本体。
 * 降级顺序（design §8）：<1280 narrow（器物收）<1100 slim（今日收条）<960 compact。 */
function snapshotOf(preset: HomePreset, compact: boolean, slim?: boolean, narrow?: boolean): Snapshot {
  if (compact && preset.compact) return preset.compact;
  if (slim && preset.slim) return preset.slim;
  if (narrow && preset.narrow) return preset.narrow;
  return preset;
}

function packNavAbovePlugins(items: ResolvedItem[]): void {
  const plugins = items.filter((item) => isPluginPart(item.id) && item.frame);
  if (!plugins.length) return;
  const ceiling = Math.min(...plugins.map((item) => item.frame!.top)) - SNAP_GAP;
  for (const item of items) {
    if (item.kind !== "nav" || !item.frame) continue;
    const box = item.frame;
    const sharesColumn = plugins.some((plugin) => {
      const frame = plugin.frame!;
      return box.left < frame.left + frame.width && box.left + box.width > frame.left;
    });
    if (!sharesColumn || box.top + box.height <= ceiling || ceiling <= box.top) continue;
    item.frame = { ...box, height: ceiling - box.top };
  }
}

export function collapsibleOf(presetId: HomePresetId): readonly string[] {
  const preset: HomePreset = HOME_PRESETS[presetId];
  const grid = preset.grid;
  if (!grid) return [];
  if (grid.tracks) return grid.tracks.filter((track) => track.fold).map((track) => track.area);
  return grid.fold ?? [];
}

/** 可折叠区域 → 侧别（start=起始侧，end=结束侧）。tracks 按位置均分；columns/areas 按列位置判定。 */
export function collapsibleSidesOf(presetId: HomePresetId): Record<string, FoldSide> {
  const preset: HomePreset = HOME_PRESETS[presetId];
  const grid = preset.grid;
  if (!grid) return {};
  if (grid.tracks) {
    const tracks = grid.tracks.filter((track) => track.fold);
    const mid = tracks.length / 2;
    const out: Record<string, FoldSide> = {};
    tracks.forEach((track, index) => {
      out[track.area] = index < mid ? "start" : "end";
    });
    return out;
  }
  const cols = (grid.columns ?? "").split(" ").filter(Boolean).length;
  const row0 = parseAreas(grid.areas)[0] ?? [];
  const out: Record<string, FoldSide> = {};
  for (const name of grid.fold ?? []) {
    const idx = row0.indexOf(name);
    out[name] = idx >= 0 && cols > 0 && idx >= cols / 2 ? "end" : "start";
  }
  return out;
}

export function groupOf(presetId: HomePresetId, name: string): readonly string[] {
  const grid = (HOME_PRESETS[presetId] as HomePreset).grid;
  return grid?.stacks[name] ?? [];
}

function presentIds(
  candidate: Iterable<string>,
  hidden: Set<string>,
  absent: Set<string>,
  overlayFrames?: Partial<Record<string, FrameBox>>,
): Set<string> {
  const present = new Set<string>();
  for (const id of candidate) {
    if (!isPartId(id) || hidden.has(id)) continue;
    if (absent.has(id) && !overlayFrames?.[id]) continue;
    present.add(id);
  }
  return present;
}

/** 解析 areas 字符串（`"a b c" "d . f"`）为行 × 单元格的二维数组。 */
function parseAreas(areas?: string): string[][] {
  if (!areas) return [];
  const rows: string[][] = [];
  for (const m of areas.matchAll(/"([^"]*)"/g)) {
    const cells = m[1].trim().split(/\s+/).filter(Boolean);
    if (cells.length) rows.push(cells);
  }
  return rows;
}

/** 按顶层空格切分 track 串：minmax(0, 1fr) 内部的空格不是分隔符（foldColumnAreas 存量 bug 根因）。 */
function splitTracks(s: string): string[] {
  const out: string[] = [];
  let depth = 0;
  let cur = "";
  for (const ch of s) {
    if (ch === "(") depth += 1;
    else if (ch === ")") depth = Math.max(0, depth - 1);
    if (depth === 0 && /\s/.test(ch)) {
      if (cur) out.push(cur);
      cur = "";
    } else {
      cur += ch;
    }
  }
  if (cur) out.push(cur);
  return out;
}

/** 从 columns/areas 字符串预设中移除已折叠的区域列（连同相邻纯间隙列，避免留下双空隙）。 */
function foldColumnAreas(
  src: NonNullable<Snapshot["grid"]>,
  folded: ReadonlySet<string>,
): { columns?: string; areas?: string } {
  const cols = splitTracks(src.columns ?? "");
  const rows = parseAreas(src.areas);
  if (!cols.length || !rows.length) return {};
  let removed = false;
  for (const name of folded) {
    const idx = rows[0].indexOf(name);
    if (idx < 0) continue;
    const pureGap = (i: number) => rows.every((r) => r[i] === ".");
    const del = [idx];
    if (pureGap(idx + 1)) del.push(idx + 1);
    else if (pureGap(idx - 1)) del.push(idx - 1);
    for (const i of del.sort((a, b) => b - a)) {
      cols.splice(i, 1);
      for (const row of rows) row.splice(i, 1);
    }
    removed = true;
  }
  if (!removed) return {};
  return {
    columns: cols.join(" "),
    areas: rows.map((row) => `"${row.join(" ")}"`).join(" "),
  };
}

function makeItem(
  id: string,
  presentations: Record<string, string>,
  extra?: Partial<ResolvedItem>,
): ResolvedItem | null {
  const meta = getPartMeta(id);
  if (!meta) return null;
  return {
    id,
    kind: meta.kind,
    presentation: defaultPresentation(id, presentations[id]),
    ...extra,
  };
}

function resolveGrid(
  presetId: HomePresetId,
  snap: Snapshot,
  presentations: Record<string, string>,
  present: Set<string>,
  collapsed: readonly string[],
): Assembly {
  const src = snap.grid;
  if (!src) {
    return { preset: presetId, place: "grid", items: [] };
  }
  const folded = new Set(collapsed.filter((name) => collapsibleOf(presetId).includes(name)));
  const stacks: Record<string, string[]> = {};
  for (const [area, ids] of Object.entries(src.stacks)) {
    stacks[area] = ids.filter((id) => present.has(id));
  }
  // 插件卡不再自动塞进预设列（sidecar 的 widget 由 HomeChat 的独立 plugin-well 槽渲染）
  const visibleStacks: Record<string, string[]> = {};
  for (const [area, ids] of Object.entries(stacks)) {
    if (folded.has(area)) continue;
    visibleStacks[area] = ids;
  }
  const grow = new Set(src.grow ?? []);
  const pinEnd = new Set(src.pinEnd ?? []);
  const items: ResolvedItem[] = [];
  for (const [area, ids] of Object.entries(visibleStacks)) {
    for (const id of ids) {
      const item = makeItem(id, presentations, {
        area,
        grow: grow.has(id),
        pinEnd: pinEnd.has(id),
      });
      if (item) items.push(item);
    }
  }
  const tracks = src.tracks?.filter((track) => !folded.has(track.area));
  const fold: AssemblyFold[] = (src.tracks ?? [])
    .filter((track) => track.fold)
    .map((track, index, all) => ({
      name: track.area,
      side: index < all.length / 2 ? "start" as const : "end" as const,
      folded: folded.has(track.area),
    }));
  if (src.tracks) {
    for (const track of src.tracks) {
      if (!track.fold) continue;
      const side: FoldSide = src.tracks.indexOf(track) === 0 ? "start" : "end";
      const i = fold.findIndex((row) => row.name === track.area);
      if (i >= 0) fold[i] = { ...fold[i], side };
    }
  }
  // 非 tracks 预设（columns/areas 字符串，如 desk 的 note 便条）：把折叠区域的列移出网格
  const foldedAreas = !src.tracks && folded.size > 0 ? foldColumnAreas(src, folded) : {};
  const grid: ResolvedGrid = {
    pad: src.pad ?? 8,
    gap: src.gap ?? 8,
    rowGap: src.rowGap ?? src.gap ?? 8,
    columnGap: src.columnGap ?? src.gap ?? 8,
    ground: src.ground,
    justify: src.justify ?? "stretch",
    align: src.align ?? "stretch",
    columns: tracks ? tracks.map((track) => track.size).join(" ") : (foldedAreas.columns ?? src.columns ?? "minmax(0,1fr)"),
    rows: tracks ? "minmax(0,1fr)" : (src.rows ?? "minmax(0,1fr)"),
    areas: tracks ? `"${tracks.map((track) => track.area).join(" ")}"` : (foldedAreas.areas ?? src.areas ?? "."),
    stacks: visibleStacks,
    fold,
    bubbleMax: src.bubbleMax,
  };
  return { preset: presetId, place: "grid", items, grid };
}

export function gridStageStyle(grid: ResolvedGrid): Record<string, string> {
  return {
    display: "grid",
    gridTemplateColumns: grid.columns,
    gridTemplateRows: grid.rows,
    gridTemplateAreas: grid.areas,
    gap: `${grid.rowGap}px ${grid.columnGap}px`,
    padding: `${grid.pad}px`,
    justifyContent: grid.justify,
    alignContent: grid.align,
    ...(grid.bubbleMax ? { "--yb-bubble-max": grid.bubbleMax } : {}),
    minWidth: "0",
    minHeight: "0",
    height: "100%",
    width: "100%",
  };
}

export function resolveAssembly(
  presetId: HomePresetId,
  prefs: LayoutPrefs,
  opts?: {
    compact?: boolean;
    slim?: boolean;
    narrow?: boolean;
    extra?: readonly PlaceItem[];
    stage?: StageSize;
    collapsed?: readonly string[];
    pluginIds?: readonly PartId[];
  },
): Assembly {
  const preset: HomePreset = HOME_PRESETS[presetId];
  const snap = snapshotOf(preset, opts?.compact === true, opts?.slim === true, opts?.narrow === true);
  const stage = opts?.stage ?? DEFAULT_STAGE;
  const pluginIds = opts?.pluginIds ?? [];
  const hidden = new Set<string>(prefs.hidden ?? []);
  const overlay = prefs.layouts?.[presetId];
  const absent = new Set(snap.absent ?? []);
  const presentations = { ...(preset.presentations as Record<string, string>), ...(snap.presentations ?? {}) };
  const place: PlaceKind = snap.grid ? "grid" : "canvas";

  if (place === "grid") {
    const candidate = new Set<string>();
    for (const ids of Object.values(snap.grid?.stacks ?? {})) {
      for (const id of ids) candidate.add(id);
    }
    for (const id of pluginIds) candidate.add(id);
    for (const row of opts?.extra ?? []) candidate.add(row.id);
    const present = presentIds(candidate, hidden, absent, overlay?.frames);
    return resolveGrid(presetId, snap, presentations, present, opts?.collapsed ?? []);
  }

  const frames: Record<string, FrameBox> = { ...(snap.frames ?? {}) };
  const attach: Record<string, Attach | null> = { ...(snap.attach ?? {}) };
  if (overlay?.frames) {
    for (const [id, box] of Object.entries(overlay.frames)) {
      if (box) frames[id] = { ...frames[id], ...box };
    }
  }
  if (overlay?.attach) {
    for (const [id, spec] of Object.entries(overlay.attach)) {
      if (spec !== undefined) attach[id] = spec;
    }
  }
  for (const row of opts?.extra ?? []) {
    if (row.frame) frames[row.id] = row.frame;
    if (row.attach !== undefined) attach[row.id] = row.attach;
    if (row.presentation) presentations[row.id] = row.presentation;
  }

  const ids = new Set([...Object.keys(frames), ...Object.keys(attach)]);
  if (snap.pluginFrame) {
    for (const id of pluginIds) ids.add(id);
  }
  const present = presentIds(ids, hidden, absent, overlay?.frames);

  const items: ResolvedItem[] = [];
  const pendingAttach: { id: string; spec: Attach }[] = [];

  for (const id of present) {
    const spec = attach[id];
    if (spec && spec.to && present.has(spec.to)) {
      pendingAttach.push({ id, spec });
      continue;
    }
    const box = frames[id];
    if (!box) continue;
    const item = makeItem(id, presentations, { frame: resolveFrame(box, stage) });
    if (item) items.push(item);
  }

  if (snap.pluginFrame) {
    const origin = resolveFrame(snap.pluginFrame, stage);
    const step = origin.height + SNAP_GAP;
    const stackUp = snap.pluginFrame.bottom !== undefined && snap.pluginFrame.top === undefined;
    let i = 0;
    for (const id of pluginIds) {
      if (!present.has(id) || items.some((item) => item.id === id)) continue;
      const overlayBox = overlay?.frames?.[id];
      const frame = overlayBox
        ? resolveFrame({ ...snap.pluginFrame, ...overlayBox }, stage)
        : { ...origin, top: stackUp ? origin.top - i * step : origin.top + i * step, z: origin.z + i };
      const item = makeItem(id, presentations, { frame });
      if (item) items.push(item);
      i += 1;
    }
    packNavAbovePlugins(items);
  }

  const byId = new Map(items.map((item) => [item.id, item]));
  for (const { id, spec } of pendingAttach) {
    const host = byId.get(spec.to);
    if (!host?.frame) continue;
    const item = makeItem(id, presentations, { frame: attachFrame(host.frame, spec), attach: spec });
    if (!item) continue;
    items.push(item);
    byId.set(id, item);
  }

  items.sort((a, b) => (a.frame?.z ?? 1) - (b.frame?.z ?? 1));
  return { preset: presetId, place: "canvas", items };
}

export function presentationOf(assembly: Assembly, id: PartId): string | undefined {
  return assembly.items.find((i) => i.id === id)?.presentation;
}

export function faceOf(assembly: Assembly, id: PartId, fallback = ""): string {
  return presentationOf(assembly, id) ?? fallback;
}

export function spineLimitOf(_assembly: Assembly): number {
  return 0;
}

export function itemOf(assembly: Assembly, id: PartId): ResolvedItem | undefined {
  return assembly.items.find((i) => i.id === id);
}

export function docksOf(assembly: Assembly, host: PartId, edge: DockEdge): ResolvedItem[] {
  return assembly.items.filter((i) => i.attach?.to === host && i.attach.edge === edge);
}

export function isPlaced(assembly: Assembly, id: string): boolean {
  return assembly.items.some((i) => i.id === id);
}

/** 本次独立在场则打开；贴在别人边上则先收起。 */
export function defaultPeek(assembly: Assembly): boolean {
  const now = itemOf(assembly, "now");
  if (!now) return false;
  return !now.attach;
}

export function frameStyle(frame: ResolvedFrame): Record<string, string> {
  return {
    position: "absolute",
    left: `${frame.left}px`,
    top: `${frame.top}px`,
    width: `${frame.width}px`,
    height: `${frame.height}px`,
    zIndex: String(frame.z),
  };
}
