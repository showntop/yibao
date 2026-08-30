/** 主屏零件：显隐/大小/瓷玻璃。落槽由装配预设管，不在这里写左右栏。 */
import { reactive, watch } from "vue";

export const HOME_WIDGETS = [
  { id: "identity", label: "身份", defaultSize: "m", defaultMaterial: "porcelain" },
  { id: "mind", label: "认知", defaultSize: "l", defaultMaterial: "porcelain" },
  { id: "when", label: "此刻", defaultSize: "s", defaultMaterial: "porcelain" },
  { id: "line", label: "一句", defaultSize: "m", defaultMaterial: "porcelain" },
  { id: "jot", label: "闪念", defaultSize: "m", defaultMaterial: "porcelain" },
  { id: "bench", label: "工位", defaultSize: "m", defaultMaterial: "porcelain" },
  { id: "today", label: "今日", defaultSize: "s", defaultMaterial: "porcelain" },
  { id: "need", label: "需要你", defaultSize: "m", defaultMaterial: "porcelain" },
  { id: "tasks", label: "进行中", defaultSize: "m", defaultMaterial: "porcelain" },
  { id: "remind", label: "提醒", defaultSize: "s", defaultMaterial: "porcelain" },
  { id: "materials", label: "素材", defaultSize: "s", defaultMaterial: "porcelain" },
  { id: "flashes", label: "闪念数", defaultSize: "s", defaultMaterial: "porcelain" },
  { id: "spark", label: "想起", defaultSize: "m", defaultMaterial: "porcelain" },
  { id: "glimpse", label: "余光", defaultSize: "s", defaultMaterial: "glass" },
  { id: "catch", label: "接到", defaultSize: "m", defaultMaterial: "porcelain" },
  { id: "scratch", label: "草稿", defaultSize: "m", defaultMaterial: "porcelain" },
  { id: "sessions", label: "会话", defaultSize: "l", defaultMaterial: "porcelain" },
  { id: "now", label: "本次", defaultSize: "m", defaultMaterial: "porcelain" },
] as const;

export type WidgetId = (typeof HOME_WIDGETS)[number]["id"];
export type WidgetSize = "s" | "m" | "l";
export type WidgetMaterial = "porcelain" | "glass";

export const WIDGET_SIZES: { id: WidgetSize; label: string }[] = [
  { id: "s", label: "小" },
  { id: "m", label: "中" },
  { id: "l", label: "大" },
];
export const WIDGET_MATERIALS: { id: WidgetMaterial; label: "瓷" | "玻璃" }[] = [
  { id: "porcelain", label: "瓷" },
  { id: "glass", label: "玻璃" },
];

const KEY = "yibao-home-widgets";
const IDS = new Set<string>(HOME_WIDGETS.map((w) => w.id));

