import { Capacitor } from "@capacitor/core";
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

// normalizeHost("") 会得 "http:"（truthy 但无 netloc）——靠这里判 hostname 兜住缺 host 的深链
function hasNetloc(h: string): boolean {
  try {
    return new URL(h).hostname !== "";
  } catch {
    return false;
  }
}

export function parsePairUrl(u: string): ConnConfig | null {
  // 深链：yibao://pair?host=<urlencoded>&token=<...>（桌面设置页二维码内容，P5 落地）
  try {
    const url = new URL(u);
    if (url.protocol !== "yibao:" || url.host !== "pair") return null;
    const host = normalizeHost(url.searchParams.get("host") || "");
    const token = (url.searchParams.get("token") || "").trim();
    if (!hasNetloc(host) || !token) return null;
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

export async function clearConn(): Promise<void> {
  await Preferences.remove({ key: KEY });
}

/** API 基址：dev 面页面是 https（vite+mkcert），页内直连 sidecar 的 http:// 撞
 * WebKit mixed content 拦截（请求根本不发，fetch 抛 "Load failed"）——
 * 浏览器态一律同源走 vite 代理（vite.config server.proxy /v1 → 127.0.0.1:19527）；
 * 原生 WebView 无 mixed content 语义，直连 conn.host。 */
export function apiBase(
  c: ConnConfig,
  isNative: () => boolean = () => Capacitor.isNativePlatform(),
): string {
  return isNative() ? c.host : "";
}

export async function testConn(
  c: ConnConfig,
  fetchImpl: typeof fetch = fetch,
): Promise<{ ok: boolean; reason?: string }> {
  try {
    const r = await fetchImpl(`${apiBase(c)}/v1/health`, { headers: { "X-Yibao-Token": c.token } });
    if (r.status === 200) return { ok: true };
    if (r.status === 401 || r.status === 429) return { ok: false, reason: `token 不对或被限速（${r.status}）` };
    return { ok: false, reason: `服务器返回 ${r.status}` };
  } catch (e) {
    return { ok: false, reason: `连不上：${e instanceof Error ? e.message : "网络错误"}（译宝在运行吗？）` };
  }
}
