import { describe, expect, it, vi } from "vitest";
import { createSessionStore, type SessionDeps } from "./session";

function makeDeps(invokeImpl?: SessionDeps["invoke"]) {
  const timers: Array<{ fn: () => void; ms: number; cleared: boolean }> = [];
  const reports: string[] = [];
  const reportArgs: Array<[string, boolean]> = [];
  const invoke = invokeImpl ?? vi.fn(async () => ({ session_id: "s1" }));
  const deps: SessionDeps = {
    invoke,
    report: (st, hasSession) => { reports.push(st); reportArgs.push([st, hasSession]); },
    setTimer: (fn, ms) => { const t = { fn, ms, cleared: false }; timers.push(t); return t as never; },
    clearTimer: (t) => { (t as unknown as (typeof timers)[0]).cleared = true; },
    userEchoFallbackMs: 1500,
  };
  return { deps, timers, reports, reportArgs, invoke };
}

/** invoke 挂起,手动放行——用于秒败竞态/兜底定时器等需要精确时序的用例 */
function heldInvoke() {
  let release!: (v: { session_id: string }) => void;
  const invoke = vi.fn(() => new Promise<{ session_id: string }>((res) => { release = res; }));
  // release 必须包一层迟读:invoke 被调用后 executor 才赋值,直接导出会在创建时快照成 undefined
  return { invoke, release: (v: { session_id: string }) => release(v) };
}

const ev = (e: Parameters<ReturnType<typeof createSessionStore>["applyEvent"]>[0]) => e;

