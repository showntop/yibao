/** 主屏零件布局：与 finish 正交。
 *  finish 管瓷/纸/金的全局几何；本文件管每一块零件的显隐、大小、瓷/玻璃、排序。
 *  新零件 = HOME_WIDGETS 加一行 + 栏里包 <HomeWidget id="…">。 */
import { reactive, watch } from "vue";

export const HOME_WIDGETS = [
  { id: "identity", rail: "left", label: "身份", defaultSize: "m", defaultMaterial: "porcelain" },
  { id: "mind", rail: "left", label: "认知", defaultSize: "l", defaultMaterial: "porcelain" },
  { id: "today", rail: "left", label: "今日", defaultSize: "s", defaultMaterial: "porcelain" },
  { id: "sessions", rail: "left", label: "会话", defaultSize: "l", defaultMaterial: "porcelain" },
  { id: "now", rail: "right", label: "本次", defaultSize: "m", defaultMaterial: "porcelain" },
] as const;

export type WidgetId = (typeof HOME_WIDGETS)[number]["id"];
export type WidgetSize = "s" | "m" | "l";
export type WidgetMaterial = "porcelain" | "glass";
export type WidgetRail = "left" | "right";

export const WIDGET_SIZES: { id: WidgetSize; label: string }[] = [
  { id: "s", label: "小" },
  { id: "m", label: "中" },
  { id: "l", label: "大" },
];
export const WIDGET_MATERIALS: { id: WidgetMaterial; label: string }[] = [
  { id: "porcelain", label: "瓷" },
  { id: "glass", label: "玻璃" },
];

const KEY = "yibao-home-widgets";
const IDS = new Set<string>(HOME_WIDGETS.map((w) => w.id));

function readStored(): string | null {
  try { return localStorage.getItem(KEY); } catch { return null; }
}

export interface WidgetLayout {
  hidden: WidgetId[];
  size: Partial<Record<WidgetId, WidgetSize>>;
  material: Partial<Record<WidgetId, WidgetMaterial>>;
  order: WidgetId[];
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
    order: HOME_WIDGETS.filter((w) => w.rail === "left").map((w) => w.id),
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
    rail: meta.rail,
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

/** 把 `id` 插到 `before` 前面；`before` 空则放到末尾。只动同轨。 */
export function moveWidget(layout: WidgetLayout, id: WidgetId, before: WidgetId | null): WidgetLayout {
  const meta = HOME_WIDGETS.find((w) => w.id === id);
  if (!meta || meta.rail !== "left") return layout;
  const order = layout.order.filter((x) => x !== id);
  const at = before ? order.indexOf(before) : -1;
  if (at >= 0) order.splice(at, 0, id);
  else order.push(id);
  return { ...layout, order };
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
}

export function useHomeWidgets() {
  return {
    state,
    spec: (id: WidgetId) => specOf(state, id),
    hide: (id: WidgetId) => assign(toggleHidden(state, id)),
    setSize: (id: WidgetId, size: WidgetSize) => assign(setSize(state, id, size)),
    setMaterial: (id: WidgetId, material: WidgetMaterial) => assign(setMaterial(state, id, material)),
    move: (id: WidgetId, before: WidgetId | null) => assign(moveWidget(state, id, before)),
    reset: () => assign(defaultLayout()),
  };
}
