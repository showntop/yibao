/** 桌上工位：委派文案、是否接着干、glance 长出锚点。 */

export type DeskWork = {
  panel?: string;
  plugin: string;
  title: string;
  objectTitle?: string;
};

const PANELISH = /列表|看板|studio|^main$|^widget$/i;

/** 账本只写一个给人看的名字，不写 plugin · 中文名 · 面板。 */
export function deskWho(work: DeskWork): string {
  const plugin = work.plugin.trim() || "插件";
  const head = (value: string) => value.split("·")[0].trim();
  const title = head(work.title || "");
  const job = head(work.objectTitle || "");
  if (job && job !== plugin && job !== title && !PANELISH.test(job)) return job;
  if (title && title !== plugin) return title;
  if (job && job !== plugin) return job;
  return plugin;
}

/** 这张面上谁在想：无脑工具 / 译宝脑 / 工人脑。 */
export type DeskKind = "tool" | "host" | "worker";

export function deskAskLine(work: DeskWork): string {
  return deskPathOpen("worker", work);
}

export function deskLeaveLine(work: DeskWork): string {
  return deskPathClose("worker", work);
}

export function deskPathOpen(kind: DeskKind, work: DeskWork): string {
  const who = deskWho(work);
  if (kind === "worker") return `已请 ${who}`;
  if (kind === "tool") return `用了 ${who}`;
  return `摊开 ${who}`;
}

export function deskPathClose(kind: DeskKind, work: DeskWork): string {
  const who = deskWho(work);
  if (kind === "worker") return `已走 ${who}`;
  return `收起 ${who}`;
}

export function isDeskPathOpenLine(text: string): boolean {
  return /^(已请|摊开|用了) /.test(text.trim());
}

export function isDeskPathCloseLine(text: string): boolean {
  return /^(已走|收起) /.test(text.trim());
}

export type PathTalkBubble = {
  role: string;
  text: string;
  panelLink?: boolean;
  proc?: unknown;
  icon?: string;
};

function isDeskPathTalk(bubble: PathTalkBubble): boolean {
  if (isDeskPathOpenLine(bubble.text) || isDeskPathCloseLine(bubble.text)) return false;
  if (bubble.role === "user") return true;
  if (bubble.proc) return true;
  return bubble.role === "ai" && !bubble.icon && !bubble.panelLink && Boolean(bubble.text.trim());
}

export function isDeskPathBounce(
  last: DeskWork | null | undefined,
  next: DeskWork,
  since: readonly PathTalkBubble[],
): boolean {
  if (!last || !isResumeDeskWork(last, next)) return false;
  return !since.some(isDeskPathTalk);
}

export function shouldStampDeskPath(
  current: DeskWork | null,
  last: DeskWork | null,
  next: DeskWork,
  since: readonly PathTalkBubble[],
): boolean {
  if (current && isResumeDeskWork(current, next)) return false;
  if (isDeskPathBounce(last, next, since)) return false;
  return true;
}

const PATH_OPEN = /^(已请|摊开|用了) /;
const PATH_CLOSE = /^(已走|收起) /;

export function unmatchedDeskPath(texts: readonly string[]): string | null {
  let open: string | null = null;
  for (const raw of texts) {
    const text = raw.trim();
    if (PATH_OPEN.test(text)) open = text;
    else if (PATH_CLOSE.test(text)) open = null;
  }
  return open;
}

export function unmatchedDeskAsk(texts: readonly string[]): string | null {
  return unmatchedDeskPath(texts);
}

export function deskKind(plugin: string, input?: string | null): DeskKind {
  if (input === "handoff" || plugin.trim() === "coding") return "worker";
  if (input === "none" || plugin.trim() === "toolbox") return "tool";
  return "host";
}

export function isResumeDeskWork(prev: DeskWork | null | undefined, next: DeskWork): boolean {
  if (!prev) return false;
  if (prev.panel && next.panel) return prev.panel === next.panel;
  return prev.plugin === next.plugin && prev.title === next.title;
}

export function isDeskLivePlugin(widgetPanel: string, livePanel?: string | null): boolean {
  if (!livePanel) return false;
  if (widgetPanel === livePanel) return true;
  const widget = widgetPanel.split(":", 1)[0];
  const live = livePanel.split(":", 1)[0];
  return Boolean(widget && live && widget === live);
}

let origin: DOMRect | null = null;

export function setDeskOrigin(node: Element | DOMRect | null): void {
  if (!node) {
    origin = null;
    return;
  }
  origin = node instanceof DOMRect ? node : node.getBoundingClientRect();
}

export function takeDeskOrigin(): DOMRect | null {
  const rect = origin;
  origin = null;
  return rect;
}
