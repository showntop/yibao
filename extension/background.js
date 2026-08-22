import { extractPage, saveToYibao, isInjectFail, INJECT_FAIL_HINT } from "./shared.js";

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "yibao-save",
    title: "存进译宝素材库",
    contexts: ["page", "selection"],
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== "yibao-save" || !tab?.id) return;
  // badge 回执：零权限必达（系统通知可能被 macOS 权限/专注模式吞掉）。点击即「…」过程反馈，结束转 ✓/✗ 3s 自消
  const badge = (state) => {
    const badgeSpec = { pending: ["…", "#0284c7"], ok: ["✓", "#16a34a"], fail: ["✗", "#dc2626"] }[state];
    chrome.action.setBadgeBackgroundColor({ color: badgeSpec[1] });
    chrome.action.setBadgeText({ text: badgeSpec[0] });
    if (state !== "pending") setTimeout(() => chrome.action.setBadgeText({ text: "" }), 3000);
  };
  try {
    badge("pending");
    const [{ result }] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: extractPage });
    const r = await saveToYibao({ ...result, mode: "material" });
    badge(r.ok ? "ok" : "fail");
    chrome.notifications.create({
      type: "basic",
      iconUrl: "icon128.png",
      title: r.ok ? "已存素材" : "存素材失败",
      message: r.ok ? `《${r.title}》` : r.error || "未知错误",
    });
  } catch (e) {
    badge("fail");
    const msg = String(e);
    chrome.notifications.create({
      type: "basic",
      iconUrl: "icon128.png",
      title: "存素材失败",
      message: isInjectFail(msg) ? INJECT_FAIL_HINT : msg.slice(0, 80),
    });
  }
});
