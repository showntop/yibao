// @vitest-environment happy-dom
// Avatar idle 生命感动效：随机眨眼（既有）+ 偶发小动作（sway 身体轻摇 / wiggle 天线轻晃）。
// 小动作是 JS 随机稀疏触发（8–20s）、单次播放 900ms 后摘 class 再排下一次；非 idle/卸载即清理。
//
// 时序控制：mock Math.random=0 → 间隔固定取最小 8000ms、动作固定 sway。
// 不能一步 advance 20000：会让「触发(+8~20s)→摘除(+900ms)→重排」在同一推进内走完，断言扑空。
import { mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@tauri-apps/api/window", () => ({
  getCurrentWindow: vi.fn(() => ({
    outerPosition: vi.fn(() => Promise.resolve({ x: 0, y: 0 })),
    scaleFactor: vi.fn(() => Promise.resolve(1)),
    setPosition: vi.fn(() => Promise.resolve()),
  })),
  PhysicalPosition: class {},
}));

import Avatar from "./Avatar.vue";

const MIN_GAP = 8000; // random=0 时的最小间隔

describe("Avatar idle 偶发小动作", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.spyOn(Math, "random").mockReturnValue(0); // 间隔 8000ms、动作固定 sway
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("idle 时触发 sway 小动作并挂到根元素 class", async () => {
    const w = mount(Avatar, { props: { state: "idle" } });
    expect(w.classes()).not.toContain("sway");
    vi.advanceTimersByTime(MIN_GAP);
    await w.vm.$nextTick();
    expect(w.classes()).toContain("sway");
  });

  it("小动作 900ms 后摘 class 并重新调度下一次", async () => {
    const w = mount(Avatar, { props: { state: "idle" } });
    vi.advanceTimersByTime(MIN_GAP);
    await w.vm.$nextTick();
    expect(w.classes()).toContain("sway");

    vi.advanceTimersByTime(900); // 播放完摘除
    await w.vm.$nextTick();
    expect(w.classes()).not.toContain("sway");

    vi.advanceTimersByTime(MIN_GAP); // 重排的下一次
    await w.vm.$nextTick();
    expect(w.classes()).toContain("sway");
  });

  it("离开 idle 状态立即清除小动作与待触发定时器", async () => {
    const w = mount(Avatar, { props: { state: "idle" } });
    await w.setProps({ state: "think" }); // 清理 pending 定时器
    vi.advanceTimersByTime(60000);        // 即使时间走完也不再触发
    expect(w.classes()).not.toContain("sway");
    expect(w.classes()).not.toContain("wiggle");
  });

  it("重进 idle 重新开始调度", async () => {
    const w = mount(Avatar, { props: { state: "work" } });
    vi.advanceTimersByTime(60000);
    expect(w.classes()).not.toContain("sway"); // 非 idle 全程不触发
    await w.setProps({ state: "idle" });
    vi.advanceTimersByTime(MIN_GAP);
    await w.vm.$nextTick();
    expect(w.classes()).toContain("sway");
  });

  it("卸载时清理定时器（时间继续走无残留副作用）", () => {
    const w = mount(Avatar, { props: { state: "idle" } });
    w.unmount();
    vi.advanceTimersByTime(60000);
    expect(vi.getTimerCount()).toBe(0); // blink + quirk 两组定时器都已清
  });
});
