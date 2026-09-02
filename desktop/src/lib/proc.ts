// 过程展示（工具调用气泡行）共享小工具：四个对话场景（宠物窗/大窗对话/面板浮窗/大窗插件页）同款逻辑。
import type { BrainAction, BrainResult } from "./brain";

/** 过程行标题：技能短标签，回退 tool_id。 */
export function procLabel(a?: BrainAction): string {
  return a?.label || a?.tool_id || "操作";
}

/** use_plugin 不插过程行（成功有 notice 轻提示，重复；失败由 LLM 下一句转告）。 */
export function procSkip(a?: BrainAction): boolean {
  return a?.tool_id === "use_plugin";
}

import { truncate } from "./text";
export { truncate } from "./text";

/** 过程行结果摘要：失败带 error（60 字截断），成功空串（行尾只换 ✅）。 */
export function procResultSuffix(r?: BrainResult): string {
  if (r && !r.success) return "：" + truncate(r.error || "失败", 60);
  return "";
}

/** error 事件（用户拒绝/策略禁止）的过程行行尾短理由：完整文案由告警行承担，行尾只缀结论。 */
export function procErrorReason(text?: string): string {
  const t = text ?? "";
  if (t.includes("拒绝")) return "已拒绝";
  if (t.includes("禁止")) return "已拦截";
  return t || "失败";
}

/** pstate 过程行（宠物窗/面板浮窗/插件页共用形状）。 */
export type ProcStateRow = { text: string; pstate?: "run" | "ok" | "fail" };

/** 拒绝/禁止执行的 error 事件：对应过程行原地收尾为失败（否则没有 action_result，永远转圈）。 */
export function settleProcOnError(rows: ProcStateRow[], procIdx: Map<string, number>, e: { action?: BrainAction; text?: string }): void {
  if (e.action?.id === undefined) return;
  const idx = procIdx.get(e.action.id);
  if (idx === undefined) return;
  const row = rows[idx];
  if (row && row.pstate === "run") {
    row.pstate = "fail";
    row.text = procLabel(e.action) + procResultSuffix({ success: false, error: procErrorReason(e.text) });
  }
  procIdx.delete(e.action.id);
}

/** 打断：全部在途过程行收尾为失败（行尾缀「已打断」），停下转圈。 */
export function settleProcsOnInterrupt(rows: ProcStateRow[], procIdx: Map<string, number>): void {
  for (const [id, idx] of procIdx) {
    const row = rows[idx];
    if (row && row.pstate === "run") {
      row.pstate = "fail";
      row.text += procResultSuffix({ success: false, error: "已打断" });
    }
    procIdx.delete(id);
  }
}

/** 大窗「详情」展开内容：参数 pretty JSON + 结果（error 优先，其次 data.human，再 data 全量），各截 800 字。 */
export function procDetail(a?: BrainAction, r?: BrainResult): string {
  const parts: string[] = [];
  if (a?.params && Object.keys(a.params).length) {
    parts.push("参数：\n" + truncate(JSON.stringify(a.params, null, 2), 800));
  }
  if (r) {
    const out = !r.success
      ? (r.error || "失败")
      : String(r.data?.human ?? "") || truncate(JSON.stringify(r.data ?? {}, null, 2), 800);
    parts.push("结果：\n" + truncate(out, 800));
  }
  return parts.join("\n\n") || "（无细节）";
}