describe("事件归约", () => {
  // 清单 1
  it("text_delta 连到同一气泡;非文本事件切断后再起新气泡", () => {
    const { deps } = makeDeps();
    const s = createSessionStore(deps);
    s.applyEvent(ev({ kind: "text_delta", text: "你" }));
    s.applyEvent(ev({ kind: "text_delta", text: "好" }));
    expect(s.state.items).toHaveLength(1);
    expect(s.state.items[0]).toMatchObject({ type: "assistant", raw: "你好", done: false });
    s.applyEvent(ev({ kind: "tool_use", tool: "Bash", input: { command: "ls" } }));
    s.applyEvent(ev({ kind: "text_delta", text: "完" }));
    expect(s.state.items).toHaveLength(3);
    expect(s.state.items[0]).toMatchObject({ done: true });
    expect(s.state.items[2]).toMatchObject({ type: "assistant", raw: "完" });
  });

  // 清单 2
  it("tool_result 挂最近工具卡;file_edit 切断配对", () => {
    const { deps } = makeDeps();
    const s = createSessionStore(deps);
    s.applyEvent(ev({ kind: "tool_use", tool: "Bash", input: {} }));
    s.applyEvent(ev({ kind: "tool_result", text: "ok", is_error: false }));
    const card = s.state.items[0];
    expect(card).toMatchObject({ type: "tool", results: [{ text: "ok", isError: false }] });
    s.applyEvent(ev({ kind: "file_edit", tool: "Edit", path: "a.ts", old: "x", new: "y" }));
    s.applyEvent(ev({ kind: "tool_result", text: "done", is_error: false }));
    expect((card as { results: unknown[] }).results).toHaveLength(1); // 不错挂
  });

  // 清单 2(错误标与无卡退化)
  it("tool_result 错误置 hasError;无卡错误退 errbar,无卡普通退 marker", () => {
    const { deps } = makeDeps();
    const s = createSessionStore(deps);
    s.applyEvent(ev({ kind: "tool_use", tool: "Bash", input: {} }));
    s.applyEvent(ev({ kind: "tool_result", text: "exit 1", is_error: true }));
    expect(s.state.items[0]).toMatchObject({ results: [{ text: "exit 1", isError: true }], hasError: true });
    // 无卡错误结果 → errbar
    s.applyEvent(ev({ kind: "text_delta", text: "段" })); // assistant 切断 lastToolCard
    s.applyEvent(ev({ kind: "tool_result", text: "坏", is_error: true }));
    expect(s.state.error).toBe("坏");
    // 无卡普通结果 → marker
    s.applyEvent(ev({ kind: "tool_result", text: "旁注", is_error: false }));
    const last = s.state.items[s.state.items.length - 1];
    expect(last).toMatchObject({ type: "marker", text: "旁注", err: false });
  });

  // 清单 3
  it("permission_request 产生等待卡,permission_done 收敛(allow/deny)并复位 waiting", () => {
    const { deps, reports } = makeDeps();
    const s = createSessionStore(deps);
    s.applyEvent(ev({ kind: "permission_request", rid: "r1", tool: "Bash", input: { command: "rm" } }));
    expect(s.state.items).toHaveLength(1);
    expect(s.state.items[0]).toMatchObject({ type: "perm", rid: "r1", tool: "Bash", input: { command: "rm" }, state: "waiting" });
    expect(s.state.waiting).toBe(true);
    expect(reports).toContain("waiting");
    s.applyEvent(ev({ kind: "permission_done", rid: "r1", allow: true }));
    expect(s.state.items[0]).toMatchObject({ state: "allowed" });
    expect(s.state.waiting).toBe(false);
    // deny 分支
    s.applyEvent(ev({ kind: "permission_request", rid: "r2", tool: "Write", input: {} }));
    expect(s.state.waiting).toBe(true);
    s.applyEvent(ev({ kind: "permission_done", rid: "r2", allow: false }));
    expect(s.state.items[1]).toMatchObject({ state: "denied" });
    expect(s.state.waiting).toBe(false);
  });

  // 清单 4
  it("user_msg 产生用户气泡(带 uuid);文本匹配的兜底气泡原地升级不双份", async () => {
    // 直接回流(无兜底)→ 新用户气泡
    const { deps } = makeDeps();
    const s = createSessionStore(deps);
    s.applyEvent(ev({ kind: "user_msg", uuid: "u1", text: "hi" }));
    expect(s.state.items).toHaveLength(1);
    expect(s.state.items[0]).toMatchObject({ type: "user", text: "hi", uuid: "u1" });

    // 兜底升级:超时画的无锚气泡,user_msg 回流文本匹配 → 原地补 uuid,不双份
    const h = heldInvoke();
    const d2 = makeDeps(h.invoke as never);
    const s2 = createSessionStore(d2.deps);
    const p = s2.send("/tmp", "hello", "acceptEdits", "claude-code");
    d2.timers[d2.timers.length - 1].fn(); // 手动触发 1.5s 兜底
    expect(s2.state.items).toHaveLength(1);
    expect(s2.state.items[0]).toMatchObject({ type: "user", text: "hello" });
    expect(s2.state.items[0]).not.toHaveProperty("uuid");
    const bubble = s2.state.items[0];
    s2.applyEvent(ev({ kind: "user_msg", uuid: "u9", text: "hello" }));
    expect(s2.state.items).toHaveLength(1); // 不双份
    expect(s2.state.items[0]).toBe(bubble); // 原地升级
    expect(s2.state.items[0]).toMatchObject({ uuid: "u9" });
    h.release({ session_id: "s1" });
    await p;

    // 文本不匹配 → 兜底留存,另起新气泡
    const h3 = heldInvoke();
    const d3 = makeDeps(h3.invoke as never);
    const s3 = createSessionStore(d3.deps);
    const p3 = s3.send("/tmp", "hello", "acceptEdits", "claude-code");
    d3.timers[d3.timers.length - 1].fn();
    s3.applyEvent(ev({ kind: "user_msg", uuid: "u8", text: "world" }));
    expect(s3.state.items).toHaveLength(2);
    expect(s3.state.items[0]).toMatchObject({ type: "user", text: "hello" });
    expect(s3.state.items[0]).not.toHaveProperty("uuid");
    expect(s3.state.items[1]).toMatchObject({ type: "user", text: "world", uuid: "u8" });
    h3.release({ session_id: "s1" });
    await p3;
  });

  // 清单 5
  it("终态复位 streaming/ended;usage 累加且容缺(usage 缺/cost_usd null 不炸)", async () => {
    const { deps } = makeDeps();
    const s = createSessionStore(deps);
    await s.send("/tmp", "hi", "acceptEdits", "claude-code");
    expect(s.state.streaming).toBe(true);
    s.applyEvent(ev({ kind: "done", usage: { input_tokens: 10, output_tokens: 5, cost_usd: 0.01 } }));
    expect(s.state.streaming).toBe(false);
    expect(s.state.ended).toBe("done");
    expect(s.state.usage).toMatchObject({ tok: 15, cost: 0.01, hasCost: true });
    // done.usage 整体缺失不炸
    s.applyEvent(ev({ kind: "done" }));
    expect(s.state.usage.tok).toBe(15);
    // cost_usd 恒 null(codex):token 照算,hasCost 不置位
    s.applyEvent(ev({ kind: "done", usage: { input_tokens: 1, output_tokens: 2, cost_usd: null } }));
    expect(s.state.usage).toMatchObject({ tok: 18, cost: 0.01, hasCost: true });
    // stopped:err marker + ended
    s.applyEvent(ev({ kind: "stopped" }));
    expect(s.state.ended).toBe("stopped");
    expect(s.state.streaming).toBe(false);
    expect(s.state.items[s.state.items.length - 1]).toMatchObject({ type: "marker", text: "已中断", err: true });
    // error:errbar + ended
    s.applyEvent(ev({ kind: "error", text: "boom" }));
    expect(s.state.ended).toBe("error");
    expect(s.state.error).toBe("boom");
  });

  // 清单 7(秒败竞态)
  it("send 秒败竞态:终态先于 invoke 返回 → 不进 streaming", async () => {
    let release!: (v: { session_id: string }) => void;
    const invoke = vi.fn(() => new Promise<{ session_id: string }>((res) => { release = res; }));
    const { deps } = makeDeps(invoke as never);
    const s = createSessionStore(deps);
    const p = s.send("/tmp", "hi", "acceptEdits", "claude-code");
    s.applyEvent(ev({ kind: "error", text: "boom" })); // 终态抢跑
    release({ session_id: "s1" });
    await p;
    expect(s.state.streaming).toBe(false);
    expect(s.state.ended).toBe("error");
  });
});

