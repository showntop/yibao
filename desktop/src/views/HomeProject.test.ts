// @vitest-environment happy-dom
// N1：工作语境切换的两步确认——会话有任务在跑（busy）时，点「切换」先变确认态，
// 再点一次才真切；不忙时点一下就切。切换留痕由 sidecar notice 覆盖（test_server.py）。
import { mount } from "@vue/test-utils";
import { computed, ref } from "vue";
import { describe, expect, it, vi } from "vitest";
import HomeProject from "./HomeProject.vue";
import type { ProjectInfo } from "../lib/brain";

const switchCalls: string[] = [];

vi.mock("../composables/useProject", () => ({
  useProject: () => ({
    current: computed(() => ({
      id: "proj_current", name: "当前项目", objects: [], touched_at: 1, created_at: 1, dir: "",
    })),
    projects: ref<ProjectInfo[]>([
      { id: "proj_current", name: "当前项目", objects: [], touched_at: 1, created_at: 1, dir: "" } as unknown as ProjectInfo,
      { id: "proj_other", name: "别的项目", objects: [], touched_at: 1, created_at: 1, dir: "" } as unknown as ProjectInfo,
    ]),
    switchTo: async (id: string) => {
      switchCalls.push(id);
      return { ok: true, conversation_id: "", projects: [], current: id };
    },
    refresh: async () => {},
  }),
}));

// projectCardFace 需要的最小字段形状由 lib 决定；这里只关心切换交互，不验卡面
describe("HomeProject 切换两步确认（N1）", () => {
  // HomeWidget 只在装配 placed 时渲染内容；单测用透传 stub 旁路装配上下文
  const global = { stubs: { HomeWidget: { template: "<section><slot /></section>" } } };

  it("不忙：点一次就切", async () => {
    switchCalls.length = 0;
    const w = mount(HomeProject, { props: { sessionId: "c1", busy: false }, global });
    const row = w.findAll(".row")[0];
    await row.trigger("click");
    expect(switchCalls).toEqual(["proj_other"]);
  });

  it("忙：第一下只进确认态不切，再点才切", async () => {
    switchCalls.length = 0;
    const w = mount(HomeProject, { props: { sessionId: "c1", busy: true }, global });
    const row = w.findAll(".row")[0];
    await row.trigger("click");
    expect(switchCalls).toEqual([]); // 没切
    expect(row.text()).toContain("确认切换");
    await row.trigger("click");
    expect(switchCalls).toEqual(["proj_other"]); // 第二下真切
  });
});
