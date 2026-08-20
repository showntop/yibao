// @vitest-environment happy-dom
// AssistantBubble 思考折叠块:流式期自动展开看直播,done 自动收(省屏);
// 用户手动开合后钉住不再干预(程序性开合不得误存为用户偏好——原生 toggle 事件在
// 绑定驱动下也异步触发,只能接管 summary 点击记录)。
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import AssistantBubble from "./AssistantBubble.vue";

function makeItem(done: boolean) {
  return { type: "assistant" as const, raw: "答", thinking: ["想一想"], done };
}
const thinkOpen = (w: ReturnType<typeof mount>) =>
  w.find("details.think").attributes("open") !== undefined;

describe("AssistantBubble 思考折叠", () => {
  it("流式期自动展开,done 自动收", async () => {
    const w = mount(AssistantBubble, { props: { item: makeItem(false) } });
    expect(w.find("details.think").exists()).toBe(true);
    expect(thinkOpen(w)).toBe(true);
    await w.setProps({ item: makeItem(true) });
    expect(thinkOpen(w)).toBe(false);
  });

  it("用户手动开合后钉住:done 前点开,done 后不收;再点收回", async () => {
    const w = mount(AssistantBubble, { props: { item: makeItem(false) } });
    await w.setProps({ item: makeItem(true) }); // done → 自动收
    expect(thinkOpen(w)).toBe(false);
    await w.find("details.think summary").trigger("click"); // 用户点开
    expect(thinkOpen(w)).toBe(true);
    await w.find("details.think summary").trigger("click"); // 用户收回
    expect(thinkOpen(w)).toBe(false);
  });

  it("无思考不渲染折叠块;markdown 正文照常", () => {
    const w = mount(AssistantBubble, {
      props: { item: { type: "assistant" as const, raw: "**加粗**", thinking: [], done: true } },
    });
    expect(w.find("details.think").exists()).toBe(false);
    expect(w.find(".mdc strong").exists()).toBe(true);
  });
});
