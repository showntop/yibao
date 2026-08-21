// @vitest-environment happy-dom
// 大窗 handoff(panel-input-modes spec §C):插件页激活面板的 input ∈ {handoff, none} → 底部 bench-bar 让位;
// 缺省/inherit 不动;切走恢复。逃生口 = 顶部「主屏」tab(本组件不加新元素)。
import "fake-indexeddb/auto";
import { flushPromises, mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { describe, expect, it, vi } from "vitest";

let brainHandler: ((e: any) => void) | null = null;
vi.mock("../lib/brain", () => ({
  onBrainEvent: vi.fn((cb: any) => { brainHandler = cb; return Promise.resolve(() => {}); }),
  onPendingConfirms: vi.fn(() => () => {}),
  openHomeWindow: vi.fn(() => Promise.resolve()),
  panelAction: vi.fn(() => Promise.resolve({})),
  sendConfirmBatch: vi.fn(() => Promise.resolve()),
  runInput: vi.fn(() => Promise.resolve()),
  voiceStart: vi.fn(() => Promise.resolve()),
  interrupt: vi.fn(() => Promise.resolve()),
  reportPanelContext: vi.fn(() => Promise.resolve()),
  setSurface: vi.fn(),
  canRememberSkill: vi.fn(() => false),
  rememberLabelForSkill: vi.fn(() => ""),
}));
// list_plugins 须回 [](回 null 会让列表模板 plugins.length 抛错);get_current_panel 回 null(无缓存)
vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn((cmd: string) => Promise.resolve(cmd === "list_plugins" ? [] : null)),
}));
vi.mock("@tauri-apps/api/window", () => ({
  getCurrentWindow: () => ({ onFocusChanged: vi.fn(() => Promise.resolve(() => {})) }),
}));

import HomePlugins from "./HomePlugins.vue";

function mountPage() {
  // HomePlugins 子组件较重,桩掉与本特性无关的;先挂载读模板,缺的桩按报错补齐
  return mount(HomePlugins, {
    global: {
      stubs: {
        SchemaPanel: { template: "<div />" },
        WebviewPanel: { template: "<div />" },
        InputBar: { template: "<div />", setup(_: any, { expose }: any) { expose({ focus() {}, insertText() {} }); return {}; } },
        Avatar: { template: "<button v-bind='$attrs' />" },
        YbIcon: { template: "<i />" },
      },
    },
  });
}

function firePanel(panel: string, input?: string) {
  brainHandler!({
    kind: "panel",
    payload: { panel, title: panel, schema: null, webview: { url: "yibao-plugin://x/panel/dist/index.html", v: 1 }, data: {}, ...(input ? { input } : {}) },
  });
}

describe("大窗 handoff", () => {
  it("input=handoff → bench-bar 让位;无声明不动;切走恢复", async () => {
    const w = mountPage();
    await flushPromises();
    firePanel("coding:studio"); // 无声明
    await nextTick();
    expect(w.find(".bench-bar").exists()).toBe(true);
    firePanel("coding:studio", "handoff");
    await nextTick();
    expect(w.find(".bench-bar").exists()).toBe(false);
    firePanel("toolbox:main"); // 切走
    await nextTick();
    expect(w.find(".bench-bar").exists()).toBe(true);
  });

  it("桌上工位：页头让位，面板留在原树（不搬 iframe）", async () => {
    const w = mount(HomePlugins, {
      props: { scene: true },
      global: {
        stubs: {
          SchemaPanel: { template: "<div class='schema-stub' />" },
          WebviewPanel: { template: "<div class='webview-stub' />" },
          InputBar: { template: "<div />", setup(_: any, { expose }: any) { expose({ focus() {}, insertText() {} }); return {}; } },
          Avatar: { template: "<button v-bind='$attrs' />" },
          YbIcon: { template: "<i />" },
        },
      },
    });
    await flushPromises();
    firePanel("notes:list");
    await nextTick();
    expect(w.find(".page-head").exists()).toBe(false);
    expect(w.find(".bench").exists()).toBe(false);
    expect(w.find(".panel-grow").exists()).toBe(true);
    expect(w.text()).not.toContain("当前任务");
    w.unmount();
  });
});
