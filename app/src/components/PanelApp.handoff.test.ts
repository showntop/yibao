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

  it("handoff 期点标题栏团子开浮层,mini 输入直问大脑;收起清空残稿", async () => {
    const w = mountApp();
    await flushPromises();
    firePanel("coding:studio");
    await nextTick();
    await w.find(".titlebar .pet").trigger("click");
    expect(w.find(".ask-row").exists()).toBe(true);
    await w.find(".ask-input").setValue("这个报错什么意思");
    await w.find(".ask-send").trigger("click");
    expect(runInputMock).toHaveBeenCalledWith("这个报错什么意思");
    expect(w.find(".t-row.user").text()).toContain("这个报错什么意思");
    // 收起清空残稿,重开不留
    await w.find(".thread-x").trigger("click");
    await w.find(".titlebar .pet").trigger("click");
    expect((w.find(".ask-input").element as HTMLInputElement).value).toBe("");
  });

  it("非 handoff 时无 mini 输入(主 InputBar 在场,不需要逃生口)", async () => {
    const w = mountApp();
    await flushPromises();
    firePanel("toolbox:main");
    await nextTick();
    expect(w.find(".ask-row").exists()).toBe(false);
  });

  it("随迁:进 coding 瞬间取走译宝条草稿,postToIframe handoff-draft;空稿不触发", async () => {
    const takeDraft = vi.fn(() => "草稿文字");
    const postToIframe = vi.fn();
    mount(PanelApp, {
      global: {
        stubs: {
          SchemaPanel: { template: "<div />" },
          WebviewPanel: { template: "<div />", setup(_: any, { expose }: any) { expose({ postToIframe }); return {}; } },
          InputBar: { template: "<div />", setup(_: any, { expose }: any) { expose({ takeDraft, focus() {}, insertText() {} }); return {}; } },
          Avatar: { template: "<button class='avatar-stub' v-bind='$attrs' />" },
          YbIcon: { template: "<i />" },
        },
      },
    });
    await flushPromises();
    firePanel("toolbox:main"); // 先落在非 coding:不触发
    await flushPromises();
    expect(takeDraft).not.toHaveBeenCalled();
    firePanel("coding:studio");
    await flushPromises();
    expect(takeDraft).toHaveBeenCalledTimes(1);
    expect(postToIframe).toHaveBeenCalledWith({ type: "handoff-draft", text: "草稿文字" });
  });

  it("takeDraft 返回空串不投递", async () => {
    const takeDraft = vi.fn(() => "");
    const postToIframe = vi.fn();
    mount(PanelApp, {
      global: {
        stubs: {
          SchemaPanel: { template: "<div />" },
          WebviewPanel: { template: "<div />", setup(_: any, { expose }: any) { expose({ postToIframe }); return {}; } },
          InputBar: { template: "<div />", setup(_: any, { expose }: any) { expose({ takeDraft, focus() {}, insertText() {} }); return {}; } },
          Avatar: { template: "<button class='avatar-stub' v-bind='$attrs' />" },
          YbIcon: { template: "<i />" },
        },
      },
    });
    await flushPromises();
    firePanel("coding:studio");
    await flushPromises();
    expect(takeDraft).toHaveBeenCalledTimes(1); // 取稿照跑(空稿也要清持久化副本)
    expect(postToIframe).not.toHaveBeenCalled();
  });
});
