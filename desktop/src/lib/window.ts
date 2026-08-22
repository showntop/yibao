// 桌宠窗口控制（单窗三态架构）：
//  - idle/quick：窗口恒 320×300（**永不 resize**→高频 hover 零闪烁），内容层 v-show 切换；
//    仅前端上报的热区可交互（团子盒 + 快捷面板），其余透明 + 点击穿透放行桌面。
//  - chat：resize 到 360×520（低频主动展开，整窗可交互）。
import { invoke } from "@tauri-apps/api/core";
import { getCurrentWindow, LogicalSize } from "@tauri-apps/api/window";

/** 收起/快捷态恒窗尺寸（CSS 像素），chat 展开时由 setMainSize 切换。 */
const WIN_W = 320;
const WIN_H = 300;

/** 启动兜底：确保收起态固定窗口尺寸（不移动位置）。 */
export async function resetWindowSize(): Promise<void> {
  await getCurrentWindow().setSize(new LogicalSize(WIN_W, WIN_H));
}

/** 打开/聚焦面板窗（窗不存在由 Rust 侧创建；大脑 panel 事件触发）。 */
export const openPanel = (): Promise<void> => invoke("open_panel_window");

/** 通知 Rust 点击穿透模式：full=true 整窗可交互（展开），false 仅团子热区可交互、其余穿透到桌面。 */
export const setInteractiveFull = (full: boolean): Promise<void> =>
  invoke("set_interactive_full", { full });

/** 通知 Rust 说话气泡显隐：气泡带成为第二热区（点气泡=展开），其余透明区照常穿透——气泡不再整窗拦点击。 */
export const setBubbleOn = (on: boolean): Promise<void> =>
  invoke("set_bubble_on", { on });

/** 拖窗：由 Avatar 手动 setPosition 实现（见 startDrag 说明），系统 startDragging 会 clamp 窗口顶部到菜单栏 */
// export const startDrag = (): Promise<void> => getCurrentWindow().startDragging();

/** 单窗热区上报（App.vue 调用）：窗口内相对矩形组，kind 区分——
 *  "pet" 团子元素盒（enter 信号只由它驱动）/ "ui" 快捷面板（3 圆 + 输入条）。
 *  Rust 据此放行鼠标穿透（否则移到面板上被穿透点不到）；窗口相对坐标，拖动自动跟随。 */
export const setHotRects = (
  rects: { x: number; y: number; w: number; h: number; kind: string }[] | null,
): Promise<void> =>
  invoke("set_hot_rects", {
    rects: rects
      ? rects.map((r) => ({ x: r.x, y: r.y, w: r.w, h: r.h, kind: r.kind }))
      : null,
  });

/** 团子窗尺寸：收起/快捷 320×300 / 展开对话 360×520（以左上角为锚，团子原地不动）。 */
export const setMainSize = (w: number, h: number): Promise<void> =>
  invoke("set_main_size", { width: w, height: h });
