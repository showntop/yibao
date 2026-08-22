import { describe, expect, it } from "vitest";
import {
  DOCK_GAP,
  DOCK_SIZE,
  PET_CENTER_X,
  PET_SIZE,
  PET_TO_INPUT,
  STACK_W,
  quickStackLeft,
  quickStackTop,
} from "./quick-dock";

describe("quick stack", () => {
  it("栈顶在团子脚下，水平与团子同一中轴", () => {
    expect(quickStackTop(100)).toBe(100 + PET_SIZE + PET_TO_INPUT);
    expect(quickStackLeft() + STACK_W / 2).toBe(PET_CENTER_X);
  });

  it("三颗插件排得下且不超出输入条宽度", () => {
    const rowW = DOCK_SIZE * 3 + DOCK_GAP * 2;
    expect(rowW).toBeLessThanOrEqual(STACK_W);
    expect(quickStackLeft() + STACK_W).toBeLessThanOrEqual(320);
    expect(quickStackLeft()).toBeGreaterThanOrEqual(0);
  });
});
