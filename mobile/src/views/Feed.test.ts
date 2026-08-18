import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { nextTick, ref } from "vue";
import { createMemoryHistory, createRouter } from "vue-router";
import type { ConnConfig } from "../api/connection";
import type { FeedStats, RunningTask } from "../state/feed";

// 组件挂载面测试（M 打磨批）：Feed.vue 的卸载竞态守卫与 statline 去重。
// useFeed/useReminders/loadConn 全 mock——轮询与拉取语义另有 state 层单测兜底。
vi.mock("../api/connection", () => ({ loadConn: vi.fn() }));
vi.mock("../state/feed", () => ({ useFeed: vi.fn() }));
vi.mock("../state/reminders", () => ({ useReminders: vi.fn() }));

import { loadConn } from "../api/connection";
import { useFeed } from "../state/feed";
import { useReminders } from "../state/reminders";
import Feed from "./Feed.vue";

const CONN: ConnConfig = { host: "http://x", token: "t" };

beforeEach(() => {
  vi.mocked(loadConn).mockResolvedValue(CONN);
  vi.mocked(useReminders).mockReturnValue({
    items: ref([]),
    error: ref(""),
    refresh: vi.fn(async () => {}),
    cancel: vi.fn(),
  } as never);
});

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/feed", component: Feed },
      { path: "/pairing", component: { template: "<div/>" } },
    ],
  });
}

async function mountPage() {
  const router = makeRouter();
  router.push("/feed");
  await router.isReady();
  const w = mount(Feed, { global: { plugins: [router] } });
  await flushPromises(); // onMounted 链路走完
  return w;
}

describe("Feed（打磨批）", () => {
  it("卸载竞态守卫：loadConn 未决即离页 → 不再构造 useFeed（interval 漏停源头消除）", async () => {
    // loadConn 悬而不决：构造 onMounted 里 await 之后的窗口，恰是竞态现场
    let resolveConn!: (c: ConnConfig | null) => void;
    vi.mocked(loadConn).mockImplementation(() => new Promise((r) => { resolveConn = r; }));
    vi.mocked(useFeed).mockImplementation(() => {
      throw new Error("已卸载还构造 useFeed：30s 轮询 interval 将无人 stop");
    });
    const w = await mountPage();
    w.unmount(); // await 期间离页：onUnmounted 先跑（无 feed 可停）
    resolveConn(CONN); // 迟到的连接结果回来
    await flushPromises();
    expect(vi.mocked(useFeed)).not.toHaveBeenCalled(); // 守卫生效：不再构造（也就无 interval 可漏）
  });

  it("进行中计数去重：区块显示时 statline 不含「进行中」，区块隐藏时完整显示", async () => {
    const running = ref<RunningTask[]>([
      { id: "job_1", kind: "script", label: "后台命令", status: "running", created_at: 1 },
    ]);
    const stats = ref<FeedStats | null>({ pending_reminders: 1, running_tasks: 1, done_24h: 5 });
    vi.mocked(useFeed).mockReturnValue({
      running,
      stats,
      items: ref([]),
      refresh: vi.fn(async () => {}),
      start: vi.fn(),
      stop: vi.fn(),
    } as never);
    const w = await mountPage();
    // 有进行中：计数只出现在区块头一处，statline 隐藏该段
    expect(w.find(".running").exists()).toBe(true);
    expect(w.get(".r-head").text()).toContain("进行中 · 1");
    expect(w.get(".statline").text()).not.toContain("进行中");
    // 无进行中：区块整体隐藏，statline 恢复完整（含「进行中 0」）
    running.value = [];
    await nextTick();
    expect(w.find(".running").exists()).toBe(false);
    expect(w.get(".statline").text()).toContain("进行中 0");
  });
});
