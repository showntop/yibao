// @vitest-environment happy-dom
// PeekSurface 面板动作接线（走查 B3 修复）：探窗里的 SchemaPanel action（素材「查看」/看板卡「详情」）
// 必须落到 panelAction 直调——此前没接 @action，点了没反应。
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

const panelActionMock = vi.fn((..._a: any[]) => Promise.resolve());
beforeEach(() => panelActionMock.mockClear());
vi.mock("../lib/brain", () => ({
  onBrainEvent: vi.fn((_cb: any) => Promise.resolve(() => {})),
  panelAction: (...a: any[]) => panelActionMock(...a),
}));

import PeekSurface from "./PeekSurface.vue";

// vitest 在 desktop/ 下跑，plugins/ 在仓库根（同 SchemaPanel.test.ts 约定）
const PLUGINS_DIR = join(process.cwd(), "..", "plugins");
const materialsSchema = JSON.parse(
  readFileSync(join(PLUGINS_DIR, "zimeiti/panel/materials.schema.json"), "utf-8"),
);

function mountPeek() {
  // reduced-motion：跳过 growIn 的 el.animate（happy-dom 无完整 Web Animations）
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn(() => ({ matches: true })),
  });
  return mount(PeekSurface, {
    props: {
      panel: "zimeiti:materials",
      title: "内容创作 · 素材库",
      provider: "zimeiti",
      schema: materialsSchema,
      webview: null,
      data: {
        rows: [
          { id: "m1", title: "赚钱案例库", summary: "十条案例" },
          { id: "m2", title: "平台规则", summary: "抖音规则摘录" },
        ],
      },
    },
    global: { stubs: { YbIcon: { template: "<i />" } } },
  });
}

describe("PeekSurface 面板动作", () => {
  it("点条目「查看」→ panelAction 调 zimeiti.mat_get，$item.id 解析成行 id", async () => {
    const w = mountPeek();
    await flushPromises();
    const viewBtns = w.findAll(".card-actions button").filter((b) => b.text() === "查看");
    expect(viewBtns).toHaveLength(2);
    await viewBtns[0].trigger("click");
    expect(panelActionMock).toHaveBeenCalledWith(
      "zimeiti.mat_get",
      { id: "m1" },
      undefined,
      "panel:zimeiti",
    );
  });

  it("点第二行「删除」→ 带该行 id（绑定按行解析，不串行）", async () => {
    const w = mountPeek();
    await flushPromises();
    const delBtns = w.findAll(".card-actions button").filter((b) => b.text() === "删除");
    await delBtns[1].trigger("click");
    expect(panelActionMock).toHaveBeenCalledWith(
      "zimeiti.mat_delete",
      { id: "m2" },
      undefined,
      "panel:zimeiti",
    );
  });
});
