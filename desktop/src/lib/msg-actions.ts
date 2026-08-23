// 消息可操作性策略（单一事实来源）：一条消息能做什么，只由它的 role 决定。
//
// 背景（2026-08-23）：此前操作按钮散落在渲染层 if-else（runShowFooter / 分支判断），
// 导致 sys 提示行（use_plugin 展开通知等）被渲染出「复制/赞/踩/重写」完整工具栏——
// 系统通知既非 AI 生成内容（不可重写/评价），也非用户消息（不可编辑），应零操作。
//
// 语义：
//   user  = 可复制 / 编辑重发
//   ai    = 可复制 / 反馈（👍👎）/ 重写（halted 时渲染为「重试」）
//   sys   = 零操作（系统通知、轻提示、过程行基底——过程行另有 proc 结构区分）
//
// 渲染层只遍历 actionsOf(role) 出按钮，不许再写「角色 → 按钮」的散落条件。
export type BubbleRole = "user" | "ai" | "sys";
export type MsgAction = "copy" | "edit" | "feedback" | "regenerate";

export const ROLE_ACTIONS: Record<BubbleRole, ReadonlySet<MsgAction>> = {
  user: new Set(["copy", "edit"]),
  ai: new Set(["copy", "feedback", "regenerate"]),
  sys: new Set(),
};

/** 操作定义：copy/edit/regenerate 显示文字，feedback 渲染为 👍/👎 双按钮。 */
export const ACTION_DEFS: Record<MsgAction, { label?: string; icon?: "thumb-up" | "thumb-down" }> = {
  copy: { label: "复制" },
  edit: { label: "编辑" },
  feedback: { icon: "thumb-up" },
  regenerate: { label: "重写" },
};

/** 一条消息可执行的操作列表（顺序即按钮顺序）。 */
export function actionsOf(role: BubbleRole): MsgAction[] {
  return [...ROLE_ACTIONS[role]];
}
