import { afterEach, describe, expect, it, vi } from "vitest";
import { pendingCount, usePendingBadge } from "./pending-badge";
import type { ConnConfig } from "../api/connection";

function fakeStream() {
  const reg = new Map<string, Set<(d: any) => void>>();
  return {
    on: (k: string, fn: (d: any) => void) => { if (!reg.has(k)) reg.set(k, new Set()); reg.get(k)!.add(fn); },
    emit: (k: string) => reg.get(k)?.forEach((f) => f({})),
  };
}

afterEach(() => vi.useRealTimers());

describe("usePendingBadge（待批角标，从 useChat 上移）", () => {
  it("构造拉 /v1/state 计数；confirmation_needed 帧不本地 +1，经 debounce 拉服务端事实校准", async () => {
    vi.useFakeTimers();
    const st = fakeStream();
    let pendingN = 1; // 构造时已有 1 条待批
    const fetchImpl = vi.fn(async (url: string) => {
      if (url.endsWith("/v1/state"))
        return new Response(JSON.stringify({ ok: true, running: null,
          pending: Array.from({ length: pendingN }, (_, i) => ({ id: `pa_${i}` })) }), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    const badge = usePendingBadge(st as never, { host: "http://x", token: "t" } as ConnConfig, fetchImpl as never);
    await vi.advanceTimersByTimeAsync(0); // 等构造时的首次计数落地
    expect(badge.count.value).toBe(1);
    pendingN = 3; // 服务端实际涨到 3（帧只是「有变化」提示，数目以拉取为准）
    st.emit("confirmation_needed");
    st.emit("confirmation_needed");
    expect(badge.count.value).toBe(1); // 帧到达不本地 +1：Tab 重挂载/断线 replay 重放历史帧时 +1 必虚增
    await vi.advanceTimersByTimeAsync(300); // debounce 到点 → 拉一次校准
    expect(badge.count.value).toBe(3);
  });

  it("debounce 合并：300ms 窗口内连发多帧只多拉一次 /v1/state", async () => {
    vi.useFakeTimers();
    const st = fakeStream();
    const fetchImpl = vi.fn(async () =>
      new Response(JSON.stringify({ ok: true, running: null, pending: [] }), { status: 200 }));
    usePendingBadge(st as never, { host: "http://x", token: "t" } as ConnConfig, fetchImpl as never);
    await vi.advanceTimersByTimeAsync(0); // 构造首拉
    const base = fetchImpl.mock.calls.length;
    st.emit("confirmation_needed");
    await vi.advanceTimersByTimeAsync(150);
    st.emit("confirmation_needed"); // 窗口内第二帧：计时重置（尾沿 debounce）
    await vi.advanceTimersByTimeAsync(150);
    st.emit("confirmation_needed"); // 第三帧再重置
    await vi.advanceTimersByTimeAsync(299);
    expect(fetchImpl.mock.calls.length).toBe(base); // 静默窗口内不拉
    await vi.advanceTimersByTimeAsync(1);
    expect(fetchImpl.mock.calls.length).toBe(base + 1); // 收口只多拉一次
  });

  it("sync 直接收敛为服务端事实（审批处理完当场归零），并吸收未到点的 debounce", async () => {
    vi.useFakeTimers();
    const st = fakeStream();
    let pendingN = 2;
    const fetchImpl = vi.fn(async () =>
      new Response(JSON.stringify({ ok: true, running: null,
        pending: Array.from({ length: pendingN }, (_, i) => ({ id: `pa_${i}` })) }), { status: 200 }));
    const badge = usePendingBadge(st as never, { host: "http://x", token: "t" } as ConnConfig, fetchImpl as never);
    await vi.advanceTimersByTimeAsync(0);
    expect(badge.count.value).toBe(2);
    st.emit("confirmation_needed"); // 排起一个 debounce
    pendingN = 0; // 审批页已全部处理完
    await badge.sync(); // 手动 sync 即时收敛（清掉 debounce，不再多拉一次）
    expect(badge.count.value).toBe(0);
    const pulls = fetchImpl.mock.calls.length;
    await vi.advanceTimersByTimeAsync(400); // 被 sync 吸收的 debounce 不再触发
    expect(fetchImpl.mock.calls.length).toBe(pulls);
    expect(badge.count.value).toBe(0);
  });

  it("模块级单例：Chat 页与 TabBar（无 stream 的兄弟组件）读同一个计数", async () => {
    vi.useFakeTimers();
    const st = fakeStream();
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({ ok: true, running: null, pending: [{ id: "pa_1" }] }), { status: 200 }));
    usePendingBadge(st as never, { host: "http://x", token: "t" } as ConnConfig, fetchImpl as never);
    await vi.advanceTimersByTimeAsync(0);
    expect(pendingCount.value).toBe(1); // TabBar 直接 import 的就是这枚 ref
  });

  it("拉取失败不动计数（角标宁可滞后不误清）", async () => {
    const st = fakeStream();
    pendingCount.value = 2;
    const fetchImpl = vi.fn(async () => { throw new TypeError("down"); });
    const badge = usePendingBadge(st as never, { host: "http://x", token: "t" } as ConnConfig, fetchImpl as never);
    await badge.sync();
    expect(badge.count.value).toBe(2);
  });
});
