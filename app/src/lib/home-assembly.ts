/** 主屏装配：零件目录 + 摊法 + 两套落点（grid / canvas）。与 theme / finish 正交。 */
import { ref } from "vue";

export type PartKind = "work" | "input" | "nav" | "context" | "glance";
export type DockEdge = "start" | "end" | "top" | "bottom";
export type PartSource = "core" | "plugin";

export const HOME_PARTS = [
  { id: "chat", kind: "work" as const, presentations: ["thread", "paper", "talk"] as const },
  { id: "composer", kind: "input" as const, presentations: ["bar"] as const },
  { id: "sessions", kind: "nav" as const, presentations: ["list", "spine", "cards"] as const },
  { id: "now", kind: "context" as const, presentations: ["inspector", "note"] as const },
  { id: "identity", kind: "glance" as const, presentations: ["tile", "seat"] as const },
  { id: "mind", kind: "glance" as const, presentations: ["map", "tile"] as const },
  { id: "today", kind: "glance" as const, presentations: ["tile"] as const },
  { id: "need", kind: "glance" as const, presentations: ["tile"] as const },
  { id: "tasks", kind: "glance" as const, presentations: ["tile"] as const },
  { id: "remind", kind: "glance" as const, presentations: ["tile"] as const },
  { id: "spark", kind: "glance" as const, presentations: ["tile"] as const },
  { id: "glimpse", kind: "glance" as const, presentations: ["tile"] as const },
  { id: "catch", kind: "glance" as const, presentations: ["tile"] as const },
  { id: "scratch", kind: "glance" as const, presentations: ["tile"] as const },
] as const;

export type CorePartId = (typeof HOME_PARTS)[number]["id"];
export type PartId = string;
export type PartMeta = {
  id: PartId;
  kind: PartKind;
  presentations: readonly string[];
  source: PartSource;
};

const PARTS = new Map<string, PartMeta>(
  HOME_PARTS.map((p) => [p.id, { id: p.id, kind: p.kind, presentations: p.presentations, source: "core" }]),
);

export function isPartId(v: unknown): v is PartId {
  return typeof v === "string" && PARTS.has(v);
}

export function pluginPartId(panel: string): PartId {
  return `plugin:${panel}`;
}

export function isPluginPart(id: string): boolean {
  return id.startsWith("plugin:");
}

export function registerPart(meta: Omit<PartMeta, "source"> & { source?: PartSource }): void {
  if (PARTS.get(meta.id)?.source === "core" && meta.source === "plugin") return;
  PARTS.set(meta.id, { ...meta, source: meta.source ?? "core" });
}

export const livePluginIds = ref<PartId[]>([]);

export function syncPluginParts(widgets: readonly { panel: string }[]): PartId[] {
  const keep = new Set<PartId>();
  for (const widget of widgets) {
    if (!widget.panel) continue;
    const id = pluginPartId(widget.panel);
    keep.add(id);
    registerPart({ id, kind: "glance", presentations: ["tile"], source: "plugin" });
  }
  for (const [id, meta] of [...PARTS]) {
    if (meta.source === "plugin" && !keep.has(id)) PARTS.delete(id);
  }
  livePluginIds.value = [...keep];
  return livePluginIds.value;
}

export function resetPluginParts(): void {
  for (const [id, meta] of [...PARTS]) {
    if (meta.source === "plugin") PARTS.delete(id);
  }
  livePluginIds.value = [];
}

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

export type HomeDrag = {
  id: string;
  frame: ResolvedFrame;
  xs: number[];
  ys: number[];
};

export type Attach = { to: PartId; edge: DockEdge; gap?: number; width?: number; height?: number };
export type PlaceKind = "grid" | "canvas";

export type GridTrack = { area: string; size: string; fold?: boolean };

export type GridTemplate = {
  pad?: number;
  gap?: number;
  rowGap?: number;
  columnGap?: number;
  ground?: "desk";
  justify?: "stretch" | "center";
  align?: "stretch" | "center";
  tracks?: readonly GridTrack[];
  columns?: string;
  rows?: string;
  areas?: string;
  stacks: Readonly<Record<string, readonly string[]>>;
  grow?: readonly string[];
  pinEnd?: readonly string[];
  pluginArea?: string;
};

export type Snapshot = {
  presentations?: Readonly<Record<string, string>>;
  absent?: readonly string[];
  grid?: GridTemplate;
  frames?: Readonly<Record<string, FrameBox>>;
  attach?: Readonly<Record<string, Attach>>;
  pluginFrame?: FrameBox;
};

export type HomePreset = {
  id: string;
  label: string;
  hint: string;
  place: PlaceKind;
} & Snapshot & { compact?: Snapshot };