describe("会话过滤(handleData)", () => {
  // 清单 6
  it("异会话事件在 currentSession 已设时过滤,未设放行;discardedSessions 过滤", async () => {
    const { deps } = makeDeps();
    const s = createSessionStore(deps);
    // currentSession 未设 → 放行(首块早于 invoke 返回的竞态)
    const r1 = s.handleData({ session_id: "sX", event: { kind: "text_delta", text: "早" } });
    expect(r1).toEqual({});
    expect(s.state.items).toHaveLength(1);
    // currentSession 已设 → 异会话过滤,同会话放行
    await s.send("/tmp", "hi", "acceptEdits", "claude-code"); // currentSession = s1
    const before = s.state.items.length;
    expect(s.handleData({ session_id: "sOther", event: { kind: "text_delta", text: "旁" } })).toBeNull();
    expect(s.state.items).toHaveLength(before);
    expect(s.handleData({ session_id: "s1", event: { kind: "text_delta", text: "正" } })).toEqual({});
    // discardedSessions:newChat 后旧会话事件被吞
    s.newChat();
    expect(s.handleData({ session_id: "s1", event: { kind: "text_delta", text: "尸" } })).toBeNull();
    expect(s.state.items).toHaveLength(0);
  });
});

describe("发送状态机", () => {
  // 清单 7(路由/状态流转/兜底定时器)
  it("send 路由:start/send 按 currentSession 分派;refs 拼接;1.5s 兜底定时器", async () => {
    const { deps, invoke, timers, reports } = makeDeps();
    const s = createSessionStore(deps);
    // 无 currentSession → coding.start
    await s.send("/tmp", "你好", "acceptEdits", "codex", { refs: ["a.ts", "b.ts"] });
    expect(invoke).toHaveBeenCalledWith("coding.start", {
      cwd: "/tmp",
      prompt: "你好\n\n引用文件:\n@a.ts\n@b.ts",
      mode: "acceptEdits",
      agent: "codex",
    });
    expect(s.state.currentSession).toBe("s1");
    expect(s.state.curSessAgent).toBe("codex");
    expect(s.state.sending).toBe(false);
    expect(s.state.streaming).toBe(true);
    expect(reports).toEqual(["sending", "streaming"]);
    // 1.5s 兜底定时器:未回流 user_msg 时画无锚用户气泡(完整 prompt)
    expect(timers[0].ms).toBe(1500);
    timers[0].fn();
    expect(s.state.items[0]).toMatchObject({ type: "user", text: "你好\n\n引用文件:\n@a.ts\n@b.ts" });
    expect(s.state.items[0]).not.toHaveProperty("uuid");
    // 有 currentSession → coding.send
    s.applyEvent(ev({ kind: "done" }));
    expect(s.state.streaming).toBe(false);
    await s.send("/tmp", "继续", "default", "codex");
    expect(invoke).toHaveBeenLastCalledWith("coding.send", { id: "s1", prompt: "继续", mode: "default" });
    expect(s.state.streaming).toBe(true);
  });

  // 清单 8
  it("stop 调 coding.stop;stopped 事件经 onSessionEnded 复位;无会话不调", async () => {
    const { deps, invoke } = makeDeps();
    const s = createSessionStore(deps);
    await s.send("/tmp", "hi", "acceptEdits", "claude-code");
    expect(s.state.streaming).toBe(true);
    await s.stop();
    expect(invoke).toHaveBeenCalledWith("coding.stop", { id: "s1" });
    s.applyEvent(ev({ kind: "stopped", text: "已中断" }));
    expect(s.state.streaming).toBe(false);
    expect(s.state.ended).toBe("stopped");
    expect(s.state.items[s.state.items.length - 1]).toMatchObject({ type: "marker", text: "已中断", err: true });
    // 无会话 → 不调 coding.stop
    const d2 = makeDeps();
    const s2 = createSessionStore(d2.deps);
    await s2.stop();
    expect(d2.invoke).not.toHaveBeenCalled();
  });
});

