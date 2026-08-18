import { describe, expect, it, vi } from "vitest";
import { handleDeepUrl, handlePairUrl, parseDeepPath } from "./deeplink";

const passConn = vi.fn(async () => ({ ok: true }));
const failConn = vi.fn(async () => ({ ok: false, reason: "连不上" }));

describe("handlePairUrl", () => {
  it("合法配对深链且连通 → 保存并跳转 chat；非法 → 不动", async () => {
    const save = vi.fn();
    const push = vi.fn();
    const ok = await handlePairUrl("yibao://pair?host=http%3A%2F%2F127.0.0.1%3A19527&token=abc", { save, push, testConn: passConn });
    expect(ok).toBe(true);
    expect(save).toHaveBeenCalledWith({ host: "http://127.0.0.1:19527", token: "abc" });
    expect(push).toHaveBeenCalledWith("/chat");
    const bad = await handlePairUrl("yibao://chat", { save, push, testConn: passConn });
    expect(bad).toBe(false);
    expect(save).not.toHaveBeenCalledTimes(2);
  });

  it("testConn 失败 → 不落盘不跳转", async () => {
    const save = vi.fn();
    const push = vi.fn();
    const ok = await handlePairUrl("yibao://pair?host=http%3A%2F%2F127.0.0.1%3A19527&token=abc", { save, push, testConn: failConn });
    expect(ok).toBe(false);
    expect(save).not.toHaveBeenCalled();
    expect(push).not.toHaveBeenCalled();
  });

  it("缺 token 的配对深链 → 拒绝且不跳转", async () => {
    const save = vi.fn();
    const push = vi.fn();
    const ok = await handlePairUrl("yibao://pair?host=http%3A%2F%2F127.0.0.1%3A19527", { save, push, testConn: passConn });
    expect(ok).toBe(false);
    expect(save).not.toHaveBeenCalled();
    expect(push).not.toHaveBeenCalled();
  });

  it("缺 host 的配对深链 → 拒绝且不跳转", async () => {
    const save = vi.fn();
    const push = vi.fn();
    const ok = await handlePairUrl("yibao://pair?token=abc", { save, push, testConn: passConn });
    expect(ok).toBe(false);
    expect(save).not.toHaveBeenCalled();
    expect(push).not.toHaveBeenCalled();
  });

  it("非 yibao scheme 的 URL → 拒绝", async () => {
    const save = vi.fn();
    const push = vi.fn();
    const ok = await handlePairUrl("https://example.com/pair?host=x&token=y", { save, push, testConn: passConn });
    expect(ok).toBe(false);
    expect(save).not.toHaveBeenCalled();
    expect(push).not.toHaveBeenCalled();
  });
});

describe("handleDeepUrl", () => {
  it("yibao://pair 深链 → 配对分支：连通校验通过后保存配置并跳 /chat", async () => {
    const save = vi.fn();
    const push = vi.fn();
    const ok = await handleDeepUrl("yibao://pair?host=http%3A%2F%2F127.0.0.1%3A19527&token=abc", { save, push, testConn: passConn });
    expect(ok).toBe(true);
    expect(save).toHaveBeenCalledWith({ host: "http://127.0.0.1:19527", token: "abc" });
    expect(push).toHaveBeenCalledWith("/chat");
  });

  it("yibao://approvals 深链 → 审批分支：直接 push /approvals，不保存配置", async () => {
    const save = vi.fn();
    const push = vi.fn();
    const ok = await handleDeepUrl("yibao://approvals", { save, push, testConn: passConn });
    expect(ok).toBe(true);
    expect(push).toHaveBeenCalledWith("/approvals");
    expect(save).not.toHaveBeenCalled(); // 已配对设备直达，深链不该碰存储
  });

  it("yibao://chat 无效路径 → 不处理：不保存不跳转", async () => {
    const save = vi.fn();
    const push = vi.fn();
    const ok = await handleDeepUrl("yibao://chat", { save, push, testConn: passConn });
    expect(ok).toBe(false);
    expect(save).not.toHaveBeenCalled();
    expect(push).not.toHaveBeenCalled();
  });
});

describe("parseDeepPath", () => {
  it("配对深链 → {kind:pair, host, token}", () => {
    expect(parseDeepPath("yibao://pair?host=http%3A%2F%2F127.0.0.1%3A19527&token=abc"))
      .toEqual({ kind: "pair", host: "http://127.0.0.1:19527", token: "abc" });
  });

  it("审批深链 yibao://approvals → {kind:approvals}", () => {
    expect(parseDeepPath("yibao://approvals")).toEqual({ kind: "approvals" });
  });

  it("未知路径/非法 URL → null", () => {
    expect(parseDeepPath("yibao://chat")).toBeNull();
    expect(parseDeepPath("yibao://settings?x=1")).toBeNull();
    expect(parseDeepPath("https://example.com/approvals")).toBeNull();
    expect(parseDeepPath("不是 URL")).toBeNull();
  });
});
