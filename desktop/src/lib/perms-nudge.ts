// 权限引导按需降级（9-01 审计 P2-01 / 8-31 P0-7）：三项电脑控制权限缺失时不再常驻大卡，
// 默认降级为一行状态条；展开/收起偏好走 localStorage（同 run-metrics，纯界面偏好不写大脑设置）。
// 「真正需要时就地出现」：电脑控制工具被调用而权限缺失 → 父窗给 PermissionsNudge 传 demand 自动展开
// （会话级信号，不改用户持久化偏好）。
import { ref } from "vue";

const KEY = "yibao-perms-nudge";

/** 读取收起偏好：缺省收起（降级一行条）；存 "0" = 用户上次选择展开。 */
export function readPermsNudgeCollapsed(): boolean {
  try {
    return localStorage.getItem(KEY) !== "0";
  } catch {
    return true;
  }
}

/** 全局共享 ref：大窗/小窗同一份偏好，一边收起/展开另一边即时跟随。 */
export const permsNudgeCollapsed = ref(readPermsNudgeCollapsed());

export function setPermsNudgeCollapsed(collapsed: boolean): void {
  permsNudgeCollapsed.value = collapsed;
  try {
    localStorage.setItem(KEY, collapsed ? "1" : "0");
  } catch { /* 写不进去（如存储被禁）就保持内存态 */ }
}

/** 电脑控制工具（sidecar perception 域）：这些 action_proposed = 能力真正需要 ax/screen/input。 */
const COMPUTER_CONTROL_TOOLS: ReadonlySet<string> = new Set([
  "screenshot",
  "read_tree",
  "open_app",
  "click_control",
  "type_text",
  "computer_use",
]);

export function needsComputerControl(toolId: string | undefined): boolean {
  return toolId !== undefined && COMPUTER_CONTROL_TOOLS.has(toolId);
}
