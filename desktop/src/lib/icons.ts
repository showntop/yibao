// 插件图标工具：按 id 哈希到 5 色调色板 + 首字符头像。
// 单一事实源（App.vue / HomePlugins.vue / QuickPanel.vue 共用）。
// 注意：QuickPanel 原哈希为 h*31，统一为 djb2 后颜色分布可能变化（仍为同一 5 色板）。

export const ICON_PALETTE = [
  { bg: "var(--yb-icon-bg-0)", fg: "var(--yb-icon-fg-0)" },
  { bg: "var(--yb-icon-bg-1)", fg: "var(--yb-icon-fg-1)" },
  { bg: "var(--yb-icon-bg-2)", fg: "var(--yb-icon-fg-2)" },
  { bg: "var(--yb-icon-bg-3)", fg: "var(--yb-icon-fg-3)" },
  { bg: "var(--yb-icon-bg-4)", fg: "var(--yb-icon-fg-4)" },
] as const;

/** djb2 哈希（非负）。 */
export function djb2(s: string): number {
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

/** 插件首字符样式（背景/前景色，主题感知 CSS 变量）。 */
export function iconStyle(id: string) {
  const c = ICON_PALETTE[djb2(id) % ICON_PALETTE.length];
  return { background: c.bg, color: c.fg };
}

/** 首字符头像文案（大写）；空名回退 "?"。 */
export function initial(name: string): string {
  const ch = name.trim().charAt(0);
  return ch ? ch.toUpperCase() : "?";
}
