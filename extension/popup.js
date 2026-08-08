import { extractPage, saveToYibao } from "./shared.js";

const status = (t) => (document.getElementById("status").textContent = t);

async function save(mode) {
  try {
    status("提取页面…");
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id) return status("没有活动标签页");
    const [{ result }] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: extractPage });
    if (!result?.text) return status("页面没有可存的内容（先选中或打开正文页）");
    status("保存中…");
    const r = await saveToYibao({ ...result, mode });
    status(r.ok ? `✓ ${mode === "topic" ? "已存选题" : "已存素材"}：《${r.title}》` : `✗ ${r.error}`);
  } catch (e) {
    const msg = String(e);
    status(
      msg.includes("Cannot access") || msg.includes("Cannot script")
        ? "✗ 这个页面不支持读取（chrome://、应用商店、PDF 等页面受浏览器限制）"
        : `✗ ${msg.slice(0, 80)}`,
    );
  }
}

document.getElementById("save-material").addEventListener("click", () => save("material"));
document.getElementById("save-topic").addEventListener("click", () => save("topic"));
document.getElementById("opt").addEventListener("click", () => chrome.runtime.openOptionsPage());
