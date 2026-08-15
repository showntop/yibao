import { describe, expect, it, vi } from "vitest";
import { handlePairUrl } from "./deeplink";

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