function readStored(): string | null {
  try { return localStorage.getItem(KEY); } catch { return null; }
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

export type AttachSpec = { to: string; edge: "start" | "end" | "top" | "bottom"; gap?: number; width?: number; height?: number };

export interface WidgetLayout {
  hidden: WidgetId[];
  size: Partial<Record<WidgetId, WidgetSize>>;
  material: Partial<Record<WidgetId, WidgetMaterial>>;
  order: WidgetId[];
  layouts: Partial<Record<string, {
    frames?: Partial<Record<string, FrameBox>>;
    attach?: Partial<Record<string, AttachSpec | null>>;
  }>>;
}

export function isWidgetId(v: unknown): v is WidgetId {
  return typeof v === "string" && IDS.has(v);
}
function isSize(v: unknown): v is WidgetSize {
  return v === "s" || v === "m" || v === "l";
}
function isMaterial(v: unknown): v is WidgetMaterial {
  return v === "porcelain" || v === "glass";
}

export function defaultLayout(): WidgetLayout {
  return {
    hidden: [],
    size: {},
    material: {},
    order: HOME_WIDGETS.map((w) => w.id),
    layouts: {},
  };
}

export function parseLayout(raw: string | null): WidgetLayout {
  const base = defaultLayout();
  if (!raw) return base;
  try {
    const parsed = JSON.parse(raw) as Partial<WidgetLayout>;
    if (Array.isArray(parsed.hidden)) base.hidden = parsed.hidden.filter(isWidgetId);
    if (parsed.size && typeof parsed.size === "object") {
      for (const [k, v] of Object.entries(parsed.size)) {
        if (isWidgetId(k) && isSize(v)) base.size[k] = v;
      }
    }
    if (parsed.material && typeof parsed.material === "object") {
      for (const [k, v] of Object.entries(parsed.material)) {
        if (isWidgetId(k) && isMaterial(v)) base.material[k] = v;
      }
    }
    if (Array.isArray(parsed.order)) {
      const next = parsed.order.filter(isWidgetId);
      for (const id of base.order) if (!next.includes(id)) next.push(id);
      base.order = next.filter((id, i) => next.indexOf(id) === i);
    }
    if (parsed.layouts && typeof parsed.layouts === "object") {
      base.layouts = parsed.layouts;
    }
    return base;
  } catch {
    return defaultLayout();
  }
}

export function specOf(layout: WidgetLayout, id: WidgetId) {
  const meta = HOME_WIDGETS.find((w) => w.id === id)!;
  return {
    id,
    label: meta.label,
    visible: !layout.hidden.includes(id),
    size: layout.size[id] ?? meta.defaultSize,
    material: layout.material[id] ?? meta.defaultMaterial,
    order: layout.order.indexOf(id),
  };
}

export function toggleHidden(layout: WidgetLayout, id: WidgetId): WidgetLayout {
  const hidden = layout.hidden.includes(id)
    ? layout.hidden.filter((x) => x !== id)
    : [...layout.hidden, id];
  return { ...layout, hidden };
}

export function setSize(layout: WidgetLayout, id: WidgetId, size: WidgetSize): WidgetLayout {
  return { ...layout, size: { ...layout.size, [id]: size } };
}

export function setMaterial(layout: WidgetLayout, id: WidgetId, material: WidgetMaterial): WidgetLayout {
  return { ...layout, material: { ...layout.material, [id]: material } };
}

export function moveWidget(layout: WidgetLayout, id: WidgetId, before: WidgetId | null): WidgetLayout {
  if (!HOME_WIDGETS.some((w) => w.id === id)) return layout;
  const order = layout.order.filter((x) => x !== id);
  const at = before ? order.indexOf(before) : -1;
  if (at >= 0) order.splice(at, 0, id);
  else order.push(id);
  return { ...layout, order };
}

export function setPartFrame(
  layout: WidgetLayout,
  preset: string,
  id: string,
  box: FrameBox,
): WidgetLayout {
  const prev = layout.layouts[preset] ?? {};
  return {
    ...layout,
    layouts: {
      ...layout.layouts,
      [preset]: {
        ...prev,
        frames: { ...prev.frames, [id]: { ...prev.frames?.[id], ...box } },
        attach: { ...prev.attach, [id]: null },
      },
    },
  };
}

export function clearPartFrame(layout: WidgetLayout, preset: string, id: string): WidgetLayout {
  const prev = layout.layouts[preset];
  if (!prev) return layout;
  const frames = { ...prev.frames };
  const attach = { ...prev.attach };
  delete frames[id];
  delete attach[id];
  const empty = !Object.keys(frames).length && !Object.keys(attach).length;
  const layouts = { ...layout.layouts };
  if (empty) delete layouts[preset];
  else layouts[preset] = { ...prev, frames, attach };
  return { ...layout, layouts };
}

export function clearPresetLayout(layout: WidgetLayout, preset: string): WidgetLayout {
  if (!layout.layouts[preset]) return layout;
  const layouts = { ...layout.layouts };
  delete layouts[preset];
  return { ...layout, layouts };
}

const state = reactive<WidgetLayout>(parseLayout(readStored()));

if (typeof watch === "function") {
  watch(state, (v) => {
    try { localStorage.setItem(KEY, JSON.stringify(v)); } catch { /* 无存储时保留本次窗口 */ }
  }, { deep: true });
}

function assign(next: WidgetLayout) {
  state.hidden = next.hidden;
  state.size = next.size;
  state.material = next.material;
  state.order = next.order;
  state.layouts = next.layouts;
}

export function useHomeWidgets() {
  return {
    state,
    spec: (id: WidgetId) => specOf(state, id),
    hide: (id: WidgetId) => assign(toggleHidden(state, id)),
    setSize: (id: WidgetId, size: WidgetSize) => assign(setSize(state, id, size)),
    setMaterial: (id: WidgetId, material: WidgetMaterial) => assign(setMaterial(state, id, material)),
    move: (id: WidgetId, before: WidgetId | null) => assign(moveWidget(state, id, before)),
    setFrame: (preset: string, id: string, box: FrameBox) => assign(setPartFrame(state, preset, id, box)),
    resetFrame: (preset: string, id: string) => assign(clearPartFrame(state, preset, id)),
    resetLayout: (preset: string) => assign(clearPresetLayout(state, preset)),
    reset: () => assign(defaultLayout()),
  };
}
