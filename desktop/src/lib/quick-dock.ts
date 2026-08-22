/** 收起态窗口与团子锚点（与 App.vue `.pet` / InputBar 高度常量一致）。 */
export const PET_LEFT = 144;
export const PET_SIZE = 96;
export const PET_CENTER_X = PET_LEFT + PET_SIZE / 2;
export const STACK_W = 240;
/** 负值：吃掉头像盒底的透明留白（身体约在 96 盒的 74px 处），贴到视觉脚边。 */
export const PET_TO_INPUT = -8;
/** 插件是辅路径，比原先 50px 收一档。 */
export const DOCK_SIZE = 40;
export const DOCK_GAP = 14;

export function quickStackTop(petY: number): number {
  return petY + PET_SIZE + PET_TO_INPUT;
}

export function quickStackLeft(): number {
  return PET_CENTER_X - STACK_W / 2;
}
