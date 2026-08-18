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
    expect(s5.isResuming()).toBe(true); // autoReplay 让位判据:attach/手动接续在跑时不得再排候选
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
    await vi.waitFor(() => expect(s5.isResuming()).toBe(false)); // 接力链排空后回落
    expect(d5.invoke).toHaveBeenCalledWith("coding.history", { id: "sC" });
    expect(d5.invoke).not.toHaveBeenCalledWith("coding.history", { id: "sB" }); // sB 被覆盖丢弃

    // attach 载荷 → 触发 resumeSession
    const d6 = makeDeps(vi.fn(async () => ({ messages: [] })) as never);
    const s6 = createSessionStore(d6.deps);
    expect(s6.handleData({ attach: true, session_id: "s9", agent: "codex" })).toEqual({ attached: "s9" });
    await vi.waitFor(() => expect(s6.state.currentSession).toBe("s9"));
    expect(s6.state.curSessAgent).toBe("codex");
  });

  // onResumedCwd:成功且 history 带 cwd → 回调(对齐原 setCwd(r.cwd));skipIfEmpty 空历史早退 → 不回调
  it("onResumedCwd:resumeSession 成功带 cwd 回调;skipIfEmpty 空历史不回调", async () => {
    const onResumedCwd = vi.fn();
    const { deps } = makeDeps(vi.fn(async () => ({
      messages: [{ role: "user", text: "旧" }],
      cwd: "/resumed/dir",
    })) as never);
    deps.onResumedCwd = onResumedCwd;
    const s = createSessionStore(deps);
    const n = await s.resumeSession("s1");
    expect(n).toBe(1);
    expect(onResumedCwd).toHaveBeenCalledWith("/resumed/dir");

    // skipIfEmpty 空历史:早退在 cwd 回调之前,即使响应带 cwd 也不得回调
    const onResumedCwd2 = vi.fn();
    const d2 = makeDeps(vi.fn(async () => ({ messages: [], cwd: "/x" })) as never);
    d2.deps.onResumedCwd = onResumedCwd2;
    const s2 = createSessionStore(d2.deps);
    const n2 = await s2.resumeSession("sEmpty", undefined, { skipIfEmpty: true });
    expect(n2).toBe(0);
    expect(onResumedCwd2).not.toHaveBeenCalled();
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

  // 清单 11(接管退出):clearTakeoverQueue 作废排队输入(对齐 setTakeover :807;由 App takeover watch 调)
  it("clearTakeoverQueue 清空排队,泄放不再有条目可发", async () => {
    const h = heldInvoke();
    const { deps, invoke } = makeDeps(h.invoke as never);
    const s = createSessionStore(deps);
    s.setQueueContext({ cwd: "/q", mode: "acceptEdits", agent: "claude-code" });
    const p = s.send("/q", "在途", "acceptEdits", "claude-code"); // 挂起 → busy
    expect(s.takeoverInput("排队中", [], "/q", "acceptEdits", "claude-code")).toEqual({ queued: true });
    expect(s._test.getQueue()).toHaveLength(1);
    s.clearTakeoverQueue(); // takeover 退出
    expect(s._test.getQueue()).toHaveLength(0);
    h.release({ session_id: "s1" });
    await p;
    s.applyEvent(ev({ kind: "done" })); // 终态泄放:队列已空,不再有第二条 send
    expect(invoke).toHaveBeenCalledTimes(1);
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

  // T9 评审修复①:泄放路径跨引擎守卫(对齐原 send() :1884 内联分支——排队条目泄放同样经 send)
  it("泄放跨引擎守卫:queueContext 带 switchAgent(≠curSessAgent)→ 交接;快照漂移/同引擎 → 普通 send", async () => {
    // ① 快照带待定切换:busy 入队 → done 泄放 → handoffSend(session_brief → start 带新引擎)
    let startCalls = 0;
    const invoke = vi.fn(async (method: string) => {
      if (method === "coding.session_brief") return { brief: "摘要" };
      if (method === "coding.start") return { session_id: ++startCalls === 1 ? "s1" : "s2" };
      return { session_id: "s1" };
    });
    const { deps } = makeDeps(invoke as never);
    const handed: string[] = [];
    deps.onQueueHandoff = (a) => { handed.push(a); };
    const s = createSessionStore(deps);
    await s.send("/q", "hi", "acceptEdits", "claude-code"); // currentSession=s1, curSessAgent=claude-code
    s.setQueueContext({ cwd: "/q", mode: "acceptEdits", agent: "claude-code", switchAgent: "codex" });
    expect(s.takeoverInput("排队任务", [], "/q", "acceptEdits", "claude-code")).toEqual({ queued: true }); // streaming 中入队
    s.applyEvent(ev({ kind: "done" })); // 终态泄放 → 守卫命中 → 交接
    await vi.waitFor(() => expect(s.state.currentSession).toBe("s2"));
    expect(invoke).toHaveBeenCalledWith("coding.session_brief", { id: "s1", target: "codex" });
    expect(invoke).toHaveBeenLastCalledWith("coding.start", {
      cwd: "/q", prompt: "【交接上下文】\n摘要\n\n【用户继续】\n排队任务", mode: "acceptEdits", agent: "codex",
    });
    expect(s.state.curSessAgent).toBe("codex");
    expect(handed).toEqual(["codex"]); // onQueueHandoff 回告(App 清待定 + curAgent 同步)
    expect(s._test.getQueue()).toHaveLength(0);
    expect(s._test.discardedSessions.has("s1")).toBe(true);

    // ② 同引擎快照(用户在 chip 点回当前引擎):不触发交接,普通 coding.send 续聊
    const d2 = makeDeps();
    const s2 = createSessionStore(d2.deps);
    await s2.send("/q", "hi", "acceptEdits", "claude-code");
    s2.setQueueContext({ cwd: "/q", mode: "acceptEdits", agent: "claude-code", switchAgent: "claude-code" });
    expect(s2.takeoverInput("再来", [], "/q", "acceptEdits", "claude-code")).toEqual({ queued: true });
    s2.applyEvent(ev({ kind: "done" }));
    await vi.waitFor(() =>
      expect(d2.invoke).toHaveBeenCalledWith("coding.send", { id: "s1", prompt: "再来", mode: "acceptEdits" }));
    expect(d2.invoke).not.toHaveBeenCalledWith("coding.session_brief", expect.anything());
  });

  it("泄放跨引擎守卫:brief 等待期快照漂移(chip 改选/清空)→ 丢弃不发 start;brief 失败落 errbar", async () => {
    // 快照漂移:brief 挂起期间 queueContext.switchAgent 被改 → stale,旧会话保留
    let releaseBrief!: (v: { brief: string }) => void;
    const invoke = vi.fn((method: string) =>
      method === "coding.session_brief"
        ? new Promise<{ brief: string }>((res) => { releaseBrief = res; })
        : Promise.resolve({ session_id: "s1" }));
    const { deps } = makeDeps(invoke as never);
    const s = createSessionStore(deps);
    await s.send("/q", "hi", "acceptEdits", "claude-code");
    s.setQueueContext({ cwd: "/q", mode: "acceptEdits", agent: "claude-code", switchAgent: "codex" });
    s.takeoverInput("排队任务", [], "/q", "acceptEdits", "claude-code");
    s.applyEvent(ev({ kind: "done" })); // 泄放 → handoffSend 挂起等 brief
    await vi.waitFor(() => expect(invoke).toHaveBeenCalledWith("coding.session_brief", expect.anything()));
    s.setQueueContext({ cwd: "/q", mode: "acceptEdits", agent: "claude-code", switchAgent: null }); // 等待期改主意
    releaseBrief({ brief: "b" });
    await vi.waitFor(() => expect(s.state.sending).toBe(false));
    expect(s.state.currentSession).toBe("s1"); // 未交接
    expect(invoke).not.toHaveBeenCalledWith("coding.start", expect.objectContaining({ agent: "codex" }));

    // brief 失败:泄放路径无状态行通道 → 落 errbar(state.error),旧会话保留
    const failing = vi.fn(async (method: string) => {
      if (method === "coding.session_brief") throw new Error("LLM 挂了");
      return { session_id: "s1" };
    });
    const d2 = makeDeps(failing as never);
    const s2 = createSessionStore(d2.deps);
    await s2.send("/q", "hi", "acceptEdits", "claude-code");
    s2.setQueueContext({ cwd: "/q", mode: "acceptEdits", agent: "claude-code", switchAgent: "codex" });
    s2.takeoverInput("排队任务", [], "/q", "acceptEdits", "claude-code");
    s2.applyEvent(ev({ kind: "done" }));
    await vi.waitFor(() => expect(s2.state.error).toContain("交接摘要生成失败:"));
    expect(s2.state.currentSession).toBe("s1");
    expect(s2.state.sending).toBe(false);
  });
});

describe("跨引擎交接 handoffSend", () => {
  // chat.html:1964-1991:占 sending 窗 → coding.session_brief → 旧 sid 进 discarded →
  // currentSession=null → marker → send 走 coding.start(isStart 分支带 mode+agent)
  it("成功:摘要移植开新引擎会话,旧会话进 discarded,marker 入列,onHandedOff 回调", async () => {
    let startCalls = 0;
    const invoke = vi.fn(async (method: string) => {
      if (method === "coding.session_brief") return { brief: "摘要内容" };
      if (method === "coding.start") return { session_id: ++startCalls === 1 ? "s1" : "s2" };
      return { session_id: "s1" };
    });
    const { deps, reports } = makeDeps(invoke as never);
    const s = createSessionStore(deps);
    await s.send("/tmp", "hi", "acceptEdits", "claude-code"); // currentSession = s1
    s.applyEvent(ev({ kind: "done" }));
    const handed: string[] = [];
    const reportsBefore = reports.length;
    const r = await s.handoffSend("codex", {
      cwd: "/tmp", userText: "继续做", mode: "plan", refs: ["a.ts"],
      isStale: () => false, onHandedOff: () => { handed.push("codex"); },
    });
    expect(r).toBe("sent");
    expect(invoke).toHaveBeenCalledWith("coding.session_brief", { id: "s1", target: "codex" });
    // send 的 isStart 分支:带 mode+agent(对齐原 send() ov 路径);refs 拼在交接 prompt 之后
    expect(invoke).toHaveBeenLastCalledWith("coding.start", {
      cwd: "/tmp",
      prompt: "【交接上下文】\n摘要内容\n\n【用户继续】\n继续做\n\n引用文件:\n@a.ts",
      mode: "plan",
      agent: "codex",
    });
    expect(s.state.currentSession).toBe("s2");
    expect(s.state.curSessAgent).toBe("codex");
    expect(s.state.streaming).toBe(true);
    expect(s._test.discardedSessions.has("s1")).toBe(true);
    expect(s.state.items.some((it) => it.type === "marker" &&
      it.text === "—— 交接给 codex 继续（上下文为摘要移植）——")).toBe(true);
    expect(handed).toEqual(["codex"]);
    expect(reports[reportsBefore]).toBe("sending"); // brief 等待期占 sending 窗
  });

  it("等待期改主意(currentSession 变了 / isStale)→ 丢弃,不发 start", async () => {
    // currentSession 变化:等待期用户点了新对话
    let releaseBrief!: (v: { brief: string }) => void;
    const invoke = vi.fn((method: string) =>
      method === "coding.session_brief"
        ? new Promise<{ brief: string }>((res) => { releaseBrief = res; })
        : Promise.resolve({ session_id: "s1" }));
    const { deps, reports } = makeDeps(invoke as never);
    const s = createSessionStore(deps);
    await s.send("/tmp", "hi", "acceptEdits", "claude-code");
    s.applyEvent(ev({ kind: "done" }));
    const p = s.handoffSend("codex", { cwd: "/tmp", userText: "x", mode: "plan", isStale: () => false });
    expect(s.state.sending).toBe(true); // brief 等待窗
    s.newChat(); // 用户改主意:新对话
    releaseBrief({ brief: "b" });
    await expect(p).resolves.toBe("stale");
    const starts1 = invoke.mock.calls.filter((c) => c[0] === "coding.start");
    expect(starts1).toHaveLength(1); // 仅初始那次,交接未发 start
    expect(s.state.sending).toBe(false);
    expect(reports[reports.length - 1]).toBe("idle");

    // isStale:chip 改选(App 的 switchAgent 偏离)
    const d2 = makeDeps(vi.fn(async (method: string) =>
      method === "coding.session_brief" ? { brief: "b" } : { session_id: "s1" }) as never);
    const s2Store = createSessionStore(d2.deps);
    await s2Store.send("/tmp", "hi", "acceptEdits", "claude-code");
    s2Store.applyEvent(ev({ kind: "done" }));
    const r2 = await s2Store.handoffSend("codex", { cwd: "/tmp", userText: "x", mode: "plan", isStale: () => true });
    expect(r2).toBe("stale");
    const starts2 = (d2.invoke.mock.calls as Array<[string, unknown]>).filter((c) => c[0] === "coding.start");
    expect(starts2).toHaveLength(1); // 仅初始那次
    expect(s2Store.state.currentSession).toBe("s1"); // 会话未动
    expect(s2Store.state.sending).toBe(false);
  });

  it("brief 生成失败:rethrow 给调用方(状态行),sending 复位,不进 errbar", async () => {
    const invoke = vi.fn(async (method: string) => {
      if (method === "coding.session_brief") throw new Error("LLM 挂了");
      return { session_id: "s1" };
    });
    const { deps, reports } = makeDeps(invoke as never);
    const s = createSessionStore(deps);
    await s.send("/tmp", "hi", "acceptEdits", "claude-code");
    s.applyEvent(ev({ kind: "done" }));
    await expect(s.handoffSend("codex", { cwd: "/tmp", userText: "x", mode: "plan", isStale: () => false }))
      .rejects.toThrow("LLM 挂了");
    expect(s.state.sending).toBe(false);
    expect(s.state.currentSession).toBe("s1"); // 旧会话保留
    expect(s.state.error).toBeNull(); // 原仅 setStatus,不进 errbar
    expect(reports[reports.length - 1]).toBe("idle");
  });

  it("重入守卫:sending 中/无会话 → failed 且不 invoke", async () => {
    const h = heldInvoke();
    const { deps, invoke } = makeDeps(h.invoke as never);
    const s = createSessionStore(deps);
    const p = s.send("/tmp", "hi", "acceptEdits", "claude-code"); // sending 挂起
    await expect(s.handoffSend("codex", { cwd: "/tmp", userText: "x", mode: "plan", isStale: () => false }))
      .resolves.toBe("failed");
    expect(invoke).not.toHaveBeenCalledWith("coding.session_brief", expect.anything());
    h.release({ session_id: "s1" });
    await p;
    const d2 = makeDeps();
    const s2 = createSessionStore(d2.deps); // 无会话
    await expect(s2.handoffSend("codex", { cwd: "/tmp", userText: "x", mode: "plan", isStale: () => false }))
      .resolves.toBe("failed");
    expect(d2.invoke).not.toHaveBeenCalled();
  });
});

describe("Codex→CC 交接启动 startHandoffSession", () => {
  // chat.html:2268-2319:coding.start {cwd, prompt:brief, source:"codex:"+sid}(不带 mode/agent);
  // 受理前即 streaming;秒败竞态同 send(pendingTurnEnded)
  it("成功:受理前即 streaming,start 不带 mode/agent,回填 CC 会话", async () => {
    let release!: (v: { session_id: string }) => void;
    const invoke = vi.fn(() => new Promise<{ session_id: string }>((res) => { release = res; }));
    const { deps } = makeDeps(invoke as never);
    const s = createSessionStore(deps);
    const p = s.startHandoffSession("/tmp", "cx1", "brief 文本");
    // 受理前:streaming 已真,pill/状态行前缀就位
    expect(s.state.sending).toBe(true);
    expect(s.state.streaming).toBe(true);
    expect(s.state.runPrefix).toBe("Codex 接续启动中…");
    expect(invoke).toHaveBeenCalledWith("coding.start", { cwd: "/tmp", prompt: "brief 文本", source: "codex:cx1" });
    release({ session_id: "s9" });
    await expect(p).resolves.toBe(true);
    expect(s.state.currentSession).toBe("s9");
    expect(s.state.curSessAgent).toBe("claude-code"); // handoff 落 CC 会话
    expect(s.state.runPrefix).toBe("Codex 接续会话 s9");
    expect(s.state.streaming).toBe(true);
    expect(s.state.sending).toBe(false);
  });

  it("秒败竞态:终态先于 invoke 返回 → 不覆盖 streaming,runPrefix 不起跑", async () => {
    let release!: (v: { session_id: string }) => void;
    const invoke = vi.fn(() => new Promise<{ session_id: string }>((res) => { release = res; }));
    const { deps } = makeDeps(invoke as never);
    const s = createSessionStore(deps);
    const p = s.startHandoffSession("/tmp", "cx1", "brief");
    s.applyEvent(ev({ kind: "error", text: "秒败" })); // sending 窗终态 → pendingTurnEnded
    expect(s.state.streaming).toBe(false);
    release({ session_id: "s9" });
    await expect(p).resolves.toBe(true);
    expect(s.state.currentSession).toBe("s9"); // 会话 id 仍回填
    expect(s.state.streaming).toBe(false); // 不被重新置真
    expect(s.state.ended).toBe("error");
  });

  it("失败:错误落 errbar + streaming/sending 复位,恒不 reject", async () => {
    const { deps } = makeDeps(vi.fn(async () => { throw new Error("spawn 失败"); }) as never);
    const s = createSessionStore(deps);
    await expect(s.startHandoffSession("/tmp", "cx1", "brief")).resolves.toBe(false);
    expect(s.state.error).toContain("Codex 接续启动失败:");
    expect(s.state.streaming).toBe(false);
    expect(s.state.sending).toBe(false);
    // 缺 session_id 同样按失败处理
    const d2 = makeDeps(vi.fn(async () => ({})) as never);
    const s2 = createSessionStore(d2.deps);
    await expect(s2.startHandoffSession("/tmp", "cx1", "brief")).resolves.toBe(false);
    expect(s2.state.error).toContain("Codex 接续启动失败:");
  });

  it("重入守卫:sending/streaming 中 → false 且不 invoke", async () => {
    const h = heldInvoke();
    const { deps, invoke } = makeDeps(h.invoke as never);
    const s = createSessionStore(deps);
    const p = s.send("/tmp", "hi", "acceptEdits", "claude-code");
    await expect(s.startHandoffSession("/tmp", "cx1", "brief")).resolves.toBe(false);
    expect(invoke).not.toHaveBeenCalledWith("coding.start", expect.objectContaining({ source: "codex:cx1" }));
    h.release({ session_id: "s1" });
    await p;
  });
});

describe("交接卡项", () => {
  it("pushHandoffCard 追加 handoff 项;dropHandoffCard 移除;newChat 清空", async () => {
    const { deps } = makeDeps();
    const s = createSessionStore(deps);
    s.pushHandoffCard("cx1", "brief", false, null);
    s.pushHandoffCard("cx2", null, false, "读取 brief 失败：x"); // 失败也开卡(红条+空 textarea)
    expect(s.state.items).toHaveLength(2);
    // seq 自增且唯一:v-for key 用,防 index-key 删前卡后组件实例复用串扰(sid 可重复不能作 key)
    expect(s.state.items[0]).toMatchObject({ type: "handoff", seq: 1, sid: "cx1", brief: "brief" });
    expect(s.state.items[1]).toMatchObject({ type: "handoff", seq: 2, sid: "cx2", brief: null, errMsg: "读取 brief 失败：x" });
    s.dropHandoffCard(s.state.items[0]);
    expect(s.state.items).toHaveLength(1);
    expect(s.state.items[0]).toMatchObject({ sid: "cx2" });
    await s.send("/tmp", "hi", "acceptEdits", "claude-code");
    s.newChat();
    expect(s.state.items).toEqual([]); // 新对话清屏,卡随之消失(对齐原 #log innerHTML 清空)
  });
});
