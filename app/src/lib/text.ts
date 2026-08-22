// 通用文本工具：截断、空白收敛（单一事实源，组件内不要手写 slice(0,N)+"…"）。

/** 超长截断，尾部省略号。 */
export function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n) + "…" : s;
}

/** 收敛空白：连续空白折叠为单个空格并去首尾。 */
export function squashSpaces(s: string): string {
  return s.replace(/\s+/g, " ").trim();
}
