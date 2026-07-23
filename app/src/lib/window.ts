// 桌宠窗口控制（方案 A 固定窗口）：窗口恒为 360×520，**永不缩放**→不会因 resize 闪。
// 收起态只渲染团子（右上角），其余透明 + 点击穿透放行桌面；展开态渲染聊天。
// 全程 LogicalSize/LogicalPosition（Retina 下 Physical 会 2x 偏差）。
import { invoke } from "@tauri-apps/api/core";
import { getCurrentWindow, LogicalSize } from "@tauri-apps/api/window";

const WIN_W = 360;
const WIN_H = 520;

/** 启动兜底：确保固定窗口尺寸（不移动位置）。 */
export async function resetWindowSize(): Promise<void> {
  await getCurrentWindow().setSize(new LogicalSize(WIN_W, WIN_H));
}

export const startDrag = (): Promise<void> => getCurrentWindow().startDragging();

/** 打开/聚焦面板窗（窗不存在由 Rust 侧创建；大脑 panel 事件触发）。 */
export const openPanel = (): Promise<void> => invoke("open_panel_window");

/** 通知 Rust 点击穿透模式：full=true 整窗可交互（展开/气泡中），false 仅团子热区可交互、其余穿透到桌面。 */
export const setInteractiveFull = (full: boolean): Promise<void> =>
  invoke("set_interactive_full", { full });
