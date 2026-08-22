import type { Presentation } from "./surface-policy";

/**
 * 一行的「表面属性」：与「进度属性」（pstate）正交。
 * 进度回答「它在干嘛、做完没有」，表面回答「这里有个面板可以打开」。
 */
export interface SurfaceAttr {
  panel: string;
  title: string;
  /** 结果条数；取不到就只显示面板名 */
  count: number | null;
  /** 是否可点。新面板事件到达时旧行一律失活——流里永远最多一条可点 */
  live: boolean;
}

/**
 * 裁决器输出 → 小窗落地形态，二值。
 *
 * 小窗只有行一种形态（见 spec §2.1），所以 inline 与 peek 不再有形态差别；
 * 剩下的唯一区别是「是否允许开窗」，而那由 explicit 决定。
 *
 * 不需要给 decideSurface 加更严的自动上限：它在非 explicit 时本就封顶到 peek，
 * 因此 stage/focus 只可能在 explicit 时出现——「模型自动调用绝不在小窗开浮窗」
 * 由这个映射自动成立。
 */
export function petFormOf(d: { presentation: Presentation | null; show: boolean }): "window" | "line" {
  if (!d.show || d.presentation === null) return "line";
  return d.presentation === "stage" || d.presentation === "focus" ? "window" : "line";
}

/** db 类声明式工具恒返回 {rows:[…]}，据此白拿计数；其它形状取不到就不显示。 */
export function surfaceCount(data: unknown): number | null {
  if (!data || typeof data !== "object") return null;
  const rows = (data as { rows?: unknown }).rows;
  return Array.isArray(rows) ? rows.length : null;
}

/** 新面板事件到达：此前所有行的表面属性失活（只剩历史，不再是入口）。 */
export function deactivateAll(rows: { surface?: SurfaceAttr }[]): void {
  for (const r of rows) if (r.surface?.live) r.surface.live = false;
}
