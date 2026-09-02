// @vitest-environment happy-dom
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import HomeGapCard from "./HomeGapCard.vue";

const gap = {
  through: "脚本",
  available: ["选题", "证据", "脚本"],
  missing: ["分镜", "素材", "配音"],
  note: "可做到脚本；分镜起缺能力，安装对应 provider 后可继续",
};

describe("HomeGapCard", () => {
  it("缺能力段为主体、可达段弱化、降级建议一句", () => {
    const wrapper = mount(HomeGapCard, { props: { gap } });
    expect(wrapper.text()).toContain("能力边界 · 可做到脚本");
    expect(wrapper.findAll(".gap-stage.miss").map((n) => n.text())).toEqual(["分镜", "素材", "配音"]);
    expect(wrapper.findAll(".gap-stage.ok").map((n) => n.text())).toEqual(["选题", "证据", "脚本"]);
    expect(wrapper.text()).toContain("分镜起缺能力");
  });

  it("信息卡不发明交互：没有按钮", () => {
    const wrapper = mount(HomeGapCard, { props: { gap } });
    expect(wrapper.find("button").exists()).toBe(false);
  });
});
