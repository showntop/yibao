// @vitest-environment happy-dom
// Composer fillDraft（handoff 草稿随迁）：空稿直填并聚焦；残稿换行追加（不覆盖用户输入）
import { flushPromises, mount } from "@vue/test-utils";
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

describe("Composer 复刻结构", () => {
  it("composer-bar 容器 + 输入行内嵌发送钮;busy 期中断钮现身于其左", async () => {
    const w = mount(Composer, { props: { busy: false, cwd: "/x", onStop: vi.fn() } });
    expect(w.find(".composer-bar").exists()).toBe(true);
    expect(w.find(".composer-row textarea#prompt").exists()).toBe(true);
    expect(w.find(".composer-row #send.cbtn.main").exists()).toBe(true);
    expect(w.find("#stop").exists()).toBe(false);
    await w.setProps({ busy: true });
    expect(w.find(".composer-row #stop.cbtn.stop").exists()).toBe(true);
  });

  it("发送回归:点击 #send 上抛 send(文本+refs)", async () => {
    const w = mount(Composer, { props: { busy: false, cwd: "/x", onStop: vi.fn() } });
    const ta = w.find("textarea#prompt");
    (ta.element as HTMLTextAreaElement).value = "hello";
    await w.find("#send").trigger("click");
    expect(w.emitted("send")?.[0]).toEqual(["hello", []]);
  });

  it("@ 补全回归:输入 @ 触发文件菜单(coding.files)", async () => {
    const { invoke } = await import("../lib/bridge");
    (invoke as ReturnType<typeof vi.fn>).mockResolvedValue({ files: [{ rel: "src/main.ts" }] });
    const w = mount(Composer, { props: { busy: false, cwd: "/x", onStop: vi.fn() } });
    const ta = w.find("textarea#prompt");
    (ta.element as HTMLTextAreaElement).value = "看下 @src";
    await ta.trigger("input");
    await flushPromises();
    expect(w.find(".at-menu").exists()).toBe(true);
    expect(w.find(".at-item").text()).toBe("src/main.ts");
  });
});
