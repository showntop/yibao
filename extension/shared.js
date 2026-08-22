// 共享：配置读写 + 页面提取 + HTTP 请求 + 错误判定。ES module（manifest background type=module；popup 用 <script type="module">）。
export const DEFAULT_PORT = 19527;

export async function getConfig() {
  const { token = "", port = DEFAULT_PORT } = await chrome.storage.sync.get(["token", "port"]);
  return { token, port: Number(port) || DEFAULT_PORT };
}

// 注入页面提取（chrome.scripting executeScript 的 func）：选区优先，否则正文截 20000 字
export function extractPage() {
  const sel = (window.getSelection()?.toString() || "").trim();
  const text = (sel || document.body?.innerText || "").trim().slice(0, 20000);
  return { url: location.href, title: document.title || "", text };
}

// 页面注入失败判定：chrome://、应用商店、PDF 等受浏览器限制的页面
export const INJECT_FAIL_HINT = "这个页面不支持读取（chrome://、应用商店、PDF 等页面受浏览器限制）";
export function isInjectFail(msg) {
  return msg.includes("Cannot access") || msg.includes("Cannot script");
}

// 统一 HTTP：打大脑 HTTP 面（token 走 header）。body 省略则为 GET，否则 POST JSON。
export async function yibaoFetch(path, { token, port }, body) {
  const headers = { "X-Yibao-Token": token };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const resp = await fetch(`http://127.0.0.1:${port}${path}`, {
    method: body !== undefined ? "POST" : "GET",
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  return resp.json();
}

export async function saveToYibao(payload) {
  const { token, port } = await getConfig();
  if (!token) return { ok: false, error: "未配置 token（扩展选项页粘贴）" };
  try {
    return await yibaoFetch("/save", { token, port }, payload);
  } catch (e) {
    return { ok: false, error: `连不上译宝大脑（127.0.0.1:${port}）——它在运行吗？` };
  }
}
