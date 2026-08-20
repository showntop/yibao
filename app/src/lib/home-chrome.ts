/** 主屏 chrome 适配：预设名仍走 yibao-chrome。布局真相在 home-assembly。 */
import { computed, inject, ref, type ComputedRef, type InjectionKey } from "vue";
import type { WidgetId } from "./home-widgets";
import { HOME_WIDGETS, defaultLayout, useHomeWidgets } from "./home-widgets";
import {
  HOME_PRESET_DEFAULT,
  HOME_PRESETS,
  gridStyle,
  isHomePresetId,
  itemOf,
  presentationOf,
  resolveAssembly,
  type Assembly,
  type HomePresetId,
} from "./home-assembly";

export type HomeChromeId = HomePresetId;
export type HomeSurfaceKind = "thread" | "paper";
export type SessionVariant = "list" | "spine";
export type PeekDensity = "inspector" | "note";
export type MindDensity = "map" | "tile";
export type HomeSlot = string;

/** 窄书脊两字；maxChars=0 时交整行标题，由 CSS 截断。 */
export function spineCaption(title: string, maxChars = 2): string {
  const t = title.trim();
  if (!t || /^新对话/.test(t)) return maxChars ? "页" : (t || "新对话");
  if (!maxChars) return t;
  const stripped = t.replace(/[\s"'`「」『』《》【】[\]（）()·./\\-]+/g, "");
  const chars = Array.from(stripped);
  const cjk = chars.filter((ch) => /\p{Script=Han}/u.test(ch));
  if (cjk.length >= maxChars) return cjk.slice(0, maxChars).join("");
  if (cjk.length === 1) return cjk[0];
  return chars.slice(0, maxChars).join("") || "页";
}

/** 书脊只露出最近几页；当前页不在窗口里时换掉最旧的一张。limit=0 表示全部。 */
export function spineVisible<T extends { id: string }>(
  sessions: readonly T[],
  activeId: string,
  limit: number,
): T[] {
  if (!limit || sessions.length <= limit) return [...sessions];
  const head = sessions.slice(0, limit);
  if (head.some((s) => s.id === activeId)) return head;
  const active = sessions.find((s) => s.id === activeId);
  if (!active) return head;
  return [...head.slice(0, limit - 1), active];
}

function slotName(preset: HomePresetId, id: WidgetId): HomeSlot {
  const item = itemOf(resolveAssembly(preset, defaultLayout()), id);
  if (!item) return "";
  if (item.region) return item.region;
  if (item.dock) return `${item.dock.to}:${item.dock.edge}`;
  return "";
}

function chromeView(id: HomePresetId) {
  const assembly = resolveAssembly(id, defaultLayout());
  const preset = HOME_PRESETS[id];
  const widgetSlot = Object.fromEntries(
    HOME_WIDGETS.map((w) => [w.id, slotName(id, w.id)]),
  ) as Record<WidgetId, HomeSlot>;
  return {
    id,
    label: preset.label,
    hint: preset.hint,
    surface: (presentationOf(assembly, "chat") ?? "thread") as HomeSurfaceKind,
    sessionVariant: (presentationOf(assembly, "sessions") ?? "list") as SessionVariant,
    peekDensity: (presentationOf(assembly, "now") ?? "inspector") as PeekDensity,
    mindDensity: (presentationOf(assembly, "mind") ?? "map") as MindDensity,
    spineLimit: (presentationOf(assembly, "sessions") === "spine" ? 4 : 0) as 0 | 4,
    spineChars: 2 as const,
    collapsible: preset.collapsible,
    widgetSlot,
    columns: preset.grid.columns,
    rows: preset.grid.rows,
    areaRows: preset.grid.areas,
    surfaceSlot: {
      book: itemOf(assembly, "chat")?.region ?? "book",
      paper: "paper",
      composer: itemOf(assembly, "composer")?.region ?? "compose",
      peek: slotName(id, "now"),
    },
  };
}

export const HOME_CHROMES = {
  rails: chromeView("rails"),
  desk: chromeView("desk"),
} as const;

export type HomeChrome = (typeof HOME_CHROMES)[HomeChromeId];

export const HOME_CHROME_DEFAULT: HomeChromeId = HOME_PRESET_DEFAULT;
export const HOME_CHROME_LIST: HomeChrome[] = [HOME_CHROMES.rails, HOME_CHROMES.desk];

const KEY = "yibao-chrome";
const chromeId = ref<HomeChromeId>(HOME_CHROME_DEFAULT);

export function isHomeChromeId(v: string | null): v is HomeChromeId {
  return isHomePresetId(v);
}

export function readChrome(): HomeChromeId {
  try {
    const v = localStorage.getItem(KEY);
    return isHomeChromeId(v) ? v : HOME_CHROME_DEFAULT;
  } catch {
    return HOME_CHROME_DEFAULT;
  }
}

export function bootChrome(): HomeChromeId {
  const id = readChrome();
  chromeId.value = id;
  document.documentElement.dataset.chrome = id;
  return id;
}

export function applyChrome(id: HomeChromeId): void {
  chromeId.value = id;
  document.documentElement.dataset.chrome = id;
  try { localStorage.setItem(KEY, id); } catch { /* ignore */ }
}

export function chromeOf(id: HomeChromeId = chromeId.value): HomeChrome {
  return HOME_CHROMES[id];
}

export function widgetSlot(id: WidgetId, chrome: HomeChromeId = chromeId.value): HomeSlot {
  return HOME_CHROMES[chrome].widgetSlot[id];
}

export function chromeGridStyle(
  id: HomeChromeId,
  opts?: { peek?: boolean; compact?: boolean },
): Record<string, string> | null {
  const assembly = resolveAssembly(id, defaultLayout(), { compact: opts?.compact === true });
  return gridStyle(assembly.grid);
}

export function useHomeChrome() {
  const spec = computed(() => HOME_CHROMES[chromeId.value]);
  return { id: chromeId, spec, apply: applyChrome };
}

export const HOME_ASSEMBLY_KEY: InjectionKey<ComputedRef<Assembly>> = Symbol("home-assembly");

/** 当前装配。在 HomeFrame 内走注入（含 compact）；在外则按预设+偏好现算。 */
export function useLiveAssembly(): ComputedRef<Assembly> {
  const injected = inject(HOME_ASSEMBLY_KEY, null);
  if (injected) return injected;
  const widgets = useHomeWidgets();
  return computed(() => resolveAssembly(chromeId.value, widgets.state));
}