describe("会话恢复", () => {
  // 清单 9
  it("resumeSession:history 转换、清 log、discarded 解锁、skipIfEmpty、防重入、attach", async () => {
    // historyToItems 转换(user/assistant/marker;assistant 逐条成气泡且 done)
    const { deps } = makeDeps();
    const s = createSessionStore(deps);
    expect(s.historyToItems([
      { role: "user", text: "问", uuid: "u1" },
      { role: "assistant", text: "答" },
      { role: "marker", text: "--" },
    ])).toEqual([
      { type: "user", text: "问", uuid: "u1" },
      { type: "assistant", raw: "答", thinking: [], done: true },
      { type: "marker", text: "--", err: false },
    ]);

    const history = {
      messages: [
        { role: "user", text: "旧问", uuid: "u0" },
        { role: "assistant", text: "旧答" },
      ] as const,
    };
    const histInvoke = () => vi.fn(async (method: string) =>
      method === "coding.history" ? history : { session_id: "s1" });

    // 主流程:清 log 换历史 + 分隔 marker;旧会话进 discarded;agent 归一
    const d2 = makeDeps(histInvoke() as never);
    const s2 = createSessionStore(d2.deps);
    await s2.send("/tmp", "hi", "acceptEdits", "claude-code"); // currentSession = s1
    s2.applyEvent(ev({ kind: "text_delta", text: "残留" }));
    const n = await s2.resumeSession("s2", "codex");
    expect(n).toBe(2);
    expect(d2.invoke).toHaveBeenCalledWith("coding.history", { id: "s2" });
    expect(s2.state.currentSession).toBe("s2");
    expect(s2.state.curSessAgent).toBe("codex");
    expect(s2._test.discardedSessions.has("s1")).toBe(true);
    expect(s2.state.items).toHaveLength(3); // 2 条历史 + 分隔 marker(旧内容已清)
    expect(s2.state.items[0]).toMatchObject({ type: "user", text: "旧问", uuid: "u0" });
    expect(s2.state.items[1]).toMatchObject({ type: "assistant", raw: "旧答", done: true });
    expect(s2.state.items[2]).toMatchObject({ type: "marker", text: "—— 以上为历史,继续聊 ↓ ——" });
    expect(s2.state.streaming).toBe(false);
    expect(s2.state.ended).toBeNull();
    expect(s2.state.error).toBeNull();

    // 锁死回归:目标会话在 discarded 中,resume 必须解锁(否则自己的流被过滤吞掉)
    const d3 = makeDeps(histInvoke() as never);
    const s3 = createSessionStore(d3.deps);
    await s3.send("/tmp", "hi", "acceptEdits", "claude-code"); // s1
    s3.newChat(); // s1 进 discarded
    expect(s3._test.discardedSessions.has("s1")).toBe(true);
    await s3.resumeSession("s1");
    expect(s3._test.discardedSessions.has("s1")).toBe(false);
    expect(s3.state.currentSession).toBe("s1");

    // skipIfEmpty:空历史返回 0 且不切换会话
    const d4 = makeDeps(vi.fn(async () => ({ messages: [] })) as never);
    const s4 = createSessionStore(d4.deps);
    const n4 = await s4.resumeSession("sEmpty", undefined, { skipIfEmpty: true });
    expect(n4).toBe(0);
    expect(s4.state.currentSession).toBeNull();
    expect(s4.state.items).toHaveLength(0);

    // 防重入:进行中重入归并返回 -1,只留最新 pending 接力
    let releaseH!: (v: unknown) => void;
    const d5 = makeDeps(vi.fn((method: string) =>
      method === "coding.history"
        ? new Promise((res) => { releaseH = res; })
        : Promise.resolve({ session_id: "s1" })) as never);
    const s5 = createSessionStore(d5.deps);
    const p1 = s5.resumeSession("sA");
    const p2 = s5.resumeSession("sB"); // 重入 → -1,登记 pending
    const p3 = s5.resumeSession("sC"); // 再重入 → 覆盖 pending,只留最新
    await expect(p2).resolves.toBe(-1);
    await expect(p3).resolves.toBe(-1);
    releaseH({ messages: [{ role: "user", text: "A" }] });
    expect(await p1).toBe(1);
    expect(s5.state.currentSession).toBe("sA");
    releaseH({ messages: [{ role: "user", text: "C" }] }); // 放行接力的 sC
    await vi.waitFor(() => expect(s5.state.currentSession).toBe("sC"));
    expect(s5.state.items[0]).toMatchObject({ type: "user", text: "C" });
    expect(d5.invoke).toHaveBeenCalledWith("coding.history", { id: "sC" });
    expect(d5.invoke).not.toHaveBeenCalledWith("coding.history", { id: "sB" }); // sB 被覆盖丢弃

    // attach 载荷 → 触发 resumeSession
    const d6 = makeDeps(vi.fn(async () => ({ messages: [] })) as never);
    const s6 = createSessionStore(d6.deps);
    expect(s6.handleData({ attach: true, session_id: "s9", agent: "codex" })).toEqual({ attached: "s9" });
    await vi.waitFor(() => expect(s6.state.currentSession).toBe("s9"));
    expect(s6.state.curSessAgent).toBe("codex");
  });
});

