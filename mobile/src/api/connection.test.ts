import { describe, expect, it, vi } from "vitest";

const { apiBase, normalizeHost, parsePairUrl, testConn } = await import("./connection");

describe("normalizeHost", () => {
  it("补 scheme 去尾斜杠", () => {
    expect(normalizeHost("127.0.0.1:19527")).toBe("http://127.0.0.1:19527");
    expect(normalizeHost("https://yibao.wuyill.com/")).toBe("https://yibao.wuyill.com");
    expect(normalizeHost(" http://a.com ")).toBe("http://a.com");
  });
  it("空串归一为 'http:'（truthy 无 netloc，防锁死靠 parsePairUrl 的 netloc 判）", () => {
    expect(normalizeHost("")).toBe("http:");
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
  it("缺 host / host 无 netloc 返回 null（防 'http:' 落盘锁死）", () => {
    expect(parsePairUrl("yibao://pair?token=abc")).toBeNull(); // 缺 host
    expect(parsePairUrl("yibao://pair?host=&token=abc")).toBeNull(); // 空 host
    expect(parsePairUrl("yibao://pair?host=http%3A%2F%2F&token=abc")).toBeNull(); // 有 scheme 无 hostname
  });
});

describe("apiBase", () => {
  const c = { host: "http://192.168.31.52:19527", token: "t" };
  it("浏览器态返空串：https 页直连 http host 撞 WebKit mixed content（Load failed），走 vite 同源代理", () => {
    expect(apiBase(c)).toBe("");
  });
  it("原生态直连 conn.host（WebView 无 mixed content 语义）", () => {
    expect(apiBase(c, () => true)).toBe("http://192.168.31.52:19527");
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
  it("浏览器态经 apiBase 同源请求：URL 不带 host（jsdom 即浏览器态）", async () => {
    const f = vi.fn(async () => new Response("{}", { status: 200 }));
    await testConn({ host: "http://192.168.31.52:19527", token: "t" }, f as unknown as typeof fetch);
    expect(f).toHaveBeenCalledWith("/v1/health", expect.anything());
  });
});
