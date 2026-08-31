import { describe, expect, it, vi } from "vitest";
import { useReminders } from "./reminders";
import type { ConnConfig } from "../api/connection";

// 服务端真实形状（plugins/reminders/tools/list.py 的 rows——when 是服务端拼好的
// 展示串「08月16日 14:30 / 每天 09:00 / 每周一 08:00」，线上不传原始时间戳）：
// { id, text, when }
const ITEMS = [
  { id: "ab12cd34", text: "下午三点复盘", when: "08月16日 15:00" },
  { id: "ef56gh78", text: "每天站会", when: "每天 09:30" },
];

describe("useReminders", () => {
  it("refresh 拉 /v1/reminders：items 就位", async () => {
    const fetchImpl = vi.fn(async (url: string, _init?: RequestInit) => {
      expect(url).toBe("/v1/reminders");
      return new Response(JSON.stringify({ ok: true, items: ITEMS }), { status: 200 });
    });
    const r = useReminders({ host: "http://x", token: "t" } as ConnConfig, fetchImpl as never);
    await r.refresh();
    expect(r.items.value).toHaveLength(2);
    expect(r.items.value[0]).toMatchObject({ id: "ab12cd34", text: "下午三点复盘" });
    expect(fetchImpl.mock.calls[0][1]).toMatchObject({ headers: { "X-Yibao-Token": "t" } });
  });

  it("cancel：POST {id}；成功即从列表移除", async () => {
    const posts: { url: string; body: unknown }[] = [];
    let items = [...ITEMS];
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/v1/reminders/cancel")) {
        posts.push({ url, body: JSON.parse(String(init?.body)) });
        const { id } = JSON.parse(String(init?.body)) as { id: string };
        items = items.filter((i) => i.id !== id); // 服务端取消成功，列表少一条
        return new Response(JSON.stringify({ ok: true }), { status: 200 });
      }
      return new Response(JSON.stringify({ ok: true, items }), { status: 200 });
    });
    const r = useReminders({ host: "http://x", token: "t" } as ConnConfig, fetchImpl as never);
    await r.refresh();
    await r.cancel("ef56gh78");
    expect(posts[0]).toMatchObject({ url: "/v1/reminders/cancel", body: { id: "ef56gh78" } });
    expect(r.items.value.map((i) => i.id)).toEqual(["ab12cd34"]); // 本地即除，不等 refresh
    expect(r.error.value).toBe("");
  });

  it("cancel 失败（500 带 error）：条目保留并亮错误提示", async () => {
    const fetchImpl = vi.fn(async (url: string) => {
      if (url.endsWith("/v1/reminders/cancel"))
        return new Response(JSON.stringify({ ok: false, error: "没找到待触发的提醒" }), { status: 500 });
      return new Response(JSON.stringify({ ok: true, items: ITEMS }), { status: 200 });
    });
    const r = useReminders({ host: "http://x", token: "t" } as ConnConfig, fetchImpl as never);
    await r.refresh();
    await r.cancel("ab12cd34");
    expect(r.items.value).toHaveLength(2); // 失败不动列表
    expect(r.error.value).toContain("没找到待触发的提醒");
  });

  it("refresh 失败静默：断线不清旧列表不抛", async () => {
    let fail = false;
    const fetchImpl = vi.fn(async () => {
      if (fail) throw new TypeError("network down");
      return new Response(JSON.stringify({ ok: true, items: ITEMS }), { status: 200 });
    });
    const r = useReminders({ host: "http://x", token: "t" } as ConnConfig, fetchImpl as never);
    await r.refresh();
    fail = true;
    await expect(r.refresh()).resolves.toBeUndefined();
    expect(r.items.value).toHaveLength(2); // 旧列表保留
  });
});
