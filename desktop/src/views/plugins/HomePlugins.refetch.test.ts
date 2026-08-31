// @vitest-environment happy-dom
// 摊开/恢复工作面「打开即重拉」（走查 B2/M3 修复）：restoreSurface 按 manifest [[panel]] open
// 声明直调重拉当前面板；无 open 的对象面板（detail/matdoc）不重拉。
import "fake-indexeddb/auto";
import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

let brainHandler: ((e: any) => void) | null = null;
const panelActionMock = vi.fn((..._a: any[]) => Promise.resolve());
vi.mock("../../lib/brain", () => ({
  onBrainEvent: vi.fn((cb: any) => { brainHandler = cb; return Promise.resolve(() => {}); }),
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
  getCurrentPanel: vi.fn(() => Promise.resolve(null)),
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
vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn(() => Promise.resolve(null)) }));
vi.mock("@tauri-apps/api/window", () => ({
  getCurrentWindow: () => ({ onFocusChanged: vi.fn(() => Promise.resolve(() => {})) }),
}));

import HomePlugins from "./HomePlugins.vue";

function mountPage() {
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

function firePanel(panel: string, data: Record<string, unknown> = {}) {
  brainHandler!({ kind: "panel", payload: { panel, title: panel, schema: null, data } });
}

describe("摊开/恢复打开即重拉", () => {
  it("当前面板有 open 声明（board → list）→ restoreSurface 重拉，surface 带 panel:zimeiti", async () => {
    const w = mountPage();
    await flushPromises();
    firePanel("zimeiti:board", { rows: [{ id: "t1" }, { id: "t2" }] });
    await flushPromises();
    panelActionMock.mockClear();
    (w.vm as any).restoreSurface();
    await flushPromises();
    expect(panelActionMock).toHaveBeenCalledWith("zimeiti.list", {}, undefined, "panel:zimeiti");
  });

  it("对象面板无 open 声明（detail）→ restoreSurface 不重拉", async () => {
    const w = mountPage();
    await flushPromises();
    firePanel("zimeiti:detail", { rows: [{ id: "t1", title: "选题一" }] });
    await flushPromises();
    panelActionMock.mockClear();
    (w.vm as any).restoreSurface();
    await flushPromises();
    expect(panelActionMock).not.toHaveBeenCalled();
  });
});
