// @vitest-environment happy-dom
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import HomeShelfPeek from "./HomeShelfPeek.vue";

describe("HomeShelfPeek", () => {
  it("窄屏器物架保留 Workspace/Workflow 卡为首张", async () => {
    const wrapper = mount(HomeShelfPeek, {
      props: { sessionId: "session-field" },
      global: {
        stubs: {
          HomeProject: { name: "HomeProject", props: ["sessionId"], template: '<div data-part="project" />' },
          HomeRemindCard: { name: "HomeRemindCard", template: '<div data-part="remind" />' },
          HomeShelfStats: { name: "HomeShelfStats", template: '<div data-part="stats" />' },
        },
      },
    });
    expect(wrapper.findAll("[data-part]").map((node) => node.attributes("data-part"))).toEqual([
      "project", "remind", "stats",
    ]);
    expect(wrapper.findComponent({ name: "HomeProject" }).props("sessionId")).toBe("session-field");
  });
});
