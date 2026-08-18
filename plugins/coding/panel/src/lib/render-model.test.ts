// T4 渲染模型补缺:store 为渲染组件新增字段的归约测试(runPrefix/lastUsage/stop 返回值)。
// 通用归约行为见 stores/session.test.ts,这里只补 T4 渲染依赖的缺口。
import { describe, expect, it, vi } from "vitest";
import { createSessionStore, type SessionDeps } from "../stores/session";

function makeDeps(invokeImpl?: SessionDeps["invoke"]) {
  const timers: Array<{ fn: () => void; ms: number; cleared: boolean }> = [];
  const deps: SessionDeps = {
    invoke: invokeImpl ?? vi.fn(async () => ({ session_id: "s1" })),
    setTimer: (fn, ms) => { const t = { fn, ms, cleared: false }; timers.push(t); return t as never; },
    clearTimer: (t) => { (t as unknown as (typeof timers)[0]).cleared = true; },
    userEchoFallbackMs: 1500,
  };
  return { deps, timers };
}

describe("T4 渲染模型补缺", () => {
  it("send 进 streaming 时落定 runPrefix:新会话「启动」/ 接续会话「接续」", async () => {
    const { deps } = makeDeps();
    const s = createSessionStore(deps);
    await s.send("/tmp", "hi", "acceptEdits", "claude-code");
    expect(s.state.runPrefix).toBe("会话 s1 启动");
    s.applyEvent({ kind: "done" });
    await s.send("/tmp", "again", "acceptEdits", "claude-code");
    expect(s.state.runPrefix).toBe("会话 s1 接续");
  });

  it("秒败竞态(pendingTurnEnded)不进 streaming,runPrefix 不落定", async () => {
    let release!: (v: { session_id: string }) => void;
    const invoke = vi.fn(() => new Promise<{ session_id: string }>((res) => { release = res; }));
    const { deps } = makeDeps(invoke);
    const s = createSessionStore(deps);
    const p = s.send("/tmp", "hi", "acceptEdits", "claude-code");
    s.applyEvent({ kind: "error", text: "boom" }); // 终态先于 invoke 返回
    release({ session_id: "s1" });
    await p;
    expect(s.state.streaming).toBe(false);
    expect(s.state.runPrefix).toBe("");
  });

  it("done 的 usage 落 lastUsage;newChat/resumeSession 清零", async () => {
    const { deps } = makeDeps();
    const s = createSessionStore(deps);
    await s.send("/tmp", "hi", "acceptEdits", "claude-code");
    const u = { duration_ms: 1200, input_tokens: 10, output_tokens: 5, cost_usd: 0.01 };
    s.applyEvent({ kind: "done", usage: u });
    expect(s.state.lastUsage).toEqual(u);
    expect(s.state.usage.tok).toBe(15);
    s.newChat();
    expect(s.state.lastUsage).toBeNull();
    expect(s.state.runPrefix).toBe("");
    expect(s.state.usage).toEqual({ tok: 0, cost: 0, hasCost: false });
  });

  it("done 无 usage → lastUsage 保持(null),累计不变", async () => {
    const { deps } = makeDeps();
    const s = createSessionStore(deps);
    await s.send("/tmp", "hi", "acceptEdits", "claude-code");
    s.applyEvent({ kind: "done" });
    expect(s.state.lastUsage).toBeNull();
    expect(s.state.ended).toBe("done");
  });

  it("stop 返回值:受理 true;无会话/invoke 失败 false(pill Stop 据此重解锁)", async () => {
    const { deps } = makeDeps();
    const s = createSessionStore(deps);
    expect(await s.stop()).toBe(false); // 无会话
    await s.send("/tmp", "hi", "acceptEdits", "claude-code");
    expect(await s.stop()).toBe(true);
    const failing = createSessionStore({ ...deps, invoke: vi.fn(async () => { throw new Error("x"); }) });
    await failing.send("/tmp", "hi", "acceptEdits", "claude-code").catch(() => {}); // start 即败
    failing.state.currentSession = "s9"; // 构造仅 invoke stop 失败的会话
    expect(await failing.stop()).toBe(false);
  });

  it("errbar 文本空值兜底:无卡错误 tool_result →「工具执行失败」;error 事件 →「未知错误」", () => {
    const { deps } = makeDeps();
    const s = createSessionStore(deps);
    s.applyEvent({ kind: "tool_result", text: "", is_error: true }); // 无卡
    expect(s.state.error).toBe("工具执行失败");
    s.applyEvent({ kind: "user_msg", uuid: "u1", text: "hi" }); // 清 error(新 turn)
    expect(s.state.error).toBeNull();
    s.applyEvent({ kind: "error", text: "" });
    expect(s.state.error).toBe("未知错误");
  });
});
