// 地平线仪器条（design/2026-08-28-agent-native-workbench-design.md §5/§10-P0）的纯逻辑。
// 组件只是渲染，映射规则全部收在这里，测试见 horizon.test.ts。
import type { AvatarState, FeedItem } from "../brain";

export interface HorizonNode {
  id: number;
  label: string;
  hot: boolean;
  kind: FeedItem["kind"];
}

export function horizonNodes(items: FeedItem[], _now: number, max = 6): HorizonNode[] {
  const sorted = items.filter((it) => it.status !== "ignore").sort((a, b) => a.ts - b.ts);
  const tail = sorted.length > max ? sorted.slice(-max) : sorted;
  return tail.map((it) => ({ id: it.id, label: hhmm(it.ts), hot: it.read === 0, kind: it.kind }));
}

function hhmm(ts: number): string {
  const d = new Date(ts);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

export type HorizonEchoTone = "busy" | "ok" | "warn";

export interface HorizonEcho {
  text: string;
  tone: HorizonEchoTone;
}

/** 最近一条过程行（useChatFlow 的 proc 行裁去展示字段）+ 当前化身态 → echo 位内容。 */
export function horizonEcho(input: {
  state: AvatarState;
  proc: { label: string; done: boolean; ok?: boolean } | null;
}): HorizonEcho | null {
  const { proc } = input;
  if (!proc) return null;
  if (!proc.done) return { text: `${proc.label} …`, tone: "busy" };
  return proc.ok === false
    ? { text: `${proc.label} ✗`, tone: "warn" }
    : { text: `${proc.label} ✓`, tone: "ok" };
}
