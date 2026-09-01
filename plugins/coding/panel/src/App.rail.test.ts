// @vitest-environment happy-dom
// 左栏常驻抽屉(验收样式迭代):会话列表默认收起,stations 区(会话+Composer)铺满全宽;
// ☰ 常显开抽屉(罩层+左滑出);点行(加入/聚焦)或罩层即收。窄窗语义不变(单工位 + review 徽按钮)。
import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

vi.mock("./lib/bridge", () => ({
  hasBridge: true,
  invoke: vi.fn((m: string) => {
    if (m === "coding.list" || m === "coding.sessions")
      return Promise.resolve({ sessions: [{ id: "s1demo00", cwd: "/tmp/proj", prompt: "修 bug", agent: "cc", created_at: 0 }] });
    if (m === "coding.perm_pending") return Promise.resolve({ pending: [] });
    if (m === "coding.history") return Promise.resolve({ messages: [] });
    return Promise.resolve({});
  }),
  onInit: vi.fn(),
  onHostMessage: vi.fn(),
  emitPanelEvent: vi.fn(),
}));

import App from "./App.vue";

describe("左栏常驻抽屉", () => {
  it("默认收起:无 .rail,stations 全宽;☰ 常显", async () => {
    const w = mount(App);
    await flushPromises();
    expect(w.find(".rail").exists()).toBe(false);
    expect(w.find(".tab-btn").exists()).toBe(true);
  });

  it("☰ 开抽屉(罩层+滑出);点行加入工位并自动收", async () => {
    const w = mount(App);
    await flushPromises();
    await w.find(".tab-btn").trigger("click");
    expect(w.find(".rail-drawer").exists()).toBe(true);
    expect(w.find(".rail-mask").exists()).toBe(true);
    const row = w.find(".rail-row");
    expect(row.text()).toContain("修 bug");
    await row.trigger("click"); // 未绑行 = 加入工位,顺带收抽屉
    await flushPromises();
    expect(w.find(".rail").exists()).toBe(false); // 抽屉已收(加入链路由 stations store 测试兜底)
  });

  it("点罩层收抽屉", async () => {
    const w = mount(App);
    await flushPromises();
    await w.find(".tab-btn").trigger("click");
    await w.find(".rail-mask").trigger("click");
    expect(w.find(".rail").exists()).toBe(false);
  });
});
