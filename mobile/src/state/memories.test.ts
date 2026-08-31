import { describe, expect, it, vi } from "vitest";
import { useMemories } from "./memories";
import type { ConnConfig } from "../api/connection";

// fake fetch：按 URL 前缀路由，记录请求（断言 token header）
function mkFetch(routes: Record<string, { status?: number; body: unknown } | (() => Response)>) {
  const calls: { url: string; headers: Record<string, string> }[] = [];
  const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, headers: (init?.headers as Record<string, string>) ?? {} });
    const hit = Object.entries(routes).find(([p]) => url.startsWith(p))?.[1];
    if (!hit) return new Response(JSON.stringify({ ok: false }), { status: 500 });
    if (typeof hit === "function") return hit();
    return new Response(JSON.stringify(hit.body), { status: hit.status ?? 200 });
  });
  return { fetchImpl: fetchImpl as unknown as typeof fetch, calls };
}

// 服务端 _mem_list 的 items 形状：id/text/ns/label/created_at（底座+插件命名空间分组，最新在前）
const MEM_ITEMS = [
  { id: "m-1", text: "用户偏好极简风格", ns: "", label: "译宝", created_at: "2026-08-15 09:30:00" },
  { id: "m-2", text: "每周一早会复盘", ns: "reminders", label: "提醒", created_at: "2026-08-14 18:00:00" },
];

describe("useMemories", () => {
  it("refresh：GET /v1/memories 带 token header，items 落地且 error 清位", async () => {
    const { fetchImpl, calls } = mkFetch({
      "/v1/memories": { body: { ok: true, items: MEM_ITEMS } },
    });
    const m = useMemories({ host: "http://x", token: "t" } as ConnConfig, fetchImpl);
    await m.refresh();
    expect(m.items.value).toEqual(MEM_ITEMS);
    expect(m.error.value).toBe("");
    expect(m.loading.value).toBe(false);
    expect(calls[0].url).toBe("/v1/memories");
    expect(calls[0].headers["X-Yibao-Token"]).toBe("t");
  });

  it("loading 全程：请求中 true，落定 false（loading 态与空态可区分）", async () => {
    let release!: (v: Response) => void;
    const gate = new Promise<Response>((r) => (release = r));
    const m = useMemories({ host: "http://x", token: "t" } as ConnConfig, vi.fn(async () => gate) as unknown as typeof fetch);
    const p = m.refresh();
    expect(m.loading.value).toBe(true);
    release(new Response(JSON.stringify({ ok: true, items: [] }), { status: 200 }));
    await p;
    expect(m.loading.value).toBe(false);
  });

  it("非 200（含 503 未接线）→ error 文案（错误态与空态分开），items 不动", async () => {
    const m = useMemories(
      { host: "http://x", token: "t" } as ConnConfig,
      mkFetch({ "/v1/memories": { status: 503, body: { ok: false } } }).fetchImpl,
    );
    await m.refresh();
    expect(m.error.value).toContain("503");
    expect(m.items.value).toEqual([]);
    // 恢复后：列表落地且 error 清位
    const { fetchImpl } = mkFetch({ "/v1/memories": { body: { ok: true, items: MEM_ITEMS } } });
    const m2 = useMemories({ host: "http://x", token: "t" } as ConnConfig, fetchImpl);
    await m2.refresh();
    expect(m2.error.value).toBe("");
    expect(m2.items.value).toEqual(MEM_ITEMS);
  });

  it("网络错 → error 文案；先有数据再断线 → 保留旧 items", async () => {
    let down = false;
    const flaky = vi.fn(async () => {
      if (down) throw new Error("net down");
      return new Response(JSON.stringify({ ok: true, items: MEM_ITEMS }), { status: 200 });
    });
    const m = useMemories({ host: "http://x", token: "t" } as ConnConfig, flaky as unknown as typeof fetch);
    await m.refresh();
    down = true;
    await m.refresh();
    expect(m.items.value).toEqual(MEM_ITEMS); // 旧数据保留
    expect(m.error.value).toContain("加载失败");
  });
});
