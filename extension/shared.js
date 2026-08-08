// 共享：配置读写 + 页面提取 + 保存请求。ES module（manifest background type=module；popup 用 <script type="module">）。
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

export async function saveToYibao(payload) {
  const { token, port } = await getConfig();
  if (!token) return { ok: false, error: "未配置 token（扩展选项页粘贴）" };
  try {
    const resp = await fetch(`http://127.0.0.1:${port}/save`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Yibao-Token": token },
      body: JSON.stringify(payload),
    });
    return await resp.json();
  } catch (e) {
    return { ok: false, error: `连不上译宝大脑（127.0.0.1:${port}）——它在运行吗？` };
  }
}
