import { extractPage, saveToYibao } from "./shared.js";

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "yibao-save",
    title: "存进译宝素材库",
    contexts: ["page", "selection"],
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== "yibao-save" || !tab?.id) return;
  try {
    const [{ result }] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: extractPage });
    const r = await saveToYibao({ ...result, mode: "material" });
    chrome.notifications.create({
      type: "basic",
      iconUrl: "icon128.png",
      title: r.ok ? "已存素材" : "存素材失败",
      message: r.ok ? `《${r.title}》` : r.error || "未知错误",
    });
  } catch (e) {
    const msg = String(e);
    const injectFail = msg.includes("Cannot access") || msg.includes("Cannot script");
    chrome.notifications.create({
      type: "basic",
      iconUrl: "icon128.png",
      title: "存素材失败",
      message: injectFail
        ? "这个页面不支持读取（chrome://、应用商店、PDF 等页面受浏览器限制）"
        : msg.slice(0, 80),
    });
  }
});
