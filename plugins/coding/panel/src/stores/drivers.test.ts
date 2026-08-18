import { describe, expect, it, vi } from "vitest";
import { agentLabel, createDriversStore, normAgent } from "./drivers";

function makeStore(impl?: (method: string, params?: Record<string, unknown>) => Promise<unknown>) {
  const invoke = vi.fn(impl ?? (async () => ({ drivers: [] })));
  return { store: createDriversStore({ invoke }), invoke };
}

describe("drivers store", () => {
  it("初值:codexAvailable=null(未探测,按可用呈现),curAgent=claude-code(零回归底线)", () => {
    const { store } = makeStore();
    expect(store.state.codexAvailable).toBeNull();
    expect(store.state.curAgent).toBe("claude-code");
  });

  it("probe:调 coding.drivers;codex 可用 → true,不可用 → false", async () => {
    const ok = makeStore(async () => ({
      drivers: [
        { id: "claude-code", available: true, version: "2.0" },
        { id: "codex", available: true, version: "0.5" },
      ],
    }));
    await ok.store.probe();
    expect(ok.invoke).toHaveBeenCalledWith("coding.drivers", {});
    expect(ok.store.state.codexAvailable).toBe(true);

    const no = makeStore(async () => ({ drivers: [{ id: "codex", available: false }] }));
    await no.store.probe();
    expect(no.store.state.codexAvailable).toBe(false);
  });

  it("probe:应答里查无 codex 项同样视为不可用", async () => {
    const { store } = makeStore(async () => ({ drivers: [{ id: "claude-code", available: true }] }));
    await store.probe();
    expect(store.state.codexAvailable).toBe(false);
  });

  it("probe:claude-code 项无 version 键容缺;drivers 键整体缺失容缺", async () => {
    const noVersion = makeStore(async () => ({
      drivers: [{ id: "claude-code", available: true }, { id: "codex", available: true }],
    }));
    await noVersion.store.probe();
    expect(noVersion.store.state.codexAvailable).toBe(true);

    const bare = makeStore(async () => ({}));
    await bare.store.probe();
    expect(bare.store.state.codexAvailable).toBe(false);
  });

  it("probe 失败(老 sidecar 无此方法)保持 null 按可用呈现,恒 resolve", async () => {
    const { store } = makeStore(async () => { throw new Error("unknown method"); });
    await expect(store.probe()).resolves.toBeUndefined();
    expect(store.state.codexAvailable).toBeNull();
  });

  it("curAgent 落 codex 且探测不可用 → 强制回 claude-code;探测可用则保持", async () => {
    const no = makeStore(async () => ({ drivers: [{ id: "codex", available: false }] }));
    no.store.setCurAgent("codex");
    await no.store.probe();
    expect(no.store.state.curAgent).toBe("claude-code");

    const ok = makeStore(async () => ({ drivers: [{ id: "codex", available: true }] }));
    ok.store.setCurAgent("codex");
    await ok.store.probe();
    expect(ok.store.state.curAgent).toBe("codex");
  });

  it("applyCwdDefault:cwd 最近会话引擎记忆;codex 记忆在已探测不可用时回 CC;null 不回退", async () => {
    const ok = makeStore(async () => ({ drivers: [{ id: "codex", available: true }] }));
    await ok.store.probe();
    ok.store.applyCwdDefault("codex");
    expect(ok.store.state.curAgent).toBe("codex");
    ok.store.applyCwdDefault("cc"); // "cc" 等历史值一律归 CC
    expect(ok.store.state.curAgent).toBe("claude-code");

    const no = makeStore(async () => ({ drivers: [{ id: "codex", available: false }] }));
    await no.store.probe();
    no.store.applyCwdDefault("codex"); // codex 记忆但已探测不可用 → CC
    expect(no.store.state.curAgent).toBe("claude-code");

    const unknown = makeStore(); // 未探测(null,按可用呈现):codex 记忆保留,待 probe 结论强制回退
    unknown.store.applyCwdDefault("codex");
    expect(unknown.store.state.curAgent).toBe("codex");
  });

  it("setCurAgent 归一化;normAgent/agentLabel 纯函数", () => {
    const { store } = makeStore();
    store.setCurAgent("codex");
    expect(store.state.curAgent).toBe("codex");
    store.setCurAgent("cc");
    expect(store.state.curAgent).toBe("claude-code");

    expect(normAgent("codex")).toBe("codex");
    expect(normAgent("cc")).toBe("claude-code");
    expect(normAgent("claude-code")).toBe("claude-code");
    expect(agentLabel("codex")).toBe("codex");
    expect(agentLabel("claude-code")).toBe("CC");
  });
});
