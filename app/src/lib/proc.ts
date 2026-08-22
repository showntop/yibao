// 过程展示（工具调用气泡行）共享小工具：四个对话场景（宠物窗/大窗对话/面板浮窗/大窗插件页）同款逻辑。
import type { BrainAction, BrainResult } from "./brain";

/** 过程行标题：技能短标签，回退 skill_id。 */
export function procLabel(a?: BrainAction): string {
  return a?.label || a?.skill_id || "操作";
}

/** use_plugin 不插过程行（成功有 notice 轻提示，重复；失败由 LLM 下一句转告）。 */
export function procSkip(a?: BrainAction): boolean {
  return a?.skill_id === "use_plugin";
}

import { truncate } from "./text";
export { truncate } from "./text";

/** 过程行结果摘要：失败带 error（60 字截断），成功空串（行尾只换 ✅）。 */
export function procResultSuffix(r?: BrainResult): string {
  if (r && !r.success) return "：" + truncate(r.error || "失败", 60);
  return "";
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
