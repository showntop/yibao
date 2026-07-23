// 桌宠窗口控制：收起/展开切换，展开方向按屏幕剩余空间自适应（形象屏幕位置钉死不动）。
// 全程用 LogicalSize/LogicalPosition（Retina 下 Physical 会 2x 偏差）。
import { invoke } from "@tauri-apps/api/core";
import {
  getCurrentWindow,
  LogicalPosition,
  LogicalSize,
  currentMonitor,
} from "@tauri-apps/api/window";

const COLLAPSE_W = 132;
const COLLAPSE_H = 120;
const EXP_W = 360;
const EXP_H = 520;
const BUBBLE_W = 320; // 说话态窗口宽：左气泡 + 右团子（88）；高度不变 = COLLAPSE_H
const PET = 88;
const PET_OFF_X = 22; // 形象在收起窗内的偏移（132 宽居中 88 → 22）
const PET_OFF_Y = 12; // 形象在收起窗内的上偏移

/** 形象在展开面板的哪个角（= 面板展开的反方向角）。 */
export type Dir = "nw" | "ne" | "sw" | "se";

function petOffset(dir: Dir) {
  return {
    x: dir.includes("w") ? PET_OFF_X : EXP_W - PET_OFF_X - PET,
    y: dir.includes("n") ? PET_OFF_Y : EXP_H - PET_OFF_Y - PET,
  };
}

/** 启动兜底：确保收起态尺寸（不移动位置）。 */
export async function resetCollapsedSize(): Promise<void> {
  await getCurrentWindow().setSize(new LogicalSize(COLLAPSE_W, COLLAPSE_H));
}

/** 展开：以形象当前屏幕位置为锚，按屏幕剩余空间选择展开方向，
 * setPosition + setSize（缓动补间），返回方向供前端布局镜像。 */
export async function expand(): Promise<Dir> {
  const win = getCurrentWindow();
  const mon = await currentMonitor();
  const pos = await win.outerPosition();
  const s = mon?.scaleFactor ?? 1;
  // 形象屏幕左上（收起态：窗口左上 + 形象偏移）
  const Px = pos.x / s + PET_OFF_X;
  const Py = pos.y / s + PET_OFF_Y;
  const mx = (mon?.position.x ?? 0) / s;
  const my = (mon?.position.y ?? 0) / s;
  const sw = (mon?.size.width ?? 1440) / s;
  const sh = (mon?.size.height ?? 900) / s;
  // 向右展开（形象在左）需形象右侧容纳面板右半；向下展开（形象在上）同理
  const goRight = Px + (EXP_W - PET_OFF_X) <= mx + sw;
  const goDown = Py + (EXP_H - PET_OFF_Y) <= my + sh;
  const dir = `${goDown ? "n" : "s"}${goRight ? "w" : "e"}` as Dir;
  const off = petOffset(dir);
  await tween(win, { w: COLLAPSE_W, h: COLLAPSE_H, x: Px - PET_OFF_X, y: Py - PET_OFF_Y }, { w: EXP_W, h: EXP_H, x: Px - off.x, y: Py - off.y });
  return dir;
}

/** 收起：沿同一锚点缩回（缓动补间），形象屏幕位置不变。 */
export async function collapse(dir: Dir): Promise<void> {
  const win = getCurrentWindow();
  const mon = await currentMonitor();
  const pos = await win.outerPosition();
  const s = mon?.scaleFactor ?? 1;
  const off = petOffset(dir);
  const Px = pos.x / s + off.x;
  const Py = pos.y / s + off.y;
  await tween(
    win,
    { w: EXP_W, h: EXP_H, x: Px - off.x, y: Py - off.y },
    { w: COLLAPSE_W, h: COLLAPSE_H, x: Px - PET_OFF_X, y: Py - PET_OFF_Y },
  );
}

/** 窗口尺寸/位置缓动补间（ease-out cubic）：收放不再瞬间跳变。 */
async function tween(
  win: ReturnType<typeof getCurrentWindow>,
  from: { w: number; h: number; x: number; y: number },
  to: { w: number; h: number; x: number; y: number },
  durMs = 150,
): Promise<void> {
  const steps = 6;
  for (let i = 1; i <= steps; i++) {
    const t = i / steps;
    const e = 1 - Math.pow(1 - t, 3);
    const lerp = (a: number, b: number) => a + (b - a) * e;
    // 不取整：x 与 w 线性补间时和（=窗口右沿）恒定，右锚团子才不会逐帧 ±1px 抖动。
    await win.setSize(new LogicalSize(lerp(from.w, to.w), lerp(from.h, to.h)));
    await win.setPosition(new LogicalPosition(lerp(from.x, to.x), lerp(from.y, to.y)));
    if (i < steps) await new Promise((r) => setTimeout(r, durMs / steps));
  }
}

/** 说话态：窗口向左撑宽到 BUBBLE_W（右沿屏幕坐标固定 → 团子原地不动），左侧腾出气泡位。 */
export async function speakOpen(): Promise<void> {
  await bubbleTween(BUBBLE_W);
}

/** 收起气泡：沿同一右沿缩回 COLLAPSE_W（团子回到 132 居中位）。 */
export async function speakClose(): Promise<void> {
  await bubbleTween(COLLAPSE_W);
}

/** 右沿固定的宽度补间：从当前宽 → targetW，保持窗口右沿屏幕坐标不变，
 *  这样 CSS 上团子（right:34 锚到右沿）在撑开/收回全程原地不动。 */
async function bubbleTween(targetW: number): Promise<void> {
  const win = getCurrentWindow();
  const mon = await currentMonitor();
  const s = mon?.scaleFactor ?? 1;
  const pos = await win.outerPosition();
  const sz = await win.outerSize();
  const x = pos.x / s;
  const y = pos.y / s;
  const w = sz.width / s;
  const rightEdge = x + w;
  await tween(win, { w, h: COLLAPSE_H, x, y }, { w: targetW, h: COLLAPSE_H, x: rightEdge - targetW, y });
}

export const startDrag = (): Promise<void> => getCurrentWindow().startDragging();

/** 打开/聚焦面板窗（窗不存在由 Rust 侧创建；大脑 panel 事件触发）。 */
export const openPanel = (): Promise<void> => invoke("open_panel_window");
