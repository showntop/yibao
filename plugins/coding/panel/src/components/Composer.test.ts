// @vitest-environment happy-dom
// Composer fillDraft（handoff 草稿随迁）：空稿直填并聚焦；残稿换行追加（不覆盖用户输入）
import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

vi.mock("../lib/bridge", () => ({
  hasBridge: true,
  invoke: vi.fn(() => Promise.resolve({})),
  onInit: vi.fn(),
  onHostMessage: vi.fn(),
  emitPanelEvent: vi.fn(),
}));

import Composer from "./Composer.vue";

describe("Composer fillDraft", () => {
  it("空稿直填并聚焦；残稿换行追加", () => {
    const w = mount(Composer, { props: { busy: false, cwd: "/x", onStop: vi.fn() }, attachTo: document.body });
    const ta = w.find("textarea#prompt").element as HTMLTextAreaElement;
    (w.vm as any).fillDraft("帮我修 bug");
    expect(ta.value).toBe("帮我修 bug");
    expect(document.activeElement).toBe(ta);
    (w.vm as any).fillDraft("顺便补测试");
    expect(ta.value).toBe("帮我修 bug\n顺便补测试");
  });
});
