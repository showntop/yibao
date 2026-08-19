// @vitest-environment happy-dom
// 输入条 handoff(spec 2026-08-19-input-handoff-design.md §A):
// coding:studio 打开 → bench-bar(团子+chip+InputBar)整行让位,团子搬标题栏;切走原样恢复
import "fake-indexeddb/auto"; // PanelApp 经 state/store 触碰 indexedDB,防 unhandled rejection(同 persist-engine.test.ts)
import { flushPromises, mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { describe, expect, it, vi } from "vitest";

let brainHandler: ((e: any) => void) | null = null;
const runInputMock = vi.fn((..._a: any[]) => Promise.resolve());
vi.mock("../lib/brain", () => ({
  onBrainEvent: vi.fn((cb: any) => { brainHandler = cb; return Promise.resolve(() => {}); }),
  onPendingConfirms: vi.fn(() => () => {}),
  openHomeWindow: vi.fn(() => Promise.resolve()),
  panelAction: vi.fn(() => Promise.resolve({})),
  sendConfirmBatch: vi.fn(() => Promise.resolve()),
  runInput: (...a: any[]) => runInputMock(...a),
  voiceStart: vi.fn(() => Promise.resolve()),
  interrupt: vi.fn(() => Promise.resolve()),
  reportPanelContext: vi.fn(() => Promise.resolve()),
  setSurface: vi.fn(),
  canRememberSkill: vi.fn(() => false),
  rememberLabelForSkill: vi.fn(() => ""),
}));
vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn(() => Promise.resolve(null)) }));
vi.mock("@tauri-apps/api/window", () => ({
  getCurrentWindow: () => ({ onFocusChanged: vi.fn(() => Promise.resolve(() => {})) }),
}));

import PanelApp from "./PanelApp.vue";

function mountApp() {
  return mount(PanelApp, {
    global: {
      stubs: {
        SchemaPanel: { template: "<div class='schema-stub' />" },
        WebviewPanel: { template: "<div class='webview-stub' />" },
        InputBar: { template: "<div class='inputbar-stub' />" },
        Avatar: { template: "<button class='avatar-stub' v-bind='$attrs' />" },
        YbIcon: { template: "<i />" },
      },
    },
  });
}

function firePanel(panel: string) {
  brainHandler!({
    kind: "panel",
    payload: { panel, title: panel, schema: null, webview: { url: "yibao-plugin://x/panel/dist/index.html", v: 1 }, data: {} },
  });
}

describe("输入条 handoff", () => {
  it("coding:studio 打开 → bench-bar 让位 + 团子搬标题栏;切走恢复", async () => {
    const w = mountApp();
    await flushPromises();
    expect(w.find(".bench-bar").exists()).toBe(true);
    expect(w.find(".titlebar .pet").exists()).toBe(false);
    firePanel("coding:studio");
    await nextTick();
    expect(w.find(".bench-bar").exists()).toBe(false);
    expect(w.find(".titlebar .pet").exists()).toBe(true);
    firePanel("toolbox:main");
    await nextTick();
    expect(w.find(".bench-bar").exists()).toBe(true);
    expect(w.find(".titlebar .pet").exists()).toBe(false);
  });
});
