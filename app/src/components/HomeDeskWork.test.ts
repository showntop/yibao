// @vitest-environment happy-dom
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import HomeDeskWork from "./HomeDeskWork.vue";

describe("HomeDeskWork", () => {
  it("署名是委派不是换房间", () => {
    const w = mount(HomeDeskWork, {
      props: { plugin: "coding", title: "studio", objectTitle: "改登录", busy: true, kind: "worker", lendEar: true },
    });
    expect(w.text()).toContain("译宝请来");
    expect(w.text()).toContain("改登录");
    expect(w.text()).toContain("正在干");
    expect(w.text()).not.toContain("当前任务");
    expect(w.text()).not.toContain("返回");
    expect(w.get("#yb-desk-work-body").exists()).toBe(true);
  });

  it("译宝脑工位不举行请来仪式", () => {
    const w = mount(HomeDeskWork, {
      props: { plugin: "notes", title: "闪念盘", objectTitle: "闪念列表", kind: "host" },
    });
    expect(w.text()).toContain("闪念盘");
    expect(w.text()).toContain("收起");
    expect(w.text()).not.toContain("译宝请来");
    expect(w.text()).not.toContain("在场");
    expect(w.text()).not.toContain("问译宝");
  });

  it("无脑工具更安静", () => {
    const w = mount(HomeDeskWork, {
      props: { plugin: "toolbox", title: "工具箱", kind: "tool" },
    });
    expect(w.text()).toContain("工具箱");
    expect(w.text()).not.toContain("译宝请来");
    expect(w.text()).not.toContain("正在用");
    expect(w.text()).not.toContain("在场");
  });

  it("handoff 时可以就地问译宝", async () => {
    const w = mount(HomeDeskWork, {
      props: { plugin: "coding", title: "studio", lendEar: true },
    });
    expect(w.text()).toContain("问译宝");
    await w.get("button[title='跟译宝说']").trigger("click");
    expect(w.emitted("ask")).toBeTruthy();
  });
});
