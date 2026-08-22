/** 主屏对话宿主提供给 thread / paper 摊法的会话。 */
import type { ComputedRef, InjectionKey, Ref } from "vue";
import type { BrainEvent, RunMetrics } from "../lib/brain";
import type { AvatarState } from "../protocol/brain-types";
import type { ThreadItem, PaperPage } from "../lib/work-thread";

export type HomeAvatarState = AvatarState;

export type ProcInfo = {
  label: string;
  action?: BrainEvent["action"];
  result?: BrainEvent["result"];
  done: boolean;
  expanded: boolean;
};

export type RunRef = { label: string; detail: string; ok: boolean };

export type BubbleMsg = {
  id?: string;
  role: "user" | "ai" | "sys";
  text: string;
  panelLink?: boolean;
  proc?: ProcInfo;
  halted?: boolean;
  icon?: "clock" | "alert";
  ts?: number;
  refs?: RunRef[];
  refsOpen?: boolean;
  metrics?: RunMetrics;
};

export type SuggestChip = { text: string; icon: "sparkle" | "doc" | "chat" };

export type HomeChatSession = {
  bubbles: Ref<BubbleMsg[]>;
  thread: ComputedRef<ThreadItem[]>;
  state: Ref<HomeAvatarState>;
  greeting: ComputedRef<string>;
  suggestChips: SuggestChip[];
  showTyping: ComputedRef<boolean>;
  streamingIdx: Ref<number | null>;
  thinkNote: Ref<string>;
  showJump: Ref<boolean>;
  bubblesRef: Ref<HTMLElement | null>;
  pages: ComputedRef<PaperPage[]>;
  pageIndex: Ref<number>;
  page: ComputedRef<PaperPage | null>;
  paperEmpty: ComputedRef<boolean>;
  paperDuty: ComputedRef<boolean>;
  paperTitle: ComputedRef<string>;
  paperLabel: ComputedRef<string>;
  stampLabels: ComputedRef<string[]>;
  peekOpen: Ref<boolean>;
  livePathLine: ComputedRef<string | null>;
  threadKey: (item: ThreadItem) => string;
  submit: (text: string) => void;
  fmtDay: (ts?: number) => string;
  openPanel: () => void;
  procOk: (p: ProcInfo) => boolean;
  procErrSuffix: (p: ProcInfo) => string;
  procText: (p: ProcInfo) => string;
  paperShowProc: (p: ProcInfo) => boolean;
  runRefsOf: (indices: number[]) => BubbleMsg | undefined;
  toggleRunRefs: (indices: number[]) => void;
  runShowFooter: (indices: number[]) => boolean;
  runMetricsOf: (indices: number[]) => RunMetrics | undefined;
  runHalted: (indices: number[]) => boolean;
  copyRun: (indices: number[]) => void;
  copyText: (t: string) => void;
  onFeedback: (ok: boolean) => void;
  regenerate: (i: number) => void;
  onEditMessage: (i: number) => void;
  onBubblesScroll: () => void;
  scrollBubbles: (smooth: boolean) => void;
  flipPage: (delta: number) => void;
  noticeFor: (b: BubbleMsg) => { summary: string; detail: string } | null | undefined;
};

export const HOME_CHAT_SESSION: InjectionKey<HomeChatSession> = Symbol("home-chat-session");