export type PlaceItem = {
  id: PartId;
  frame?: FrameBox;
  attach?: Attach | null;
  presentation?: string;
};

const SPINE_W = "28px";
const NOTE_W = "176px";
const TILE = "156px";
const RAIL = "264px";

export const HOME_PRESETS = {
  rails: {
    id: "rails",
    label: "三栏",
    hint: "左零件、中对话、右本次。缺省。",
    place: "grid" as const,
    presentations: {
      chat: "thread",
      sessions: "list",
      now: "inspector",
      mind: "map",
      identity: "tile",
      composer: "bar",
      today: "tile",
      need: "tile",
      tasks: "tile",
      remind: "tile",
      spark: "tile",
      glimpse: "tile",
      catch: "tile",
    },
    absent: ["scratch"],
    grid: {
      pad: 8,
      gap: 8,
      tracks: [
        { area: "left", size: RAIL, fold: true },
        { area: "main", size: "minmax(0,1fr)" },
        { area: "right", size: RAIL, fold: true },
      ],
      stacks: {
        // 三栏左栏：保留核心可视化（脑图/今日/余光）+ 会话；去掉信息卡（提醒/动态/需要/刚复制），会话列表才有空间
        left: ["identity", "spark", "mind", "today", "sessions"],
        main: ["chat", "composer"],
        right: ["now"],
      },
      grow: ["sessions", "chat"],
      pluginArea: "left",
    },
  },
  desk: {
    id: "desk",
    label: "整桌",
    hint: "整窗是桌，纸是工作面。",
    place: "grid" as const,
    presentations: {
      chat: "paper",
      sessions: "spine",
      now: "note",
      mind: "tile",
      identity: "tile",
      composer: "bar",
      today: "tile",
      need: "tile",
      tasks: "tile",
      remind: "tile",
      spark: "tile",
      glimpse: "tile",
      catch: "tile",
      scratch: "tile",
    },
    grid: {
      pad: 12,
      rowGap: 12,
      columnGap: 0,
      ground: "desk",
      columns: `${TILE} 12px ${SPINE_W} minmax(0,1fr) 12px ${NOTE_W} 12px ${TILE}`,
      rows: "minmax(0,1fr) minmax(min-content, auto)",
      areas: `"start . spine paper . note . end" "start . . compose . . . end"`,
      stacks: {
        start: ["mind", "need", "spark", "glimpse", "identity"],
        spine: ["sessions"],
        paper: ["chat"],
        note: ["now"],
        end: ["today", "tasks", "remind", "catch", "scratch"],
        compose: ["composer"],
      },
      grow: ["chat"],
      pinEnd: ["identity"],
      pluginArea: "start",
    },
    compact: {
      presentations: { chat: "paper", composer: "bar", sessions: "spine" },
      grid: {
        pad: 8,
        rowGap: 8,
        columnGap: 0,
        ground: "desk",
        columns: `${SPINE_W} minmax(0,1fr)`,
        rows: "minmax(0,1fr) minmax(min-content, auto)",
        areas: `"spine paper" ". compose"`,
        stacks: {
          spine: ["sessions"],
          paper: ["chat"],
          compose: ["composer"],
        },
        grow: ["chat"],
      },
    },
  },
  salon: {
    id: "salon",
    label: "会客",
    hint: "译宝坐在屋里，只摊刚说的几句。",
    place: "grid" as const,
    presentations: {
      chat: "talk",
      identity: "seat",
      sessions: "cards",
      composer: "bar",
      mind: "tile",
      today: "tile",
      need: "tile",
      remind: "tile",
      spark: "tile",
    },
    absent: ["now", "tasks", "glimpse", "catch", "scratch"],
    grid: {
      pad: 24,
      gap: 12,
      justify: "center",
      align: "center",
      columns: "148px 148px 148px 148px",
      rows: "auto auto auto auto",
      areas: `"mind need today remind" "identity chat chat chat" ". sessions sessions sessions" ". composer composer composer"`,
      stacks: {
        mind: ["mind"],
        need: ["need"],
        today: ["today"],
        remind: ["remind"],
        identity: ["identity", "spark"],
        chat: ["chat"],
        sessions: ["sessions"],
        composer: ["composer"],
      },
    },
    compact: {
      presentations: { chat: "talk", composer: "bar" },
      grid: {
        pad: 16,
        gap: 12,
        columns: "minmax(0,1fr)",
        rows: "minmax(160px,1fr) 48px",
        areas: `"chat" "composer"`,
        stacks: { chat: ["chat"], composer: ["composer"] },
        grow: ["chat"],
      },
    },
  },
  canvas: {
    id: "canvas",
    label: "画布",
    hint: "自己摆零件。",
    place: "canvas" as const,
    presentations: {
      chat: "thread",
      sessions: "list",
      now: "inspector",
      mind: "map",
      identity: "tile",
      composer: "bar",
      today: "tile",
      need: "tile",
      tasks: "tile",
      remind: "tile",
      spark: "tile",
      glimpse: "tile",
      catch: "tile",
      scratch: "tile",
    },
    frames: {
      mind: { left: 40, top: 40, width: 200, height: 176, z: 1 },
      spark: { left: 72, top: 228, width: 168, height: 92, z: 3 },
      need: { left: 256, top: 40, width: 180, height: 108, z: 1 },
      identity: { left: 40, top: 332, width: 200, height: 120, z: 2 },
      today: { left: 256, top: 164, width: 180, height: 72, z: 1 },
      tasks: { left: 256, top: 252, width: 180, height: 88, z: 1 },
      glimpse: { left: 256, top: 356, width: 180, height: 72, z: 1 },
      remind: { left: 40, top: 468, width: 200, height: 80, z: 1 },
      catch: { left: 256, top: 444, width: 180, height: 88, z: 1 },
      scratch: { left: 72, top: 564, width: 200, height: 120, z: 2 },
      chat: { left: 460, top: 40, width: 520, height: 400, z: 1 },
      composer: { left: 460, top: 456, width: 520, height: 72, z: 3 },
      sessions: { left: 1000, top: 40, width: 220, height: 280, z: 1 },
      now: { left: 1000, top: 336, width: 220, height: 192, z: 1 },
    },
    pluginFrame: { left: 1000, top: 548, width: 220, height: 48, z: 2 },
    compact: {
      presentations: { chat: "thread", composer: "bar" },
      frames: {
        chat: { left: 16, top: 16, right: 16, bottom: 72, z: 1 },
        composer: { left: 16, right: 16, bottom: 16, height: 48, z: 2 },
      },
    },
  },
} as const satisfies Record<string, HomePreset>;