describe("newChat", () => {
  // 清单 10
  it("清态并把 currentSession 进 discarded", async () => {
    const { deps, reports } = makeDeps();
    const s = createSessionStore(deps);
    await s.send("/tmp", "hi", "acceptEdits", "claude-code");
    s.applyEvent(ev({ kind: "text_delta", text: "答" }));
    s.applyEvent(ev({ kind: "done", usage: { input_tokens: 3, output_tokens: 4 } }));
    s.newChat();
    expect(s.state.currentSession).toBeNull();
    expect(s.state.items).toEqual([]);
    expect(s.state.streaming).toBe(false);
    expect(s.state.sending).toBe(false);
    expect(s.state.waiting).toBe(false);
    expect(s.state.ended).toBeNull();
    expect(s.state.error).toBeNull();
    expect(s.state.usage).toEqual({ tok: 0, cost: 0, hasCost: false });
    expect(s._test.discardedSessions.has("s1")).toBe(true);
    expect(s._test.getQueue()).toHaveLength(0);
    expect(reports[reports.length - 1]).toBe("idle");
  });
});

describe("takeover", () => {
  // 清单 11
  it("busy 入队、空闲直发、终态泄放(stopped 清空)、takeover-stop 调 stop", async () => {
    const { deps, invoke } = makeDeps();
    const s = createSessionStore(deps);
    s.setQueueContext({ cwd: "/q", mode: "acceptEdits", agent: "claude-code" });
    // 空闲直发
    expect(s.takeoverInput("第一条", [], "/tmp", "acceptEdits", "claude-code")).toEqual({ queued: false });
    expect(invoke).toHaveBeenCalledWith("coding.start", expect.objectContaining({ cwd: "/tmp", prompt: "第一条" }));
    // busy 入队(首个 send 尚未返回)
    expect(s.takeoverInput("第二条", ["x.ts"], "/tmp", "acceptEdits", "claude-code")).toEqual({ queued: true });
    expect(s._test.getQueue()).toHaveLength(1);
    await vi.waitFor(() => expect(s.state.streaming).toBe(true));
    // 终态泄放:done → 队列条目经 send 发出(cwd/mode/agent 取 queueContext 快照)
    s.applyEvent(ev({ kind: "done" }));
    expect(invoke).toHaveBeenCalledWith("coding.send", {
      id: "s1",
      prompt: "第二条\n\n引用文件:\n@x.ts",
      mode: "acceptEdits",
    });
    expect(s._test.getQueue()).toHaveLength(0);
    // takeover-stop:busy(第二个 send 在途)→ 调 coding.stop
    s.takeoverStop();
    expect(invoke).toHaveBeenCalledWith("coding.stop", { id: "s1" });
    // stopped 清空队列:中断即放弃排队意图
    s.takeoverInput("第三条", [], "/tmp", "acceptEdits", "claude-code");
    expect(s._test.getQueue()).toHaveLength(1);
    s.applyEvent(ev({ kind: "stopped" }));
    expect(s._test.getQueue()).toHaveLength(0);
    expect(s.state.ended).toBe("stopped");
    // 在途 send 因秒败标记不进 streaming
    await vi.waitFor(() => expect(s.state.sending).toBe(false));
    expect(s.state.streaming).toBe(false);
  });
});

