// @vitest-environment happy-dom
// App 挂载回归(R4 真机验收实测卡死):壳的工位函数 ref 回调里有无条件响应式写入
// (refsVersion++),函数 ref 每渲染被重调 → 写入 → 再渲染 → 无限渲染风暴,
// 面板 WebView JS 线程打满、全程不可点击。
// 修复前:dev 版 Vue 抛「Maximum recursive updates exceeded」(unhandled rejection,
// vitest 判负);生产版(打包 runtime)无此护栏 → 无限风暴。修复后两拍内稳定。
import { flushPromises, mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { describe, expect, it, vi } from "vitest";
import App from "./App.vue";

vi.mock("./lib/bridge", () => ({
  hasBridge: true,
  invoke: vi.fn((m: string) => {
    if (m === "coding.list" || m === "coding.sessions") return Promise.resolve({ sessions: [] });
    if (m === "coding.perm_pending") return Promise.resolve({ pending: [] });
    if (m === "coding.history") return Promise.resolve({ messages: [] });
    return Promise.resolve({});
  }),
  onInit: vi.fn(),
  onHostMessage: vi.fn(),
  emitPanelEvent: vi.fn(),
}));

describe("App 挂载", () => {
  it("挂载后渲染循环收敛——ref 回调不得携带响应式写入", async () => {
    const w = mount(App);
    await flushPromises(); // 修复前渲染风暴永不排空(此处挂起至超时)
    await nextTick();
    expect(w.find(".shell").exists()).toBe(true);
    expect(w.find(".station").exists()).toBe(true);
  }, 3000);
});