export type HomePresetId = keyof typeof HOME_PRESETS;
export const HOME_PRESET_DEFAULT: HomePresetId = "rails";
export const HOME_PRESET_LIST: HomePreset[] = [
  HOME_PRESETS.rails,
  HOME_PRESETS.desk,
  HOME_PRESETS.salon,
  HOME_PRESETS.canvas,
];

export function isHomePresetId(v: string | null): v is HomePresetId {
  return !!v && v in HOME_PRESETS;
}

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
};

export type Assembly = {
  preset: HomePresetId;
  place: PlaceKind;
  items: ResolvedItem[];
  grid?: ResolvedGrid;
};

export const FOLD_HANDLE = { width: 24, height: 24 };

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
    zIndex: "12",
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
  return PARTS.get(id)?.presentations[0] ?? "tile";
}

function snapshotOf(preset: HomePreset, compact: boolean): Snapshot {
  if (compact && preset.compact) return preset.compact;
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
  return [];
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

function makeItem(
  id: string,
  presentations: Record<string, string>,
  extra?: Partial<ResolvedItem>,
): ResolvedItem | null {
  const meta = PARTS.get(id);
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
  const grid: ResolvedGrid = {
    pad: src.pad ?? 8,
    gap: src.gap ?? 8,
    rowGap: src.rowGap ?? src.gap ?? 8,
    columnGap: src.columnGap ?? src.gap ?? 8,
    ground: src.ground,
    justify: src.justify ?? "stretch",
    align: src.align ?? "stretch",
    columns: tracks ? tracks.map((track) => track.size).join(" ") : (src.columns ?? "minmax(0,1fr)"),
    rows: tracks ? "minmax(0,1fr)" : (src.rows ?? "minmax(0,1fr)"),
    areas: tracks ? `"${tracks.map((track) => track.area).join(" ")}"` : (src.areas ?? "."),
    stacks: visibleStacks,
    fold,
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
    minWidth: "0",
    minHeight: "0",
    height: "100%",
    width: "100%",
  };
}

export function resolveAssembly(
  presetId: HomePresetId,
  prefs: LayoutPrefs,
  opts?: { compact?: boolean; extra?: readonly PlaceItem[]; stage?: StageSize; collapsed?: readonly string[] },
): Assembly {
  const preset: HomePreset = HOME_PRESETS[presetId];
  const snap = snapshotOf(preset, opts?.compact === true);
  const stage = opts?.stage ?? DEFAULT_STAGE;
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
    for (const id of livePluginIds.value) candidate.add(id);
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
    for (const id of livePluginIds.value) ids.add(id);
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
    for (const id of livePluginIds.value) {
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

export function spineLimitOf(assembly: Assembly): number {
  return faceOf(assembly, "sessions") === "spine" ? 4 : 0;
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
