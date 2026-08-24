// 通用文本工具：截断、空白收敛（单一事实源，组件内不要手写 slice(0,N)+"…"）。

/** 超长截断，尾部省略号。 */
export function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n) + "…" : s;
}

/** 收敛空白：连续空白折叠为单个空格并去首尾。 */
export function squashSpaces(s: string): string {
  return s.replace(/\s+/g, " ").trim();
}

/**
 * 剥掉任务收尾文案的 emoji 状态前缀（✅/❌/⏰/⏹）。
 * 后端事件 text 保留前缀是协议事实（mobile 端 raw text 依赖它做状态信号），
 * desktop 有结构化 meta.status + YbIcon 双通道，展示层剥掉避免「图标 + 徽章 + emoji」三重复。
 */
export function stripTaskStatusEmoji(s: string): string {
  return s.replace(/^(✅|❌|⏰|⏹)\s*/u, "");
}