describe("状态上报", () => {
  // 清单 12
  it("report 按状态流转上报 (state, hasSession)(仅 takeover 态由 App 经桥真发)", async () => {
    const { deps, reportArgs } = makeDeps();
    const s = createSessionStore(deps);
    await s.send("/tmp", "hi", "acceptEdits", "claude-code");
    s.applyEvent(ev({ kind: "permission_request", rid: "r1", tool: "Bash", input: {} }));
    s.applyEvent(ev({ kind: "permission_done", rid: "r1", allow: true }));
    s.applyEvent(ev({ kind: "done" }));
    expect(reportArgs).toEqual([
      ["sending", false],
      ["streaming", true],
      ["waiting", true],
      ["streaming", true],
      ["idle", true],
    ]);
    s.newChat();
    expect(reportArgs[reportArgs.length - 1]).toEqual(["idle", false]);
  });
});

describe("评审回归", () => {
  // send 开窗清 fallbackUserIndex:上一轮残留兜底引用不得被新一轮 user_msg 误升级
  it("跨轮误升级守卫:turn1 无锚气泡留存,turn2 同文本 user_msg 另起新气泡", async () => {
    const h = heldInvoke();
    const { deps, timers } = makeDeps(h.invoke as never);
    const s = createSessionStore(deps);
    // turn1:兜底定时器画无锚气泡,user_msg 永不回流
    const p1 = s.send("/tmp", "hello", "acceptEdits", "claude-code");
    timers[timers.length - 1].fn();
    expect(s.state.items).toHaveLength(1);
    expect(s.state.items[0]).not.toHaveProperty("uuid");
    h.release({ session_id: "s1" });
    await p1;
    s.applyEvent(ev({ kind: "done" })); // turn1 结束,残留 fallbackUserIndex
    // turn2:同文本再发;开窗已清兜底引用 → user_msg 回流另起新气泡
    const p2 = s.send("/tmp", "hello", "acceptEdits", "claude-code");
    s.applyEvent(ev({ kind: "user_msg", uuid: "u-new", text: "hello" }));
    expect(s.state.items).toHaveLength(2);
    expect(s.state.items[0]).toMatchObject({ type: "user", text: "hello" });
    expect(s.state.items[0]).not.toHaveProperty("uuid"); // 旧气泡不被误升级
    expect(s.state.items[1]).toMatchObject({ type: "user", text: "hello", uuid: "u-new" });
    h.release({ session_id: "s1" });
    await p2;
  });

  // send 失败:直接调用方拿到 rethrow;takeover 火忘路径吞 rejection,失败仅由 state.error 承载
  it("失败路径:send rethrow 给直接调用方;takeoverInput 不外抛未处理 rejection", async () => {
    const failing = () => vi.fn(async () => { throw new Error("boom"); });
    // 直接调用:rethrow + errbar 前缀 + sending 复位
    const { deps } = makeDeps(failing() as never);
    const s = createSessionStore(deps);
    await expect(s.send("/tmp", "hi", "acceptEdits", "claude-code")).rejects.toThrow("boom");
    expect(s.state.error).toContain("启动失败:");
    expect(s.state.sending).toBe(false);
    // takeover 火忘路径:不抛未处理 rejection,失败落 state.error
    const d2 = makeDeps(failing() as never);
    const s2 = createSessionStore(d2.deps);
    expect(s2.takeoverInput("hi", [], "/tmp", "acceptEdits", "claude-code")).toEqual({ queued: false });
    await vi.waitFor(() => expect(s2.state.error).toContain("启动失败:"));
    expect(s2.state.sending).toBe(false);
  });
});
