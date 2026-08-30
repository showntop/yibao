/** 器选区共享态（design §4「边说边指」）：WebviewPanel 收到器的 selection_changed
 * 面板事件时写入；溪口（HomeHostAsk 提交流）读取；批注点击经 revealHost 回指器。
 * 模块级单例——同窗内 WebviewPanel（浮窗/大窗各一份）与聊天壳共享，不进装配体系。 */
import { ref } from "vue";
import type { DocAnchor } from "../../protocol/brain-types";

export interface LiveSelection extends DocAnchor {
  panel: string;
  /** 器内文档 id（zimeiti 是选题 id）；无文档时空串 */
  docId: string;
  ts: number;
}

/** 选区多旧算「还在指」：超过这个秒数视为已放手的旧上下文 */
export const SELECTION_FRESH_MS = 12_000;

export const liveSelection = ref<LiveSelection | null>(null);

/** reveal 宿主注册表：panel → postToIframe 转发器（WebviewPanel 挂载时注册） */
const revealHosts = new Map<string, (msg: Record<string, unknown>) => void>();

export function registerRevealHost(panel: string, post: (msg: Record<string, unknown>) => void): void {
  revealHosts.set(panel, post);
}

export function unregisterRevealHost(panel: string): void {
  revealHosts.delete(panel);
}

/** 把锚点指给器看（批注气泡点击 → 器滚动选中）。器不在场时静默失败。 */
export function revealAnchor(panel: string, anchor: DocAnchor, sid = `ui_${Date.now() % 2 ** 31}`): boolean {
  const post = revealHosts.get(panel);
  if (!post) return false;
  post({ type: "surface-command", command: "editor.reveal_anchor", params: { ...anchor, sid } });
  return true;
}

/** WebviewPanel 收到器的 selection_changed 上行时调用 */
export function noteSelection(panel: string, payload: Record<string, unknown>): void {
  const start = Number(payload.start);
  const end = Number(payload.end);
  if (!Number.isFinite(start) || !Number.isFinite(end)) return;
  liveSelection.value = {
    panel,
    docId: String(payload.id ?? ""),
    start,
    end,
    quote: String(payload.quote ?? ""),
    ts: Date.now(),
  };
}
