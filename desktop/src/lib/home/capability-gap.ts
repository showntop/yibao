// 能力边界卡（对话流内联）：project.create 等工具结果带 capability 摘要时，
// 缺能力必须 visibly 落卡，不能只靠模型转述。检测逻辑是纯函数，便于单测。
import type { BrainResult } from "../brain";

/** 能力边界卡素材：可达前缀末段 + 可达/缺失段列表 + 一句降级建议。 */
export interface CapabilityGap {
  /** 可做到哪（available_stages 末段标签；空 = 一段都做不到） */
  through: string;
  available: readonly string[];
  missing: readonly string[];
  /** 降级建议（degradation），缺省回退 blocked_reason */
  note: string;
}

function stringsOf(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((s): s is string => typeof s === "string") : [];
}

/** action_result 检测：成功 + data.capability + enforced + 有缺失 → 出卡；否则 null。 */
export function capabilityGapFromResult(result: BrainResult | undefined): CapabilityGap | null {
  if (!result?.success) return null;
  const cap = result.data?.capability;
  if (typeof cap !== "object" || cap === null) return null;
  const summary = cap as Record<string, unknown>;
  if (summary.enforced !== true) return null;
  const missing = stringsOf(summary.missing_stages);
  if (!missing.length) return null;
  const available = stringsOf(summary.available_stages);
  const degradation = typeof summary.degradation === "string" ? summary.degradation.trim() : "";
  const reason = typeof summary.blocked_reason === "string" ? summary.blocked_reason.trim() : "";
  return {
    through: available[available.length - 1] ?? "",
    available,
    missing,
    note: degradation || reason,
  };
}

/** 卡标题（也作气泡回退文本：纸面摊法等不渲染卡的面退化为这一行）。 */
export function capabilityGapTitle(gap: CapabilityGap): string {
  return gap.through ? `能力边界 · 可做到${gap.through}` : "能力边界";
}
