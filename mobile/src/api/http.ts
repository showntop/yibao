// 手机伴生端 HTTP 客户端：带 token 的 JSON 请求（state/*.ts 共用的唯一请求入口）。
// 两种语义：
//   - getJson/postJson：失败静默（返回 null）——用于「断线保留旧值」的增强面（如动态流）。
//   - getJsonResult/postJsonResult：返回 {data, error}——用于「错误态必须亮出来」的专页
//     （错误文案区分服务器状态码与网络错误，由调用方拼装）。
// 不抛错、不弹窗，错误展示由页面层决定。
import type { ConnConfig } from "./connection";

export interface JsonResult {
  data: unknown | null;
  /** 非 2xx 带状态码；断线/超时带网络错误消息；成功为 null */
  error: string | null;
}

async function request(
  conn: ConnConfig,
  path: string,
  init: RequestInit,
  fetchImpl: typeof fetch,
): Promise<JsonResult> {
  try {
    const r = await fetchImpl(`${conn.host}${path}`, init);
    if (!r.ok) {
      // 优先取服务端 error 字段（如 /v1/reminders/cancel 的 500 详情）；无则退回状态码
      let detail = "";
      try {
        const b = (await r.json()) as { error?: unknown };
        if (b && typeof b.error === "string") detail = b.error;
      } catch { /* 非 JSON 响应体 */ }
      return { data: null, error: detail || `服务器返回 ${r.status}` };
    }
    const text = await r.text();
    return { data: text ? (JSON.parse(text) as unknown) : null, error: null };
  } catch (e) {
    return { data: null, error: e instanceof Error ? e.message : "网络错误" };
  }
}

const authHeader = (conn: ConnConfig): Record<string, string> => ({ "X-Yibao-Token": conn.token });

/** 带 token 的 JSON GET；失败（断线/超时/非 2xx）→ null。 */
export async function getJson(
  conn: ConnConfig,
  path: string,
  fetchImpl: typeof fetch = fetch,
): Promise<unknown | null> {
  const res = await request(conn, path, { headers: authHeader(conn) }, fetchImpl);
  return res.data;
}

/** 带 token 的 JSON POST；失败 → null。 */
export async function postJson(
  conn: ConnConfig,
  path: string,
  body: unknown,
  fetchImpl: typeof fetch = fetch,
): Promise<unknown | null> {
  const res = await request(
    conn,
    path,
    { method: "POST", headers: { ...authHeader(conn), "Content-Type": "application/json" }, body: JSON.stringify(body) },
    fetchImpl,
  );
  return res.data;
}

/** 带 token 的 JSON GET，携带失败原因（状态码/网络错误）。 */
export async function getJsonResult(
  conn: ConnConfig,
  path: string,
  fetchImpl: typeof fetch = fetch,
): Promise<JsonResult> {
  return request(conn, path, { headers: authHeader(conn) }, fetchImpl);
}

/** 带 token 的 JSON POST，携带失败原因。 */
export async function postJsonResult(
  conn: ConnConfig,
  path: string,
  body: unknown,
  fetchImpl: typeof fetch = fetch,
): Promise<JsonResult> {
  return request(
    conn,
    path,
    { method: "POST", headers: { ...authHeader(conn), "Content-Type": "application/json" }, body: JSON.stringify(body) },
    fetchImpl,
  );
}
