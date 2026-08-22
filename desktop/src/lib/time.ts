// 时间格式化公共工具：收敛各组件手写 padStart 与同类格式化逻辑。
// 组件内请优先复用这里，不要重新实现 HH:MM / 相对时间 / 时长。

/** 两位数补零。 */
export function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

/** "HH:MM"（24 小时制）。 */
export function fmtHHMM(d: Date): string {
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

/** unix 秒 → "HH:MM"。 */
export function fmtHHMMFromSec(ts: number): string {
  return fmtHHMM(new Date(ts * 1000));
}

/** "M/d"。 */
export function fmtMonthDay(d: Date): string {
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

/** "M月d日 HH:mm"（schema date 过滤器用）。 */
export function fmtMonthDayTime(d: Date): string {
  return `${d.getMonth() + 1}月${d.getDate()}日 ${fmtHHMM(d)}`;
}

/** ISO 串 → "M-dd HH:mm"；非法输入返回空串。 */
export function fmtShortDateTime(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  return `${d.getMonth() + 1}-${pad2(d.getDate())} ${fmtHHMM(d)}`;
}

/** 会话列表时间：今天 → "HH:mm"；昨天 → "昨天"；今年 → "M/d"；往年 → "YYYY/M/d"。 */
export function fmtClockToday(ts: number): string {
  const date = new Date(ts);
  const now = new Date();
  if (date.toDateString() === now.toDateString()) return fmtHHMM(date);
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (date.toDateString() === yesterday.toDateString()) return "昨天";
  if (date.getFullYear() === now.getFullYear()) return `${date.getMonth() + 1}/${date.getDate()}`;
  return `${date.getFullYear()}/${date.getMonth() + 1}/${date.getDate()}`;
}

/** 相对时间：刚刚 / N分钟前 / N小时前 / N天前。 */
export function relativeTime(ts: number): string {
  const seconds = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (seconds < 60) return "刚刚";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
  return `${Math.floor(seconds / 86400)} 天前`;
}

/** 运行时长：刚开始 / 已运行 N 分钟 / 已运行 N 小时。 */
export function elapsedSince(ts: number): string {
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (seconds < 60) return "刚开始";
  if (seconds < 3600) return `已运行 ${Math.floor(seconds / 60)} 分钟`;
  return `已运行 ${Math.floor(seconds / 3600)} 小时`;
}

/** 秒 → "1.2h"；0 或负数返回空串。 */
export function fmtHours(sec: number): string {
  return sec > 0 ? `${(sec / 3600).toFixed(1)}h` : "";
}
