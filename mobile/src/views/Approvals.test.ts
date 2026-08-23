import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { ref } from "vue";
import { createMemoryHistory, createRouter } from "vue-router";

// 组件挂载面测试（M 打磨批）：只测 Approvals.vue 自身的 UI 分流/防抖逻辑，
// 事件流、角标、审批状态全部 mock 掉——decide 的三态语义另有 state 层单测兜底。
vi.mock("../api/connection", () => ({ loadConn: vi.fn(async () => ({ host: "http://x", token: "t" })) }));
vi.mock("../api/events", () => ({
  buildEventsUrl: vi.fn(() => "http://x/v1/events"),
  useEventStream: vi.fn(() => ({ start: vi.fn(), stop: vi.fn(), on: vi.fn() })),
}));
vi.mock("../state/pending-badge", () => ({ usePendingBadge: vi.fn(() => ({ count: ref(0), sync: vi.fn(async () => {}) })) }));
vi.mock("../state/approvals", () => ({ useApprovals: vi.fn() }));

import { useApprovals } from "../state/approvals";
import Approvals from "./Approvals.vue";

const CONN = { host: "http://x", token: "t" };
const PA_1 = { id: "pa_1", tool_id: "code_exec", summary: "cmd=rm x", risk: 3, created_at: 1 };

/** 与真实 useApprovals 同形的最小假体：pendings/decide/error 可逐测定制 */
function fakeApprovals() {
  return {
    pendings: ref([PA_1]),
    loading: ref(false),
    error: ref(""),
    refresh: vi.fn(async () => {}),
    decide: vi.fn(),
  };
}

let api: ReturnType<typeof fakeApprovals>;

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/chat", component: { template: "<div/>" } },
      { path: "/approvals", component: Approvals },
      { path: "/pairing", component: { template: "<div/>" } },
    ],
  });
}

async function mountPage() {
  const router = makeRouter();
  router.push("/approvals");
  await router.isReady();
  const w = mount(Approvals, { global: { plugins: [router] } });
  await flushPromises(); // onMounted 链路（loadConn → 构造 → 首刷）走完
  return w;
}

beforeEach(() => {
  api = fakeApprovals();
  vi.mocked(useApprovals).mockReturnValue(api as never);
});

describe("Approvals（打磨批 UI 语义）", () => {
  it("goneNote 清除：gone 后再成功（ok）操作，陈旧「已在桌面处理」提示要清空", async () => {
    api.decide.mockResolvedValueOnce("gone").mockResolvedValueOnce("ok");
    const w = await mountPage();
    await w.get(".ok").trigger("click"); // 第一次：桌面已抢先处理
    await flushPromises();
    expect(w.find(".gone").exists()).toBe(true);
    expect(w.get(".gone").text()).toContain("已在桌面处理");
    await w.get(".ok").trigger("click"); // 第二次：正常成功
    await flushPromises();
    expect(w.find(".gone").exists()).toBe(false); // 新成功不留陈旧提示
  });

  it("fail 态分流：错误提示「发送失败」，不弹「已处理」的 goneNote", async () => {
    // 模拟真实 state 层行为：decide 断网返 fail 并写入 error（与 approvals.ts 实现一致）
    api.decide.mockImplementation(async () => {
      api.error.value = "审批发送失败（网络）";
      return "fail";
    });
    const w = await mountPage();
    await w.get(".ok").trigger("click");
    await flushPromises();
    expect(w.get(".err").text()).toContain("审批发送失败");
    expect(w.find(".gone").exists()).toBe(false); // 网络错误 ≠ 桌面已处理
  });

  it("in-flight 防抖：decide 进行中该卡两按钮禁用，双击不重复提交，结束恢复", async () => {
    let resolveDecide!: (v: string) => void;
    api.decide.mockImplementation(() => new Promise((r) => { resolveDecide = r; }));
    const w = await mountPage();
    const ok = w.get(".ok");
    const deny = w.get(".deny");
    await ok.trigger("click");
    await flushPromises();
    expect(ok.attributes("disabled")).toBeDefined(); // 进行中：批准锁
    expect(deny.attributes("disabled")).toBeDefined(); // 拒绝同锁（同一张卡）
    await deny.trigger("click"); // 双击另一钮
    await ok.trigger("click"); // 或复点同钮
    expect(api.decide).toHaveBeenCalledTimes(1); // 只发一次 POST
    resolveDecide("ok");
    await flushPromises();
    expect(ok.attributes("disabled")).toBeUndefined(); // 结束解锁
    expect(deny.attributes("disabled")).toBeUndefined();
  });
});
