import { describe, expect, it, vi } from "vitest";
import { useApprovals } from "./approvals";
import type { EventSourceLike } from "../api/events";

function fakeStream() {
  const reg = new Map<string, Set<(d: any) => void>>();
  return {
    es: { addEventListener: () => {}, close: () => {}, onopen: null, onerror: null } as EventSourceLike,
    on: (k: string, fn: (d: any) => void) => { if (!reg.has(k)) reg.set(k, new Set()); reg.get(k)!.add(fn); },
    emit: (k: string) => reg.get(k)?.forEach((f) => f({})),
  };
}

describe("useApprovals", () => {
  it("refresh 拉取 pending；decide ok 后刷新；404 返回 gone", async () => {
    const st = fakeStream();
    const state = { calls: 0, confirmStatus: 200 };
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/v1/state")) {
        state.calls++;
        return new Response(JSON.stringify({ ok: true, running: null,
          pending: state.calls === 1 ? [{ id: "pa_1", skill_id: "code_exec", summary: "cmd=rm x", risk: 3, created_at: 1 }] : [] }), { status: 200 });
      }
      return new Response("{}", { status: state.confirmStatus });
    });
    const a = useApprovals({ host: "http://x", token: "t" } as never, st as never, fetchImpl as never);
    await a.refresh();
    expect(a.pendings.value).toHaveLength(1);
    expect(await a.decide("pa_1", true, false)).toBe("ok");
    expect(a.pendings.value).toHaveLength(0); // decide 内部 refresh
    state.confirmStatus = 404;
    expect(await a.decide("pa_9", true, false)).toBe("gone"); // 桌面已处理/过期
  });

  it("confirmation_needed 帧驱动自动刷新", async () => {
    const st = fakeStream();
    const fetchImpl = vi.fn(async () => new Response(
      JSON.stringify({ ok: true, running: null, pending: [{ id: "pa_2", skill_id: "s", summary: "x", risk: 2, created_at: 2 }] }), { status: 200 }));
    const a = useApprovals({ host: "http://x", token: "t" } as never, st as never, fetchImpl as never);
    st.emit("confirmation_needed"); // 桌面发起的新待批
    await new Promise((r) => setTimeout(r, 0));
    expect(a.pendings.value).toHaveLength(1);
  });
});
