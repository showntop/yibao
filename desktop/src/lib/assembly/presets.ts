/** 主屏装配预设：4 套摊法（rails/desk/salon/canvas）+ 几何常量（纯数据）。 */
import type { DockEdge, PartId } from "./parts";
import type { FrameBox, ResolvedFrame } from "./snap";

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

const SPINE_W = "40px";
const NOTE_W = "188px";
const TILE = "164px";
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
      when: "tile",
      line: "tile",
      jot: "tile",
      bench: "tile",
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
      when: "tile",
      line: "tile",
      jot: "tile",
      bench: "tile",
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
      areas: `"start . spine paper . note . end" "ident . . compose . . . ."`,
      stacks: {
        start: ["mind", "when", "line", "jot", "bench", "spark", "glimpse", "need", "tasks", "catch"],
        spine: ["sessions"],
        paper: ["chat"],
        note: ["now"],
        compose: ["composer"],
        ident: ["identity"],
        end: ["today", "remind", "scratch"],
      },
      grow: ["chat", "now", "scratch", "sessions"],
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
        grow: ["chat", "sessions"],
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
      when: "tile",
      line: "tile",
      jot: "tile",
      bench: "tile",
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
      when: { left: 40, top: 228, width: 200, height: 80, z: 1 },
      line: { left: 40, top: 320, width: 200, height: 108, z: 1 },
      jot: { left: 40, top: 440, width: 200, height: 88, z: 1 },
      bench: { left: 40, top: 540, width: 200, height: 88, z: 1 },
      spark: { left: 72, top: 320, width: 168, height: 92, z: 3 },
      need: { left: 256, top: 40, width: 180, height: 108, z: 1 },
      identity: { left: 40, top: 428, width: 200, height: 120, z: 2 },
      today: { left: 256, top: 164, width: 180, height: 72, z: 1 },
      tasks: { left: 256, top: 252, width: 180, height: 88, z: 1 },
      glimpse: { left: 256, top: 356, width: 180, height: 72, z: 1 },
      remind: { left: 40, top: 564, width: 200, height: 80, z: 1 },
      catch: { left: 256, top: 444, width: 180, height: 88, z: 1 },
      scratch: { left: 72, top: 660, width: 200, height: 120, z: 2 },
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
