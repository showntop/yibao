/** 主屏零件：目录与注册表（含插件零件注册）。
 * 纯数据 + 纯函数，不依赖 Vue 运行时。响应式"在线插件零件列表"由 composables/useAssembly 持有。 */

export type PartKind = "work" | "input" | "nav" | "context" | "glance";
export type DockEdge = "start" | "end" | "top" | "bottom";
export type PartSource = "core" | "plugin";

export const HOME_PARTS = [
  { id: "chat", kind: "work" as const, presentations: ["thread", "paper", "talk"] as const },
  { id: "dayTitle", kind: "glance" as const, presentations: ["title"] as const },
  { id: "composer", kind: "input" as const, presentations: ["bar"] as const },
  { id: "sessions", kind: "nav" as const, presentations: ["list", "spine", "cards"] as const },
  { id: "now", kind: "context" as const, presentations: ["inspector", "note"] as const },
  { id: "identity", kind: "glance" as const, presentations: ["tile", "seat"] as const },
  { id: "mind", kind: "glance" as const, presentations: ["map", "tile"] as const },
  { id: "when", kind: "glance" as const, presentations: ["tile"] as const },
  { id: "line", kind: "glance" as const, presentations: ["tile"] as const },
  { id: "jot", kind: "glance" as const, presentations: ["tile"] as const },
  { id: "bench", kind: "glance" as const, presentations: ["tile"] as const },
  { id: "today", kind: "glance" as const, presentations: ["tile", "panel"] as const },
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

/** 只读访问零件元数据（装配解析用）。 */
export function getPartMeta(id: PartId): PartMeta | undefined {
  return PARTS.get(id);
}

/** 同步插件零件：注册进注册表并返回当前在线列表（响应式状态由 composables/useAssembly 持有）。 */
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
  return [...keep];
}

export function resetPluginParts(): void {
  for (const [id, meta] of [...PARTS]) {
    if (meta.source === "plugin") PARTS.delete(id);
  }
}
