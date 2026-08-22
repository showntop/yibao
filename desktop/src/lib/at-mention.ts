/** @ 文件引用（输入条 chips 化）：@ 触发解析 / 触发片段移除 / 引用 contexts 的文本组装。
 *  coding 面板（panel/src/lib/refs.ts）另有同款 composeRefs（iframe 沙箱不引 ts 模块，格式约定保持一致）。 */

export interface AtTrigger {
  start: number; // "@" 在文本中的下标
  query: string; // @ 后的查询词（[\w\-./]*，与 coding 面板内联菜单同规则）
}

/** 解析光标前的 @ 触发片段，无则 null。 */
export function parseAtTrigger(text: string, caret: number): AtTrigger | null {
  const c = Math.max(0, Math.min(caret, text.length));
  const before = text.slice(0, c);
  const m = /@([\w\-./]*)$/.exec(before);
  if (!m) return null;
  return { start: before.length - m[0].length, query: m[1] };
}

/** 选中文件后移除触发片段（@query），返回剩余文本。 */
export function stripAtTrigger(text: string, caret: number, start: number): string {
  const c = Math.max(0, Math.min(caret, text.length));
  const s = Math.max(0, Math.min(start, c));
  return text.slice(0, s) + text.slice(c);
}

/** 输入条待发送上下文：attachment=本地文件/粘贴图片（path=落盘绝对路径，preview=内存预览 dataURL），
 *  reference=最近会话引用（refId=被引用会话 id，发送时展开其消息内容作上下文），
 *  file=@ 目录内文件（path=相对搜索根路径；搜索根由用户选目录，与 coding 会话无关）。 */
export interface InputContext {
  kind: "attachment" | "reference" | "file";
  label: string;
  path?: string;
  preview?: string;
  /** reference 专用：被引用会话 id（发送时 InputBar 拉取该会话消息展开成前缀） */
  refId?: string;
}

/** contexts → 发送文本前缀（每行一条【kind：label】；file/attachment 有 path 落路径，AI 能据此定位文件）。 */
export function formatContextPrefix(contexts: InputContext[]): string {
  if (!contexts.length) return "";
  const lines = contexts.map((c) => {
    if (c.kind === "file") return `【文件：${c.path ?? c.label}】`;
    if (c.kind === "attachment") return `【附件：${c.path ?? c.label}】`;
    return `【引用：${c.label}】`;
  });
  return lines.join("\n") + "\n\n";
}
