export type SurfaceState = "closed" | "loading" | "stage" | "focus";
export type TopicStatus = "想法" | "待验证" | "写作中";
export type StageView = "board" | "list";
export type TimelineFilter = "all" | "conversation" | "activity";
export type TimelineKind = "user" | "assistant" | "activity";
export type ActivityStatus = "running" | "done" | "failed";

export interface Topic {
  id: number;
  title: string;
  note: string;
  status: TopicStatus;
  sources: number;
}

export interface TimelineEntry {
  id: number;
  kind: TimelineKind;
  text: string;
  ts: number;
  plugin?: string;
  action?: string;
  object?: string;
  detail?: string;
  status?: ActivityStatus;
  expanded?: boolean;
}

export interface CapabilitySurfaceSnapshot {
  version: 2;
  surface: "closed" | "stage";
  hasOpened: boolean;
  selectedId: number | null;
  draft: string;
  stageView: StageView;
  topics: Topic[];
  timeline: TimelineEntry[];
}

const STORAGE_KEY = "yibao-capability-surface-v2";

export const DEFAULT_TOPICS: Topic[] = [
  { id: 1, title: "应用消失之后，任务如何拥有屏幕", note: "从页面导航转向能力表面", status: "想法", sources: 3 },
  { id: 2, title: "AI OS 的关键不是万能输入框", note: "状态、权限与可逆性才是底座", status: "待验证", sources: 7 },
  { id: 3, title: "插件不是目的地，而是临时长出的手", note: "用译宝的真实交互做开场", status: "写作中", sources: 11 },
  { id: 4, title: "为什么 Agent 不该自动抢焦点", note: "从桌面心流讨论主动权", status: "待验证", sources: 5 },
  { id: 5, title: "从应用接力到对象接力", note: "邮件、日历和提醒的协作模型", status: "想法", sources: 4 },
];

export function createDefaultTimeline(now = Date.now()): TimelineEntry[] {
  return [
    { id: 1, kind: "user", text: "把最近关于 AI OS 的想法整理成一个选题", ts: now - 72_000 },
    {
      id: 2,
      kind: "activity",
      plugin: "自媒体",
      action: "整理选题",
      text: "读取产品笔记并归纳方向",
      detail: "引用 3 份产品笔记，生成 5 个候选选题，并按成熟度放入看板。",
      status: "done",
      ts: now - 61_000,
    },
    {
      id: 3,
      kind: "assistant",
      text: "我整理了 5 个方向，并按成熟度放进了选题看板。可以直接展开工作面，也可以继续告诉我筛选标准。",
      ts: now - 56_000,
    },
  ];
}

export function createDefaultSnapshot(): CapabilitySurfaceSnapshot {
  return {
    version: 2,
    surface: "closed",
    hasOpened: false,
    selectedId: null,
    draft: "",
    stageView: "board",
    topics: DEFAULT_TOPICS.map((topic) => ({ ...topic })),
    timeline: createDefaultTimeline(),
  };
}

function isSnapshot(value: unknown): value is CapabilitySurfaceSnapshot {
  if (!value || typeof value !== "object") return false;
  const snapshot = value as Partial<CapabilitySurfaceSnapshot>;
  return snapshot.version === 2
    && Array.isArray(snapshot.topics)
    && Array.isArray(snapshot.timeline)
    && (snapshot.surface === "closed" || snapshot.surface === "stage")
    && (snapshot.stageView === "board" || snapshot.stageView === "list");
}

export function loadCapabilitySurfaceSnapshot(): CapabilitySurfaceSnapshot | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    return isSnapshot(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export function saveCapabilitySurfaceSnapshot(snapshot: CapabilitySurfaceSnapshot): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(snapshot));
  } catch {
    // 原型在隐私模式或受限 WebView 中仍应可用；持久化失败只降级为内存态。
  }
}

export function clearCapabilitySurfaceSnapshot(): void {
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // 同上，清理失败不影响当前会话。
  }
}
