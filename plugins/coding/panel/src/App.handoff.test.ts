// @vitest-environment happy-dom
// handoff 草稿随迁（handoff-draft 宿主消息 → 聚焦工位 Composer 填稿并聚焦）
import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

let hostMsg: ((m: any) => void) | null = null;
vi.mock("./lib/bridge", () => ({
  hasBridge: true,
  invoke: vi.fn((m: string) => {
    if (m === "coding.list" || m === "coding.sessions") return Promise.resolve({ sessions: [] });
    if (m === "coding.perm_pending") return Promise.resolve({ pending: [] });
    if (m === "coding.history") return Promise.resolve({ messages: [] });
    return Promise.resolve({});
  }),
  onInit: vi.fn(),
  onHostMessage: vi.fn((cb: any) => { hostMsg = cb; }),
  emitPanelEvent: vi.fn(),
}));

import App from "./App.vue";

describe("handoff 草稿随迁", () => {
  it("handoff-draft → 聚焦工位 Composer 填稿并聚焦", async () => {
    const w = mount(App, { attachTo: document.body }); // 挂进文档:focus() 要求节点 connected 才更新 activeElement
    await flushPromises();
    hostMsg!({ type: "handoff-draft", text: "继续上午的改造" });
    await flushPromises();
    const ta = w.find(".station.focused textarea#prompt").element as HTMLTextAreaElement;
    expect(ta.value).toBe("继续上午的改造");
    expect(document.activeElement).toBe(ta);
  });
});
