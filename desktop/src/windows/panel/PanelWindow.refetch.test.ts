// @vitest-environment happy-dom
// 面板窗「打开即重拉」（走查 M3 修复）：挂载补拉缓存后按 manifest [[panel]] open 声明重拉一次，
// 治「浮层有数据、独立窗口空」；无 open 的对象面板（detail）不重拉。
import "fake-indexeddb/auto";
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

const panelActionMock = vi.fn((..._a: any[]) => Promise.resolve());
beforeEach(() => {
  panelActionMock.mockClear();
  cachedPanel = null;
});
vi.mock("../../lib/brain", () => ({
  onBrainEvent: vi.fn((_cb: any) => Promise.resolve(() => {})),
  onPendingConfirms: vi.fn(() => () => {}),
  openHomeWindow: vi.fn(() => Promise.resolve()),
  panelAction: (...a: any[]) => panelActionMock(...a),
  sendConfirmBatch: vi.fn(() => Promise.resolve()),
  runInput: vi.fn(() => Promise.resolve()),
  voiceStart: vi.fn(() => Promise.resolve()),
  interrupt: vi.fn(() => Promise.resolve()),
  reportPanelContext: vi.fn(() => Promise.resolve()),
  setSurface: vi.fn(),
  canRememberTool: vi.fn(() => false),
  rememberLabelForTool: vi.fn(() => ""),
  closePanelWindow: vi.fn(() => Promise.resolve()),
  listPlugins: vi.fn(() =>
    Promise.resolve([
      {
        id: "zimeiti",
        name: "内容创作",
        panels: [
          { name: "board", label: "选题看板", open: "list" },
          { name: "materials", label: "素材库", open: "mat_list" },
        ],
      },
    ]),
  ),
}));

// get_current_panel 的缓存载荷由用例注入
let cachedPanel: Record<string, unknown> | null = null;
vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn((cmd: string) => Promise.resolve(cmd === "get_current_panel" ? cachedPanel : null)),
}));
vi.mock("@tauri-apps/api/event", () => ({
  listen: vi.fn(() => Promise.resolve(() => {})),
  emit: vi.fn(() => Promise.resolve()),
}));
vi.mock("@tauri-apps/api/window", () => ({
  getCurrentWindow: () => ({ onFocusChanged: vi.fn(() => Promise.resolve(() => {})) }),
}));

import PanelApp from "./PanelWindow.vue";

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

describe("面板窗打开即重拉", () => {
  it("缓存面板有 open 声明（materials → mat_list）→ 挂载后重拉", async () => {
    cachedPanel = {
      panel: "zimeiti:materials",
      title: "内容创作 · 素材库",
      schema: { version: 1, type: "list" },
      webview: null,
      data: { rows: [] }, // 陈旧快照：库里其实已有素材
    };
    mountApp();
    await flushPromises();
    expect(panelActionMock).toHaveBeenCalledWith("zimeiti.mat_list", {}, undefined, "panel:zimeiti");
  });

  it("对象面板无 open 声明（detail）→ 挂载后不重拉", async () => {
    cachedPanel = {
      panel: "zimeiti:detail",
      title: "内容创作 · 选题详情",
      schema: { version: 1, type: "detail" },
      webview: null,
      data: { rows: [{ id: "t1", title: "选题一" }] },
    };
    mountApp();
    await flushPromises();
    expect(panelActionMock).not.toHaveBeenCalled();
  });

  it("无缓存（占位页）→ 不重拉", async () => {
    cachedPanel = null;
    mountApp();
    await flushPromises();
    expect(panelActionMock).not.toHaveBeenCalled();
  });
});
