import { beforeEach, describe, expect, it, vi } from "vitest";

const { normalizeHost, parsePairUrl, testConn } = await import("./connection");

describe("normalizeHost", () => {
  it("补 scheme 去尾斜杠", () => {
    expect(normalizeHost("127.0.0.1:19527")).toBe("http://127.0.0.1:19527");
    expect(normalizeHost("https://yibao.wuyill.com/")).toBe("https://yibao.wuyill.com");
    expect(normalizeHost(" http://a.com ")).toBe("http://a.com");
  });
});

describe("parsePairUrl", () => {
  it("解析深链配对参数", () => {
    expect(parsePairUrl("yibao://pair?host=https%3A%2F%2Fyibao.wuyill.com&token=abc"))
      .toEqual({ host: "https://yibao.wuyill.com", token: "abc" });
  });
  it("非配对路径或缺参返回 null", () => {
    expect(parsePairUrl("yibao://chat")).toBeNull();
    expect(parsePairUrl("yibao://pair?host=x")).toBeNull(); // 缺 token
    expect(parsePairUrl("https://evil.com/pair?host=x&token=y")).toBeNull();
  });
});

describe("testConn", () => {
  it("health 200 → ok；401 → 带 reason；网络错 → reason", async () => {
    const ok = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    expect(await testConn({ host: "http://127.0.0.1:19527", token: "t" }, ok as unknown as typeof fetch)).toEqual({ ok: true });
    const unauth = vi.fn(async () => new Response("{}", { status: 401 }));
    const r2 = await testConn({ host: "http://x", token: "bad" }, unauth as unknown as typeof fetch);
    expect(r2.ok).toBe(false);
    expect(r2.reason).toContain("token");
    const dead = vi.fn(async () => { throw new TypeError("fetch failed"); });
    const r3 = await testConn({ host: "http://x", token: "t" }, dead as unknown as typeof fetch);
    expect(r3.ok).toBe(false);
    expect(r3.reason).toBeTruthy();
  });
});
