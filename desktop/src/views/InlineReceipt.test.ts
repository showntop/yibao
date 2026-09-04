// @vitest-environment happy-dom
// InlineReceipt 回执动作（receipt.actions 合同）：文件型产物结果在回执卡上给
// 「在 Finder 显示/打开」本机按钮（最多 2 个），点击走 panelAction 的 native: 旁路
// （宿主本地执行，不过 sidecar、无闸门）；无 receipt 时保持「忽略/展开」原样。
import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import InlineReceipt from "./InlineReceipt.vue";
import { panelAction } from "../lib/brain";

vi.mock("../lib/brain", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/brain")>()),
  panelAction: vi.fn(() => Promise.resolve()),
}));

const base = { provider: "zimeiti", title: "zimeiti · detail", summary: "已渲染 v3" };

describe("InlineReceipt 回执动作", () => {
  it("无 receipt：只有 忽略/展开", () => {
    const w = mount(InlineReceipt, { props: base });
    expect(w.findAll(".ir-actions button").map((b) => b.text())).toEqual(["忽略", "展开"]);
  });

  it("带 actions：渲染本机动作按钮，点击走 native: 旁路", async () => {
    const w = mount(InlineReceipt, {
      props: {
        ...base,
        receipt: {
          actions: [
            { label: "在 Finder 显示", kind: "reveal", path: "/tmp/v3.mp4" },
            { label: "播放", kind: "open", path: "/tmp/v3.mp4" },
          ],
        },
      },
    });
    const labels = w.findAll(".ir-actions button").map((b) => b.text());
    expect(labels).toEqual(["在 Finder 显示", "播放", "忽略", "展开"]);
    await w.findAll(".ir-actions button")[1].trigger("click");
    expect(panelAction).toHaveBeenCalledWith("native:open", { path: "/tmp/v3.mp4" });
  });

  it("actions 超 2 个截断", () => {
    const w = mount(InlineReceipt, {
      props: {
        ...base,
        receipt: {
          actions: [
            { label: "a", kind: "reveal", path: "/a" },
            { label: "b", kind: "open", path: "/b" },
            { label: "c", kind: "open", path: "/c" },
          ],
        },
      },
    });
    expect(w.findAll(".ir-actions button")).toHaveLength(4); // 2 动作 + 忽略/展开
  });
});
