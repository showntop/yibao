import { afterEach, describe, expect, it, vi } from "vitest";
import { useFeed } from "./feed";
import type { ConnConfig } from "../api/connection";

afterEach(() => vi.useRealTimers()); // fake timers 用例自还原，不外溢到后续用例

// 服务端真实形状（feed.py recent() 逐字段透传，_mobile_feed 不改字段——curl 实机为
// 旧版进程未接线，以源码为准，报告已记录）：
// { id, ts(unix 秒), kind(task|reminder|event), text, meta, read(0|1), status(none|follow|ignore) }
const ITEMS = [
  { id: 3, ts: 1755300000, kind: "task", text: "日报写完了", meta: {}, read: 0, status: "none" },
  { id: 2, ts: 1755200000, kind: "reminder", text: "该喝水了", meta: {}, read: 1, status: "none" },
  { id: 1, ts: 1755100000, kind: "event", text: "盯盘指标更新", meta: { type: "watch" }, read: 0, status: "ignore" },
];

describe("useFeed", () => {
  it("refresh 拉 /v1/feed：items/stats/running 各就位，服务端倒序原样保留", async () => {
    const fetchImpl = vi.fn(async (url: string, _init?: RequestInit) => {
      expect(url).toBe("/v1/feed?limit=60");
      return new Response(JSON.stringify({
        ok: true,
        items: ITEMS,
        stats: { pending_reminders: 1, running_tasks: 2, done_24h: 5, unread: 3, ignored: 1 },
        running_tasks: [{ id: "job_1", kind: "script", label: "后台命令", prompt: "", status: "running", created_at: 1 }],
      }), { status: 200 });
    });
    const f = useFeed({ host: "http://x", token: "t" } as ConnConfig, fetchImpl as never);
    await f.refresh();
    expect(f.items.value).toHaveLength(3);
    expect(f.items.value[0]).toMatchObject({ id: 3, kind: "task", text: "日报写完了", ts: 1755300000 });
    expect(f.stats.value?.done_24h).toBe(5);
    expect(f.running.value).toHaveLength(1);
    expect(fetchImpl.mock.calls[0][1]).toMatchObject({ headers: { "X-Yibao-Token": "t" } });
  });

  it("失败静默：断线/非 200 不清旧列表也不抛（增强面宁滞后勿打扰）", async () => {
    let fail = false;
    const fetchImpl = vi.fn(async () => {
      if (fail) throw new TypeError("network down");
      return new Response(JSON.stringify({ ok: true, items: ITEMS, stats: {}, running_tasks: [] }), { status: 200 });
    });
    const f = useFeed({ host: "http://x", token: "t" } as ConnConfig, fetchImpl as never);
    await f.refresh();
    expect(f.items.value).toHaveLength(3);
    fail = true; // 之后断线
    await expect(f.refresh()).resolves.toBeUndefined();
    expect(f.items.value).toHaveLength(3); // 旧列表原样保留
    fail = false;
    const r500 = vi.fn(async () => new Response("nope", { status: 500 }));
    const g = useFeed({ host: "http://x", token: "t" } as ConnConfig, r500 as never);
    await g.refresh();
    expect(g.items.value).toHaveLength(0); // 首拉失败：空但不抛、stats/running 留空
  });

  it("30s 轮询（M3）：auto 默认开，每 30s 触发一次 refresh，stop 后不再触发", async () => {
    vi.useFakeTimers();
    const fetchImpl = vi.fn(
      async () => new Response(JSON.stringify({ ok: true, items: [], stats: {}, running_tasks: [] }), { status: 200 }),
    );
    const f = useFeed({ host: "http://x", token: "t" } as ConnConfig, fetchImpl as never);
    expect(fetchImpl).not.toHaveBeenCalled(); // 构造只挂 interval，不立即拉（页面自会首拉）
    vi.advanceTimersByTime(30_000);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(30_000);
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    f.stop(); // 离页清 interval：之后时间流逝不再发请求
    vi.advanceTimersByTime(120_000);
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });

  it("轮询开关：auto: false 不起 interval（手动 refresh/手动 start 仍可用）", async () => {
    vi.useFakeTimers();
    const fetchImpl = vi.fn(
      async () => new Response(JSON.stringify({ ok: true, items: [], stats: {}, running_tasks: [] }), { status: 200 }),
    );
    const f = useFeed({ host: "http://x", token: "t" } as ConnConfig, fetchImpl as never, { auto: false });
    vi.advanceTimersByTime(90_000);
    expect(fetchImpl).not.toHaveBeenCalled(); // 关了就不轮询
    f.start(); // 手动补开：下一轮 30s 到点恢复
    vi.advanceTimersByTime(30_000);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });
});
