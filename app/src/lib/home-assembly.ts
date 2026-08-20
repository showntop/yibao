/** 主屏装配：零件目录 + 预设格子图 + 合成。与 theme / finish 正交。 */
import { ref } from "vue";
import { HOME_WIDGETS } from "./home-widgets";

export type PartKind = "work" | "input" | "nav" | "context" | "glance";
export type DockEdge = "start" | "end";
export type PartSource = "core" | "plugin";

export const HOME_PARTS = [
  { id: "chat", kind: "work" as const, presentations: ["thread", "paper"] as const },
  { id: "composer", kind: "input" as const, presentations: ["bar"] as const },
  { id: "sessions", kind: "nav" as const, presentations: ["list", "spine"] as const },
  { id: "now", kind: "context" as const, presentations: ["inspector", "note"] as const },
  { id: "identity", kind: "glance" as const, presentations: ["tile"] as const },
  { id: "mind", kind: "glance" as const, presentations: ["map", "tile"] as const },
  { id: "today", kind: "glance" as const, presentations: ["tile"] as const },
  { id: "need", kind: "glance" as const, presentations: ["tile"] as const },
  { id: "tasks", kind: "glance" as const, presentations: ["tile"] as const },
  { id: "remind", kind: "glance" as const, presentations: ["tile"] as const },
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

export type PlaceItem = {
  id: PartId;
  region?: string;
  dock?: { to: PartId; edge: DockEdge };
  presentation?: string;
};

export type PresetGrid = {
  columns: string;
  rows: string;
  areas: readonly string[];
};

export type HomePreset = {
  id: string;
  label: string;
  hint: string;
  grid: PresetGrid;
  compactGrid?: PresetGrid;
  place: readonly PlaceItem[];
  collapsible: readonly string[];
  /** 插件 glance 进这个区；区不在格子图里则跳过。 */
  pluginRegion?: string;
};

export const HOME_PRESETS = {
  rails: {
    id: "rails",
    label: "三栏",
    hint: "左零件、中对话、右本次。缺省。",
    grid: {
      columns: "280px minmax(0, 1fr) 280px",
      rows: "minmax(0, 1fr)",
      areas: ["left main right"],
    },
    place: [
      { id: "identity", region: "left" },
      { id: "mind", region: "left", presentation: "map" },
      { id: "today", region: "left" },
      { id: "sessions", region: "left", presentation: "list" },
      { id: "chat", region: "main", presentation: "thread" },
      { id: "composer", region: "main" },
      { id: "now", region: "right", presentation: "inspector" },
    ],
    collapsible: ["left", "right"],
    pluginRegion: "left",
  },
  desk: {
    id: "desk",
    label: "整桌",
    hint: "整窗是桌，纸是工作面。",
    grid: {
      columns: "156px minmax(0, 1fr) 156px",
      rows: "auto auto minmax(0, 1fr) auto auto",
      areas: [
        "mind book today",
        "need book tasks",
        "plug book remind",
        "me book .",
        ". compose .",
      ],
    },
    compactGrid: {
      columns: "minmax(0, 1fr)",
      rows: "minmax(0, 1fr) auto",
      areas: ["book", "compose"],
    },
    place: [
      { id: "chat", region: "book", presentation: "paper" },
      { id: "sessions", dock: { to: "chat", edge: "start" }, presentation: "spine" },
      { id: "now", dock: { to: "chat", edge: "end" }, presentation: "note" },
      { id: "composer", region: "compose" },
      { id: "mind", region: "mind", presentation: "tile" },
      { id: "today", region: "today" },
      { id: "need", region: "need" },
      { id: "tasks", region: "tasks" },
      { id: "remind", region: "remind" },
      { id: "identity", region: "me" },
    ],
    collapsible: ["right"],
    pluginRegion: "plug",
  },
} as const satisfies Record<string, HomePreset>;

export type HomePresetId = keyof typeof HOME_PRESETS;
export const HOME_PRESET_DEFAULT: HomePresetId = "rails";
export const HOME_PRESET_LIST: HomePreset[] = [HOME_PRESETS.rails, HOME_PRESETS.desk];

export function isHomePresetId(v: string | null): v is HomePresetId {
  return !!v && v in HOME_PRESETS;
}

export type ResolvedItem = {
  id: PartId;
  kind: PartKind;
  presentation: string;
  region?: string;
  dock?: { to: PartId; edge: DockEdge };
};

export type Assembly = {
  preset: HomePresetId;
  grid: PresetGrid;
  items: ResolvedItem[];
};

export function quotedAreas(rows: readonly string[]): string {
  return rows.map((row) => `"${row}"`).join(" ");
}

export function regionSet(areas: readonly string[]): Set<string> {
  const names = new Set<string>();
  for (const row of areas) {
    for (const cell of row.split(/\s+/)) {
      if (cell && cell !== ".") names.add(cell);
    }
  }
  return names;
}

export function gridStyle(grid: PresetGrid): Record<string, string> {
  return {
    gridTemplateColumns: grid.columns,
    gridTemplateRows: grid.rows,
    gridTemplateAreas: quotedAreas(grid.areas),
  };
}

/** 拆 `156px minmax(0,1fr) 156px` 这种轨，按格子第一行区名对齐。 */
export function splitGridTracks(columns: string): string[] {
  const tracks: string[] = [];
  let depth = 0;
  let start = 0;
  for (let i = 0; i < columns.length; i += 1) {
    const ch = columns[i];
    if (ch === "(") depth += 1;
    else if (ch === ")") depth -= 1;
    else if ((ch === " " || ch === "\t") && depth === 0) {
      const track = columns.slice(start, i).trim();
      if (track) tracks.push(track);
      start = i + 1;
    }
  }
  const last = columns.slice(start).trim();
  if (last) tracks.push(last);
  return tracks;
}

export function collapseGridColumns(grid: PresetGrid, collapsed: ReadonlySet<string>): string {
  const names = grid.areas[0]?.split(/\s+/).filter(Boolean) ?? [];
  const tracks = splitGridTracks(grid.columns);
  return names.map((name, i) => (collapsed.has(name) ? "0px" : tracks[i] ?? "minmax(0, 1fr)")).join(" ");
}

function defaultPresentation(id: PartId, placed?: string): string {
  if (placed) return placed;
  return PARTS.get(id)?.presentations[0] ?? "tile";
}

function pluginPlaceItems(preset: HomePreset, regions: Set<string>): PlaceItem[] {
  const region = preset.pluginRegion;
  if (!region || !regions.has(region)) return [];
  return livePluginIds.value.map((id) => ({ id, region }));
}

export function collapsibleOf(presetId: HomePresetId): readonly string[] {
  return HOME_PRESETS[presetId].collapsible as readonly string[];
}

export function resolveAssembly(
  presetId: HomePresetId,
  prefs: { hidden: readonly string[]; order: readonly string[] },
  opts?: { compact?: boolean; extra?: readonly PlaceItem[] },
): Assembly {
  const preset = HOME_PRESETS[presetId];
  const compactGrid = "compactGrid" in preset ? preset.compactGrid : undefined;
  const grid = opts?.compact && compactGrid ? compactGrid : preset.grid;
  const regions = regionSet(grid.areas);
  const hidden = new Set<string>(prefs.hidden);
  const place: PlaceItem[] = [
    ...preset.place,
    ...pluginPlaceItems(preset, regions),
    ...(opts?.extra ?? []),
  ];

  const kept: PlaceItem[] = [];
  for (const row of place) {
    if (!isPartId(row.id) || hidden.has(row.id)) continue;
    if (row.region && !regions.has(row.region)) continue;
    kept.push(row);
  }

  const present = new Set(kept.map((r) => r.id));
  const items: ResolvedItem[] = [];
  for (const row of kept) {
    if (row.dock && (hidden.has(row.dock.to) || !present.has(row.dock.to))) continue;
    const meta = PARTS.get(row.id)!;
    items.push({
      id: row.id,
      kind: meta.kind,
      presentation: defaultPresentation(row.id, row.presentation),
      region: row.region,
      dock: row.dock,
    });
  }

  items.sort((a, b) => {
    if (a.region && a.region === b.region) {
      return catalogOrder(prefs, a.id) - catalogOrder(prefs, b.id);
    }
    return 0;
  });

  return { preset: presetId, grid, items };
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
  return assembly.items.filter((i) => i.dock?.to === host && i.dock.edge === edge);
}

export function itemsInRegion(assembly: Assembly, region: string): ResolvedItem[] {
  return assembly.items.filter((i) => i.region === region);
}

/** HOME_WIDGETS 序号；未入表的零件（chat/composer/plugin）排在后面。 */
export function catalogOrder(prefs: { order: readonly string[] }, id: string): number {
  const at = prefs.order.indexOf(id);
  if (at >= 0) return at;
  const catalog = HOME_WIDGETS.findIndex((w) => w.id === id);
  return catalog >= 0 ? catalog : 1000;
}

/**
 * 同一叠里的 flex `order`。会话宿主和插件卡都要走这里，
 * 不能只给 `.yb-widget` 设 --yb-widget-order：宿主不是瓷片，默认 order:0 会插到身份后面。
 * 插件跟在同区最后一个 glance 后面、会话前面。
 */
export function stackOrder(
  assembly: Assembly,
  prefs: { order: readonly string[] },
  id: PartId,
): number {
  if (!isPluginPart(id)) return catalogOrder(prefs, id);
  const region = itemOf(assembly, id)?.region;
  const glances = region
    ? itemsInRegion(assembly, region).filter((item) => item.kind === "glance" && !isPluginPart(item.id))
    : [];
  const after = glances.length
    ? Math.max(...glances.map((item) => catalogOrder(prefs, item.id)))
    : -1;
  return after + 1;
}

export function isPlaced(assembly: Assembly, id: string): boolean {
  return assembly.items.some((i) => i.id === id);
}

/** 本次在独立栏里则打开；贴在纸边则先收起。不要用 chat 摊法来猜。 */
export function defaultPeek(assembly: Assembly): boolean {
  const now = itemOf(assembly, "now");
  if (!now) return false;
  return Boolean(now.region) && !now.dock;
}
