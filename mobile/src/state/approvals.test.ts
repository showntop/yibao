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
          pending: state.calls === 1 ? [{ id: "pa_1", tool_id: "code_exec", summary: "cmd=rm x", risk: 3, created_at: 1 }] : [] }), { status: 200 });
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

  it("decide 三态（M 打磨）：断网/异常返 fail，error 写「发送失败」，不弹 gone 语义也不 refresh", async () => {
    const st = fakeStream();
    let stateCalls = 0;
    let confirmDown = false;
    const fetchImpl = vi.fn(async (url: string) => {
      if (url.endsWith("/v1/state")) {
        stateCalls++;
        return new Response(JSON.stringify({ ok: true, running: null,
          pending: [{ id: "pa_3", tool_id: "s", summary: "x", risk: 2, created_at: 3 }] }), { status: 200 });
      }
      if (confirmDown) throw new TypeError("network down"); // /v1/confirm 断网
      return new Response("nope", { status: 500 }); // 先走非 404 的服务端错误
    });
    const a = useApprovals({ host: "http://x", token: "t" } as never, st as never, fetchImpl as never);
    await a.refresh();
    expect(a.pendings.value).toHaveLength(1);
    // 500（非 404）：不是「桌面已处理」，是发送失败
    expect(await a.decide("pa_3", true, false)).toBe("fail");
    expect(a.error.value).toContain("审批发送失败");
    expect(a.error.value).not.toContain("拉取待批失败"); // fail 路径不 refresh，语义不被覆盖
    expect(stateCalls).toBe(1); // 没有额外的 state 拉取
    // 断网抛异常：同样返 fail，列表保持原样（用户可重试）
    confirmDown = true;
    expect(await a.decide("pa_3", true, false)).toBe("fail");
    expect(a.error.value).toContain("审批发送失败");
    expect(a.pendings.value).toHaveLength(1);
    expect(stateCalls).toBe(1);
  });

  it("confirmation_needed 帧驱动自动刷新", async () => {
    const st = fakeStream();
    const fetchImpl = vi.fn(async () => new Response(
      JSON.stringify({ ok: true, running: null, pending: [{ id: "pa_2", tool_id: "s", summary: "x", risk: 2, created_at: 2 }] }), { status: 200 }));
    const a = useApprovals({ host: "http://x", token: "t" } as never, st as never, fetchImpl as never);
    st.emit("confirmation_needed"); // 桌面发起的新待批
    await new Promise((r) => setTimeout(r, 0));
    expect(a.pendings.value).toHaveLength(1);
  });
});
