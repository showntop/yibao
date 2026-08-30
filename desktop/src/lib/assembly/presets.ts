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
  /** columns/areas 字符串预设的可折叠区域名（如 desk 的 note 便条）。 */
  fold?: readonly string[];
  columns?: string;
  rows?: string;
  areas?: string;
  stacks: Readonly<Record<string, readonly string[]>>;
  grow?: readonly string[];
  pinEnd?: readonly string[];
  pluginArea?: string;
  /** 区内气泡行长上限（经 --yb-bubble-max 下传，缺省 720px）——"区宽呼吸，行长固定"（design §8） */
  bubbleMax?: string;
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
} & Snapshot & { compact?: Snapshot; slim?: Snapshot; narrow?: Snapshot };

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
      fold: ["note"],
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
  field: {
    // 第五套并行 preset「溪场」（design/2026-08-28 §4/§8/§10-P1）：家态——对话是溪，
    // 今日在轴，器物在架，历史收成细脊；地平线在壳级（HomeChat 的 HorizonBar），不进装配。
    id: "field",
    label: "溪场",
    hint: "对话是溪，今日在轴，器物在架。",
    place: "grid" as const,
    presentations: {
      chat: "thread",
      dayTitle: "title",
      composer: "bar",
      sessions: "spine",
      today: "panel",
      bench: "tile",
      jot: "tile",
      remind: "tile",
    },
    absent: ["now", "mind", "identity", "line", "need", "tasks", "glimpse", "catch", "scratch", "when", "spark"],
    grid: {
      pad: 20,
      gap: 16,
      rowGap: 12,
      ground: "desk",
      fold: ["spine", "axis"], // 细脊+今日轴都可折（验收发现#2：地平线今日/会话入口各管一列）
      // 区宽呼吸，行长固定：对话保底 420px（design §8），46:27:24 分配富余
      columns: `${SPINE_W} minmax(420px, 46fr) minmax(0, 27fr) minmax(0, 24fr)`,
      rows: "minmax(0,1fr) minmax(min-content, auto)",
      areas: `"spine chat axis shelf" ". compose . ."`,
      stacks: {
        spine: ["sessions"],
        chat: ["dayTitle", "chat"],
        axis: ["today"],
        shelf: ["remind", "bench", "jot"],
        compose: ["composer"],
      },
      grow: ["chat"],
      bubbleMax: "420px", // 行长 25–35 字（design §8）
    },
    // <1280 器物架收成地平线"器物"入口的 peek（design §8）：三区，轴保周条+提醒
    narrow: {
      presentations: {
        chat: "thread",
        dayTitle: "title",
        composer: "bar",
        sessions: "spine",
        today: "panel",
      },
      absent: ["now", "mind", "identity", "line", "need", "tasks", "glimpse", "catch", "scratch", "when", "spark", "bench", "jot", "remind"],
      grid: {
        pad: 16,
        gap: 14,
        rowGap: 12,
        ground: "desk",
        fold: ["spine", "axis"],
        columns: `${SPINE_W} minmax(420px, 58fr) minmax(0, 42fr)`,
        rows: "minmax(0,1fr) minmax(min-content, auto)",
        areas: `"spine chat axis" ". compose ."`,
        stacks: {
          spine: ["sessions"],
          chat: ["dayTitle", "chat"],
          axis: ["today"],
          compose: ["composer"],
        },
        grow: ["chat"],
        bubbleMax: "420px",
      },
    },
    // <1100 今日收成周视图条（axis 只留 when）：对话进一步吃宽
    slim: {
      presentations: {
        chat: "thread",
        dayTitle: "title",
        composer: "bar",
        sessions: "spine",
        today: "panel",
      },
      absent: ["now", "mind", "identity", "line", "need", "tasks", "glimpse", "catch", "scratch", "when", "spark", "bench", "jot", "remind"],
      grid: {
        pad: 14,
        gap: 12,
        rowGap: 12,
        ground: "desk",
        fold: ["spine", "axis"],
        columns: `${SPINE_W} minmax(420px, 70fr) minmax(200px, 30fr)`,
        rows: "minmax(0,1fr) minmax(min-content, auto)",
        areas: `"spine chat axis" ". compose ."`,
        stacks: {
          spine: ["sessions"],
          chat: ["dayTitle", "chat"],
          axis: ["today"],
          compose: ["composer"],
        },
        grow: ["chat"],
        bubbleMax: "420px",
      },
    },
    compact: {
      presentations: { chat: "thread", composer: "bar" },
      grid: {
        pad: 12,
        gap: 12,
        ground: "desk",
        columns: "minmax(0,1fr)",
        rows: "minmax(0,1fr) minmax(min-content, auto)",
        areas: `"chat" "compose"`,
        stacks: { chat: ["dayTitle", "chat"], compose: ["composer"] },
        grow: ["chat"],
        bubbleMax: "420px",
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
  HOME_PRESETS.field,
];

export function isHomePresetId(v: string | null): v is HomePresetId {
  return !!v && v in HOME_PRESETS;
}
