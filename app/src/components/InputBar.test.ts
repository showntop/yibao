// @vitest-environment happy-dom
// takeDraft(handoff 草稿随迁):取走草稿=清文本+清持久化副本(写穿,不等 300ms debounce);空草稿返回 ""
// 注:brief 原稿对 textarea.value 的断言是同步的,但 v-model 的 model→DOM 方向走重渲染
// (nextTick),故清屏断言前补 await nextTick()(语义不变:文本已清)
import { mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const setDraftMock = vi.fn();
vi.mock("../state/store", () => ({
  sessionStore: {
    conversation: {
      getActiveConversationId: () => "c1",
      getUIState: () => ({ draft: "" }),
      setDraft: (...a: any[]) => setDraftMock(...a),
    },
  },
}));
vi.mock("../lib/brain", () => ({
  panelAction: vi.fn(() => Promise.resolve({})),
  onBrainEvent: vi.fn(() => Promise.resolve(() => {})),
}));
vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn(() => Promise.resolve(null)) }));

import InputBar from "./InputBar.vue";

describe("InputBar takeDraft", () => {
  beforeEach(() => { vi.useFakeTimers(); setDraftMock.mockClear(); });
  afterEach(() => vi.useRealTimers());

  it("有稿:返回 trim 后文本,清空文本与持久化副本(写穿立即落库)", async () => {
    const w = mount(InputBar, { global: { stubs: { YbIcon: { template: "<i />" } } } });
    const ta = w.find("textarea");
    (ta.element as HTMLTextAreaElement).value = "  帮我修这个  ";
    await ta.trigger("input");
    const d = (w.vm as any).takeDraft();
    expect(d).toBe("帮我修这个");
    await nextTick(); // v-model 清屏走重渲染
    expect((ta.element as HTMLTextAreaElement).value).toBe("");
    // 写穿:不 advance timers,随迁即清立即落库
    expect(setDraftMock).toHaveBeenCalledWith("c1", "");
  });

  it("takeDraft 先清掉在途的 debounce:旧稿不落库", async () => {
    const w = mount(InputBar, { global: { stubs: { YbIcon: { template: "<i />" } } } });
    const ta = w.find("textarea");
    (ta.element as HTMLTextAreaElement).value = "旧稿";
    await ta.trigger("input"); // watch → persistDraft("旧稿") 排队 300ms
    const d = (w.vm as any).takeDraft(); // 掐死在途 debounce + 写穿 ""
    expect(d).toBe("旧稿");
    vi.advanceTimersByTime(300);
    expect(setDraftMock).toHaveBeenCalledTimes(1); // 只有写穿那一次,旧稿不落库
    expect(setDraftMock).toHaveBeenCalledWith("c1", "");
  });

  it("空稿:返回空串,不动持久化", () => {
    const w = mount(InputBar, { global: { stubs: { YbIcon: { template: "<i />" } } } });
    expect((w.vm as any).takeDraft()).toBe("");
    vi.advanceTimersByTime(300);
    expect(setDraftMock).not.toHaveBeenCalledWith("c1", "");
  });
});
