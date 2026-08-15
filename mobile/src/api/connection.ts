import { Preferences } from "@capacitor/preferences";

export interface ConnConfig {
  host: string; // 形如 http://127.0.0.1:19527 或 https://yibao.wuyill.com（无尾斜杠）
  token: string; // http.mobile_token
}

const KEY = "yibao.conn";

export function normalizeHost(h: string): string {
  let s = h.trim();
  if (!/^https?:\/\//i.test(s)) s = `http://${s}`;
  return s.replace(/\/+$/, "");
}

export function parsePairUrl(u: string): ConnConfig | null {
  // 深链：yibao://pair?host=<urlencoded>&token=<...>（桌面设置页二维码内容，P5 落地）
  try {
    const url = new URL(u);
    if (url.protocol !== "yibao:" || url.host !== "pair") return null;
    const host = normalizeHost(url.searchParams.get("host") || "");
    const token = (url.searchParams.get("token") || "").trim();
    if (!host || !token) return null;
    return { host, token };
  } catch {
    return null;
  }
}

export async function loadConn(): Promise<ConnConfig | null> {
  const { value } = await Preferences.get({ key: KEY });
  if (!value) return null;
  try {
    const c = JSON.parse(value) as ConnConfig;
    return c.host && c.token ? c : null;
  } catch {
    return null;
  }
}

export async function saveConn(c: ConnConfig): Promise<void> {
  await Preferences.set({ key: KEY, value: JSON.stringify(c) });
}

export async function testConn(
  c: ConnConfig,
  fetchImpl: typeof fetch = fetch,
): Promise<{ ok: boolean; reason?: string }> {
  try {
    const r = await fetchImpl(`${c.host}/v1/health`, { headers: { "X-Yibao-Token": c.token } });
    if (r.status === 200) return { ok: true };
    if (r.status === 401 || r.status === 429) return { ok: false, reason: `token 不对或被限速（${r.status}）` };
    return { ok: false, reason: `服务器返回 ${r.status}` };
  } catch (e) {
    return { ok: false, reason: `连不上：${e instanceof Error ? e.message : "网络错误"}（译宝在运行吗？）` };
  }
}
