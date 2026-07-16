// 桌宠窗口控制：收起/展开切换，展开方向按屏幕剩余空间自适应（形象屏幕位置钉死不动）。
// 全程用 LogicalSize/LogicalPosition（Retina 下 Physical 会 2x 偏差）。
import {
  getCurrentWindow,
  LogicalPosition,
  LogicalSize,
  currentMonitor,
} from "@tauri-apps/api/window";

const COLLAPSE_W = 132;
const COLLAPSE_H = 140;
const EXP_W = 320;
const EXP_H = 460;
const PET = 64;
const PET_OFF_X = 34; // 形象在收起窗内的左偏移（132 宽居中 64 → 34）
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

/**
 * 展开：以形象当前屏幕位置为锚，按屏幕剩余空间选择展开方向，
 * setPosition + setSize，返回方向供前端布局镜像。
 */
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
  await win.setSize(new LogicalSize(EXP_W, EXP_H));
  await win.setPosition(new LogicalPosition(Px - off.x, Py - off.y));
  return dir;
}

/** 收起：沿同一锚点缩回，形象屏幕位置不变。 */
export async function collapse(dir: Dir): Promise<void> {
  const win = getCurrentWindow();
  const mon = await currentMonitor();
  const pos = await win.outerPosition();
  const s = mon?.scaleFactor ?? 1;
  const off = petOffset(dir);
  const Px = pos.x / s + off.x;
  const Py = pos.y / s + off.y;
  await win.setSize(new LogicalSize(COLLAPSE_W, COLLAPSE_H));
  await win.setPosition(new LogicalPosition(Px - PET_OFF_X, Py - PET_OFF_Y));
}

export const startDrag = (): Promise<void> => getCurrentWindow().startDragging();
