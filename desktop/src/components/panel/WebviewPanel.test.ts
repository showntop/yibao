// @vitest-environment happy-dom
// postToIframe 就绪暂存:iframe load 前只存最后一条,load 时(init 之后)补发
// 注:happy-dom 仅在 iframe 挂载进 document 时才创建 contentWindow,故 mount 需 attachTo
import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

vi.mock("../../lib/brain", () => ({
  onBrainEvent: vi.fn(() => Promise.resolve(() => {})),
  panelAction: vi.fn(() => Promise.resolve({})),
}));
vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn(() => Promise.resolve(null)) }));

import WebviewPanel from "./WebviewPanel.vue";

const PROPS = { panel: "coding:studio", url: "yibao-plugin://coding/panel/dist/index.html", v: 1, data: {} };

describe("WebviewPanel postToIframe 就绪暂存", () => {
  it("load 前暂存,load 后补发;之后直发", async () => {
    const w = mount(WebviewPanel, { props: PROPS, attachTo: document.body });
    const iframe = w.find("iframe").element as HTMLIFrameElement;
    const postSpy = vi.spyOn(iframe.contentWindow!, "postMessage");
    (w.vm as any).postToIframe({ type: "handoff-draft", text: "甲" });
    expect(postSpy).not.toHaveBeenCalled();
    await w.find("iframe").trigger("load");
    expect(postSpy).toHaveBeenCalledWith(
      expect.objectContaining({ src: "yibao-host", type: "handoff-draft", text: "甲" }),
      "*",
    );
    postSpy.mockClear();
    (w.vm as any).postToIframe({ type: "handoff-draft", text: "乙" });
    expect(postSpy).toHaveBeenCalledTimes(1);
  });

  it("load 前两次暂存只补发最后一条", async () => {
    const w = mount(WebviewPanel, { props: PROPS, attachTo: document.body });
    const iframe = w.find("iframe").element as HTMLIFrameElement;
    const postSpy = vi.spyOn(iframe.contentWindow!, "postMessage");
    (w.vm as any).postToIframe({ type: "handoff-draft", text: "甲" });
    (w.vm as any).postToIframe({ type: "handoff-draft", text: "乙" });
    await w.find("iframe").trigger("load");
    expect(postSpy).not.toHaveBeenCalledWith(
      expect.objectContaining({ type: "handoff-draft", text: "甲" }),
      "*",
    );
    expect(postSpy).toHaveBeenCalledWith(
      expect.objectContaining({ src: "yibao-host", type: "handoff-draft", text: "乙" }),
      "*",
    );
  });
});
