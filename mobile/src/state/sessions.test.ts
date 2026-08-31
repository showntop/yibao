import { describe, expect, it, vi } from "vitest";
import { useSessions } from "./sessions";
import type { ConnConfig } from "../api/connection";

// fake fetch：按 URL 前缀路由，记录请求（断言 token header / query 形状）
function mkFetch(routes: Record<string, { status?: number; body: unknown }>) {
  const calls: { url: string; headers: Record<string, string> }[] = [];
  const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, headers: (init?.headers as Record<string, string>) ?? {} });
    const hit = Object.entries(routes).find(([p]) => url.startsWith(p))?.[1];
    if (!hit) return new Response(JSON.stringify({ ok: false }), { status: 500 });
    return new Response(JSON.stringify(hit.body), { status: hit.status ?? 200 });
  });
  return { fetchImpl: fetchImpl as unknown as typeof fetch, calls };
}

const CONV_ITEMS = [
  { id: "default", preview: "默认桶的最近回答", turns: 4 },
  { id: "c-42", preview: "", turns: 0 },
];

describe("useSessions", () => {
  it("refresh：GET /v1/conversations 带 token header，列表形状落地", async () => {
    const { fetchImpl, calls } = mkFetch({ "/v1/conversations": { body: { ok: true, items: CONV_ITEMS } } });
    const s = useSessions({ host: "http://x", token: "t" } as ConnConfig, fetchImpl);
    expect(s.list.value).toEqual([]);
    await s.refresh();
    expect(s.list.value).toEqual(CONV_ITEMS);
    expect(calls[0].url).toBe("/v1/conversations");
    expect(calls[0].headers["X-Yibao-Token"]).toBe("t");
  });

  it("refresh 失败：非 200 列表留空、网络错保留旧列表，均不抛", async () => {
    // 非 200：首次就失败 → 列表留空
    const bad = mkFetch({ "/v1/conversations": { status: 503, body: { ok: false } } });
    const s = useSessions({ host: "http://x", token: "t" } as ConnConfig, bad.fetchImpl);
    await expect(s.refresh()).resolves.toBeUndefined();
    expect(s.list.value).toEqual([]);
    // 网络错：先给好数据落地，再断线 → 保留旧列表（抽屉非关键路径，不打扰用户）
    let down = false;
    const flaky = vi.fn(async (url: string) => {
      if (down) throw new Error("net down");
      return new Response(JSON.stringify({ ok: true, items: CONV_ITEMS }), { status: 200 });
    });
    const s2 = useSessions({ host: "http://x", token: "t" } as ConnConfig, flaky as unknown as typeof fetch);
    await s2.refresh();
    down = true;
    await expect(s2.refresh()).resolves.toBeUndefined();
    expect(s2.list.value).toEqual(CONV_ITEMS);
  });

  it("refresh 首败：错误态落地（与空态分开），成功后清位", async () => {
    // 首拉就失败（列表尚空）：不能只留「还没有历史会话」的空态文案——要能区分「没数据」与「没拉到」
    const bad = mkFetch({ "/v1/conversations": { status: 500, body: { ok: false } } });
    const s = useSessions({ host: "http://x", token: "t" } as ConnConfig, bad.fetchImpl);
    await s.refresh();
    expect(s.error.value).not.toBe("");
    expect(s.list.value).toEqual([]);
    // 恢复后：列表落地且错误位清零
    const ok = mkFetch({ "/v1/conversations": { body: { ok: true, items: CONV_ITEMS } } });
    const s2 = useSessions({ host: "http://x", token: "t" } as ConnConfig, ok.fetchImpl);
    await s2.refresh();
    expect(s2.error.value).toBe("");
    expect(s2.list.value).toEqual(CONV_ITEMS);
  });

  it("refresh 网络错也落错误态（非静默吞掉）", async () => {
    const broken = vi.fn(async () => { throw new Error("net down"); });
    const s = useSessions({ host: "http://x", token: "t" } as ConnConfig, broken as unknown as typeof fetch);
    await s.refresh();
    expect(s.error.value).not.toBe("");
  });

  it("open(cid)：GET /v1/history?conversation_id=… 返回 items（含 tool 轮，清洗交 loadHistory）", async () => {
    const { fetchImpl, calls } = mkFetch({
      "/v1/history": { body: { ok: true, items: [
        { role: "user", text: "问" },
        { role: "tool", text: "工具轨迹" },
        { role: "assistant", text: "答" },
      ] } },
    });
    const s = useSessions({ host: "http://x", token: "t" } as ConnConfig, fetchImpl);
    const items = await s.open("c 42"); // id 含空格：必须 urlencode
    expect(items).toHaveLength(3);
    expect(calls[0].url).toBe("/v1/history?conversation_id=c%2042");
    expect(calls[0].headers["X-Yibao-Token"]).toBe("t");
  });

  it("open 无 conversation_id → 服务端默认桶（query 仍带空值不误伤）", async () => {
    const { fetchImpl, calls } = mkFetch({
      "/v1/history": { body: { ok: true, items: [] } },
    });
    const s = useSessions({ host: "http://x", token: "t" } as ConnConfig, fetchImpl);
    await expect(s.open("")).resolves.toEqual([]); // 真·空桶：空数组
    expect(calls[0].url).toBe("/v1/history?conversation_id=");
  });

  it("open 失败返回 null（M3）：没拉到不冒充空历史，pickSession 据此亮错误不切换", async () => {
    // 非 200：与「真空桶 []」区分开——空历史会静默清掉当前消息，失败则保留现场等重试
    const bad = mkFetch({ "/v1/history": { status: 500, body: { ok: false } } });
    const s = useSessions({ host: "http://x", token: "t" } as ConnConfig, bad.fetchImpl);
    await expect(s.open("c-1")).resolves.toBeNull();
    // 断线/超时同样返回 null
    const broken = vi.fn(async () => { throw new Error("net down"); });
    const s2 = useSessions({ host: "http://x", token: "t" } as ConnConfig, broken as unknown as typeof fetch);
    await expect(s2.open("c-1")).resolves.toBeNull();
  });
});
