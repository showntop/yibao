import { describe, expect, it, vi } from "vitest";
import { pendingCount, usePendingBadge } from "./pending-badge";
import type { ConnConfig } from "../api/connection";

function fakeStream() {
  const reg = new Map<string, Set<(d: any) => void>>();
  return {
    on: (k: string, fn: (d: any) => void) => { if (!reg.has(k)) reg.set(k, new Set()); reg.get(k)!.add(fn); },
    emit: (k: string) => reg.get(k)?.forEach((f) => f({})),
  };
}

describe("usePendingBadge（待批角标，从 useChat 上移）", () => {
  it("构造拉 /v1/state 计数；confirmation_needed 帧 +1；sync 重置为服务端事实", async () => {
    const st = fakeStream();
    let pendingN = 1; // 构造时已有 1 条待批
    const fetchImpl = vi.fn(async (url: string) => {
      if (url.endsWith("/v1/state"))
        return new Response(JSON.stringify({ ok: true, running: null,
          pending: Array.from({ length: pendingN }, (_, i) => ({ id: `pa_${i}` })) }), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    const badge = usePendingBadge(st as never, { host: "http://x", token: "t" } as ConnConfig, fetchImpl as never);
    await new Promise((r) => setTimeout(r, 0)); // 等构造时的首次计数落地
    expect(badge.count.value).toBe(1);
    st.emit("confirmation_needed"); // 桌面又发起一条待批 → +1
    st.emit("confirmation_needed");
    expect(badge.count.value).toBe(3);
    pendingN = 0; // 全部已处理 → sync 拉回 0
    await badge.sync();
    expect(badge.count.value).toBe(0);
  });

  it("模块级单例：Chat 页与 TabBar（无 stream 的兄弟组件）读同一个计数", async () => {
    const st = fakeStream();
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({ ok: true, running: null, pending: [{ id: "pa_1" }] }), { status: 200 }));
    usePendingBadge(st as never, { host: "http://x", token: "t" } as ConnConfig, fetchImpl as never);
    await new Promise((r) => setTimeout(r, 0));
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
