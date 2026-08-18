<script setup lang="ts">
// 大窗「对话」页：与宠物窗同一条大脑会话（surface=pet），事件处理同源 App.vue 气泡流；
// 差异：不管窗（无展开/收起/说话气泡），「⇢ 协作」关联气泡可点击跳插件页。
// 宠物窗隐藏时本页仍在后台收事件——同一条会话两边镜面，切回去气泡不丢。
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import Avatar from "./Avatar.vue";
import InputBar from "./InputBar.vue";
import Bubble from "./Bubble.vue";
import PermissionsBanner from "./PermissionsBanner.vue";
import SetupWizard from "./SetupWizard.vue";
import BrainSession from "./BrainSession.vue";
import HomeContextPanel from "./HomeContextPanel.vue";
import {
  onBrainEvent,
  onBrainStatus,
  onBrainPermissions,
  onPanelClosed,
  runInput,
  voiceStart,
  interrupt,
  type BrainEvent,
  type BrainPermissions,
  type BrainStatusMsg,
} from "../lib/brain";
import { procLabel, procSkip, procResultSuffix, procDetail } from "../lib/proc";
import { formatContextPrefix, type InputContext } from "../lib/at-mention";
import type { RunMetrics } from "../lib/brain";
import { sessionStore } from "../state/store";
import { newId } from "../state/domains/conversation";
import type { MessageInput } from "../state/domains/conversation";
import type { Message } from "../state/types";
import YbIcon from "./YbIcon.vue";
import UsageBar from "./UsageBar.vue";

type AvatarState = "idle" | "listen" | "think" | "work" | "say" | "success" | "error";
// proc：过程展示（工具调用行，可点开展开参数/结果）；panelLink：「⇢ 协作」关联气泡
type ProcInfo = { label: string; action?: BrainEvent["action"]; result?: BrainEvent["result"]; done: boolean; expanded: boolean };
/** 溯源引用：本条 AI 回复调用过什么工具/记忆（"参考了 ▾"展开） */
type RunRef = { label: string; detail: string; ok: boolean };
type BubbleMsg = {
  /** 稳定 id：持久化增量跟踪用（渲染忽略） */
  id?: string;
  role: "user" | "ai" | "sys";
  text: string;
  panelLink?: boolean;
  proc?: ProcInfo;
  halted?: boolean;
  icon?: "clock" | "alert";
  /** 时间戳：跨日会话的日期分隔 */
  ts?: number;
  /** 溯源引用（仅 AI 消息） */
  refs?: RunRef[];
  /** 溯源折叠展开态（仅 AI 消息） */
  refsOpen?: boolean;
  /** 本次 run 统计（token/费用/耗时）：AI 终复气泡挂 indicator bar */
  metrics?: RunMetrics;
};

function bubbleToInput(b: BubbleMsg, ephemeral = false): MessageInput {
  return {
    id: b.id,
    role: b.role,
    payload: {
      text: b.text,
      panelLink: b.panelLink,
      halted: b.halted,
      icon: b.icon,
      refs: b.refs,
      metrics: b.metrics,
      proc: b.proc
        ? {
            label: b.proc.label,
            done: b.proc.done,
            ok: b.proc.done ? b.proc.result?.success !== false : undefined,
          }
        : undefined,
    },
    ts: b.ts,
    ephemeral,
  };
}

function msgToBubble(m: Message): BubbleMsg {
  return {
    id: m.id,
    role: m.role,
    text: m.payload.text,
    panelLink: m.payload.panelLink,
    halted: m.payload.halted,
    icon: m.payload.icon,
    ts: m.ts,
    refs: m.payload.refs,
    metrics: m.payload.metrics,
    proc: m.payload.proc
      ? {
          label: m.payload.proc.label,
          done: m.payload.proc.done,
          expanded: false,
          result: m.payload.proc.ok === undefined ? undefined : { success: m.payload.proc.ok },
        }
      : undefined,
  };
}

/** 持久化当前会话的指定气泡（新增/更新），返回带 id 的气泡 */
function persistBubble(b: BubbleMsg, ephemeral = false): BubbleMsg {
  if (!currentSessionId.value) return b;
  const stored = sessionStore.conversation.appendMessage(currentSessionId.value, bubbleToInput(b, ephemeral));
  if (!b.id) b.id = stored.id;
  else void stored;
  return b;
}

/** 流式/过程行结束收尾：按 id 更新已持久化消息 */
function syncBubble(b: BubbleMsg): BubbleMsg {
  if (!currentSessionId.value || !b.id) return b;
  const stored = sessionStore.conversation.syncMessage(currentSessionId.value, bubbleToInput(b));
  if (!b.id) b.id = stored.id;
  return b;
}

/** 技能/场景快速呼出 chip（动态：预设场景 + list_plugins） */
type SkillChip = { key: string; label: string; icon: "clock" | "doc" | "sparkle" | "chat" | "plug"; draft: string };

/** 告警气泡：⚠️ 前缀改行首 alert 图标渲染（文案纯净，图标走 YbIcon） */
function pushWarn(text: string) {
  const b: BubbleMsg = { role: "ai", text, icon: "alert", ts: Date.now() };
  bubbles.value.push(b);
  persistBubble(b);
}

// state：同步给父级顶栏状态；openPanel：关联气泡点击 → 在当前任务展开能力工作面；reminder：父级切回本页
const emit = defineEmits<{
  state: [AvatarState];
  openPanel: [];
  reminder: [];
  toggleLeft: [];
  toggleRight: [];
}>();
// draft：主屏 Feed/信息面板点击带过来的自包含草稿，直接转给 InputBar（它自己 watch 填入+聚焦）
const props = withDefaults(defineProps<{
  draft?: string;
  leftRailOpen?: boolean;
  rightRailOpen?: boolean;
}>(), {
  leftRailOpen: true,
  rightRailOpen: true,
});

// 本地草稿：父级 draft 单向同步；右侧信息面板点动态也经此填入（强制重置触发 InputBar watch）
const draftRef = ref<string | undefined>(undefined);
watch(
  () => props.draft,
  (d) => {
    if (d) {
      draftRef.value = "";
      void nextTick(() => (draftRef.value = d));
    }
  },
);
/** 右侧信息面板点动态/回顾 → 带上下文进输入框（先清空、下一拍设回，强制触发）。 */
function onInfoChat(d: string) {
  draftRef.value = "";
  void nextTick(() => (draftRef.value = d));
}

// ---- 会话（左复合栏）：标题/预览随对话更新；切换会话保存/恢复气泡（SessionStore.conversation 权威）----
const sessionRef = ref<InstanceType<typeof BrainSession> | null>(null);
const currentSessionId = ref(sessionStore.conversation.getActiveConversationId() ?? "");
let sessionStarted = false; // 当前会话是否已有首条用户消息（决定是否生成标题）

function readSessionTitle(id: string): string {
  const meta = sessionStore.conversation.getConversation(id);
  return meta?.title?.trim() || "新对话";
}

const currentSessionTitle = ref(readSessionTitle(currentSessionId.value));
/** 在途流式回复所属会话（null=无流式）：流式中切走再切回时，final_reply 不重复建气泡 */
let streamingConvId: string | null = null;

/** 恢复目标会话气泡：从 Rust 权威重拉（内存缓存可能被在途 run / 别的窗口写过） */
async function restoreConversation(id: string) {
  const msgs = await sessionStore.conversation.loadMessages(id);
  const restored = msgs.map(msgToBubble);
  bubbles.value = restored;
  // 气泡重建 → 依赖下标的瞬态全部作废（否则 action_result 会更新错位置）
  procIdx.clear();
  runRefs.length = 0;
  sessionStarted = restored.some((bubble) => bubble.role === "user");
  currentSessionTitle.value = readSessionTitle(id);
}
function onSessionActive(id: string) {
  currentSessionId.value = id;
  void sessionStore.conversation.setActiveConversationId(id);
  void restoreConversation(id);
}
/** 新建会话：会话创建由 SessionList.newChat 完成（emit newChat + active），这里只重置 UI。
 *  currentSessionId 由随后的 onSessionActive(id) 设置（含 loadMessages 恢复空会话）。 */
function onSessionNew() {
  bubbles.value = [];
  streamingIdx.value = null;
  state.value = "idle"; // showTyping 由 state 推导，自动收起
  sessionStarted = false;
  currentSessionTitle.value = "新对话";
}
function onSessionSelect(id: string) {
  // 切到目标会话：domain 内存由 onEvent 双写保持最新，直接恢复目标（补拉非活跃会话消息）
  currentSessionId.value = id;
  void sessionStore.conversation.setActiveConversationId(id);
  void restoreConversation(id);
  streamingIdx.value = null;
  state.value = "idle";
}

const state = ref<AvatarState>("idle");
const bubbles = ref<BubbleMsg[]>([]);
const streamingIdx = ref<number | null>(null); // 正在接收 chunk 的 bubble 下标
const brainDown = ref(false); // 大脑掉线/重启中（守护在恢复）
const panelOpen = ref(false); // 面板协作会话进行中（关联气泡只插一次）
const perms = ref<BrainPermissions | null>(null); // macOS 权限状态（null=未收到）
// 过程展示：action.id → 过程行下标，结果回来原地更新 ✅/❌
const procIdx = new Map<string, number>();
// 溯源：本次 run 调用的工具引用，挂到下一条 AI 消息（"参考了 ▾"）
const runRefs: RunRef[] = [];

// ---- 首启设置向导（缺 LLM key 时 Rust 发 setup-config-needed，大脑未启动；逻辑同源宠物窗）----
const setupNeeded = ref(false);
const setupCfg = ref({ model: "glm-4.6", baseUrl: "", voice: "zh-CN-XiaoxiaoNeural" });
async function onSetupNeeded() {
  setupNeeded.value = true;
  try {
    setupCfg.value = await invoke("get_setup_config");
  } catch { /* 用默认值 */ }
}
function onSetupSaved() {
  setupNeeded.value = false;
  const b: BubbleMsg = { role: "sys", text: "配置已保存，大脑启动中…", ts: Date.now() };
  bubbles.value.push(b);
  persistBubble(b);
}

// success/error 是短暂 valence（不可打断），不算 busy
const busy = computed(() =>
  state.value === "listen" || state.value === "think" ||
  state.value === "work" || state.value === "say",
);
// ---- 空态：时间招呼 + 建议 chip（带线性图标，视觉更 OS）----
const greeting = computed(() => {
  const h = new Date().getHours();
  if (h < 6) return "夜深了";
  if (h < 12) return "早上好";
  if (h < 14) return "中午好";
  if (h < 18) return "下午好";
  return "晚上好";
});
const SUGGEST_CHIPS: { text: string; icon: "sparkle" | "doc" | "chat" }[] = [
  { text: "记一条闪念", icon: "sparkle" },
  { text: "看看选题看板", icon: "doc" },
  { text: "帮我写点什么", icon: "chat" },
];

// ---- 技能/场景快速呼出 chip：动态 = 预设场景 + list_plugins（与左栏技能同源）----
const PRESET_SKILLS: SkillChip[] = [
  { key: "schedule", label: "日程", icon: "clock", draft: "帮我整理最近的日程安排…" },
  { key: "write", label: "写作", icon: "doc", draft: "帮我起草…" },
  { key: "life", label: "生活", icon: "sparkle", draft: "给我点生活上的建议…" },
];
const plugins = ref<{ id: string; name: string }[]>([]);
const skillChips = computed<SkillChip[]>(() => [
  ...PRESET_SKILLS,
  ...plugins.value.slice(0, 2).map((p) => ({ key: p.id, label: p.name, icon: "plug" as const, draft: `打开${p.name}面板` })),
]);
function onSkillChip(c: SkillChip) {
  onInfoChat(c.draft); // 点击 = 填入输入框（可补全再发送）
}

const missingPerms = computed(() => perms.value !== null && (!perms.value.ax || !perms.value.screen || !perms.value.input));
// 「正在输入」占位：run 受理（think）到首个 chunk 之间气泡流还是空的，用三点呼吸占位
const showTyping = computed(() => state.value === "think" && streamingIdx.value === null);
const sessionProcesses = computed(() => bubbles.value
  .filter((bubble): bubble is BubbleMsg & { proc: ProcInfo } => Boolean(bubble.proc))
  .slice(-5)
  .map((bubble) => ({
    label: bubble.proc.label,
    done: bubble.proc.done,
    ok: bubble.proc.done ? procOk(bubble.proc) : undefined,
  })));

// ---- 思考状态文案：typing 时轮换"在干嘛"（需在 showTyping 定义后，避免 TDZ）----
const THINK_NOTES = ["正在整理思路…", "正在翻阅记忆…", "正在连接工具…", "马上就好…"];
const thinkNote = ref(THINK_NOTES[0]);
let thinkNoteTimer: ReturnType<typeof setInterval> | null = null;
watch(showTyping, (v) => {
  if (thinkNoteTimer !== null) { clearInterval(thinkNoteTimer); thinkNoteTimer = null; }
  if (v) {
    thinkNote.value = THINK_NOTES[0];
    thinkNoteTimer = setInterval(() => {
      thinkNote.value = THINK_NOTES[Math.floor(Math.random() * THINK_NOTES.length)];
    }, 2400);
  }
});

// ---- 日期分隔（跨日会话）+ 跳到最新 ----
function sameDay(a?: number, b?: number): boolean {
  if (!a || !b) return false;
  const da = new Date(a);
  const db = new Date(b);
  return da.getFullYear() === db.getFullYear() && da.getMonth() === db.getMonth() && da.getDate() === db.getDate();
}
function showDateDivider(i: number): boolean {
  const b = bubbles.value[i];
  if (!b.ts) return false;
  const prev = i > 0 ? bubbles.value[i - 1].ts : undefined;
  return prev === undefined || !sameDay(prev, b.ts);
}
function fmtDay(ts?: number): string {
  if (!ts) return "";
  const d = new Date(ts);
  const now = new Date();
  const yd = new Date();
  yd.setDate(now.getDate() - 1);
  if (sameDay(ts, now.getTime())) return "今天";
  if (sameDay(ts, yd.getTime())) return "昨天";
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}
const showJump = ref(false);
function onBubblesScroll() {
  const el = bubblesRef.value;
  if (!el) return;
  const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 140;
  showJump.value = !nearBottom && el.scrollHeight > el.clientHeight + 60;
}

// ---- 消息操作：复制 / 重新生成 / 编辑重发 / 反馈 ----
const editTarget = ref<number | null>(null); // 编辑重发：用户消息下标，发送时从该条起截断替换
function copyText(t: string) {
  void navigator.clipboard?.writeText(t).catch(() => {});
}
function onEditMessage(i: number) {
  const b = bubbles.value[i];
  if (!b || b.role !== "user") return;
  draftRef.value = "";
  void nextTick(() => {
    draftRef.value = b.text;
    editTarget.value = i;
  });
}
function onFeedback(ok: boolean) {
  const b: BubbleMsg = { role: "sys", text: ok ? "已收到正面反馈，会继续保持" : "已收到反馈，会调整回答方式", ts: Date.now() };
  bubbles.value.push(b);
  persistBubble(b);
}
/** 重新生成/重试：找到该 AI 消息前最近一条用户消息，截断到它（含）重新 runInput */
async function regenerate(i: number) {
  const target = bubbles.value[i];
  if (!target || target.role !== "ai") return;
  let j = i - 1;
  while (j >= 0 && bubbles.value[j].role !== "user") j -= 1;
  if (j < 0) return;
  const text = bubbles.value[j].text;
  bubbles.value = bubbles.value.slice(0, j + 1);
  if (currentSessionId.value) sessionStore.conversation.truncateMessages(currentSessionId.value, j + 1);
  streamingIdx.value = null;
  state.value = "think";
  try {
    await Promise.race([
      runInput(text, "pet", currentSessionId.value),
      new Promise<never>((_, rej) => setTimeout(() => rej(new Error("大脑响应超时")), 15000)),
    ]);
  } catch (err) {
    pushWarn("重新生成失败：" + String(err));
    state.value = "idle";
  }
}

// ---- 气泡流滚动：新气泡平滑到底、流式 chunk 即时跟手 ----
const bubblesRef = ref<HTMLElement | null>(null);
function scrollBubbles(smooth: boolean) {
  void nextTick(() => {
    const el = bubblesRef.value;
    if (!el) return;
    if (smooth) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    else el.scrollTop = el.scrollHeight;
  });
}
watch(() => bubbles.value.length, () => scrollBubbles(true));
watch(() => bubbles.value[bubbles.value.length - 1]?.text, () => scrollBubbles(false));
watch(showTyping, () => scrollBubbles(true));
watch(state, (s) => emit("state", s));
// 持久化不在 deep watch 全量写：各事件点显式 appendMessage/syncMessage（流式 chunk 只改内存，消除写放大）

let unlisten: (() => void) | null = null;
let unlistenStatus: (() => void) | null = null;
let unlistenPerms: (() => void) | null = null;
let unlistenPanelClosed: (() => void) | null = null;
let unlistenSetup: (() => void) | null = null;
let unlistenSetupErr: (() => void) | null = null;
let unlistenSetupCfg: (() => void) | null = null;
let unlistenUpdated: (() => void) | null = null;

function onEvent(e: BrainEvent) {
  // 会话分流：面板场景的对话事件只归插件页；panel 事件例外（关联气泡，本页也收）
  if (e.surface && e.surface !== "pet" && e.kind !== "panel") return;
  // M3 会话归属过滤：事件带 conversationId 且不属于当前会话 → 跳过渲染
  // （已由 Rust 落库到所属会话，切到该会话即见；流式中切会话不污染当前视图）
  if (e.conversationId && currentSessionId.value && e.conversationId !== currentSessionId.value) return;
  switch (e.kind) {
    case "action_proposed":
      state.value = "work";
      // 过程行：🔧 技能短标签（use_plugin 跳过——成功有 notice，不重复）
      if (e.action?.id && !procSkip(e.action)) {
        const procBubble: BubbleMsg = {
          id: newId(),
          role: "sys",
          text: "",
          proc: { label: procLabel(e.action), action: e.action, done: false, expanded: false },
          ts: Date.now(),
        };
        procIdx.set(e.action.id, bubbles.value.length);
        bubbles.value.push(procBubble);
        persistBubble(procBubble);
        runRefs.push({ label: procLabel(e.action), detail: "调用工具中…", ok: false });
      }
      break;
    case "action_result": {
      // 确认可能在别处作答，结果回来即收尾（成功短闪 400ms）
      flashValence("success");
      // 过程行收尾：✅/❌ + 结果存好（点「详情」展开看参数/输出）
      const idx = e.action?.id !== undefined ? procIdx.get(e.action.id) : undefined;
      if (idx !== undefined) {
        const p = bubbles.value[idx].proc;
        if (p) {
          p.done = true;
          p.result = e.result;
        }
        const bubble = bubbles.value[idx];
        if (bubble && bubble.id) syncBubble(bubble);
        procIdx.delete(e.action!.id!);
      }
      // 溯源收尾：把该工具的结果摘要写回最近一条未完成引用
      const ref = runRefs.find((r) => !r.ok && r.label === (e.action ? procLabel(e.action) : ""));
      if (ref) {
        ref.ok = e.result?.success !== false;
        ref.detail = e.result?.success
          ? String(e.result?.data?.human ?? "")?.slice(0, 60) || "已完成"
          : `失败：${String(e.result?.error ?? "").slice(0, 60)}`;
      }
      break;
    }
    case "final_reply_chunk":
      // 流式增量：拼到当前 streaming bubble（首片时新建；同时挂上本次 run 的溯源引用）。
      // 持久化策略：chunk 只改内存，final_reply 结束时 syncMessage 一次性落盘（消除写放大）。
      if (streamingIdx.value === null) {
        const chunkBubble: BubbleMsg = {
          id: newId(),
          role: "ai",
          text: e.text ?? "",
          ts: Date.now(),
          refs: runRefs.length ? [...runRefs] : undefined,
        };
        bubbles.value.push(chunkBubble);
        if (currentSessionId.value) {
          sessionStore.conversation.appendMessage(currentSessionId.value, bubbleToInput(chunkBubble));
        }
        runRefs.length = 0;
        streamingIdx.value = bubbles.value.length - 1;
        streamingConvId = e.conversationId || currentSessionId.value; // 记在途流式归属
      } else {
        bubbles.value[streamingIdx.value].text += e.text ?? "";
      }
      break;
    case "final_reply": {
      // 以完整文本为准收尾（兜底 chunk 丢失）；语音中保持 say 等 speaking_done
      const full = e.text ?? "";
      // run 统计（sidecar 聚合进 final_reply 的 payload.metrics）：挂到本条 AI 回复的 indicator bar
      const metrics: RunMetrics | undefined = (e.payload as { metrics?: RunMetrics } | undefined)?.metrics;
      const wasStreamed = streamingConvId !== null && streamingConvId === (e.conversationId || currentSessionId.value);
      streamingConvId = null;
      if (streamingIdx.value !== null) {
        bubbles.value[streamingIdx.value].text = full;
        const streamed = bubbles.value[streamingIdx.value];
        if (metrics) streamed.metrics = metrics;
        if (streamed.id) syncBubble(streamed); // 流式终态落盘
        streamingIdx.value = null;
      } else if (wasStreamed) {
        // 流式期间切走过又切回：Rust 已 update 首片消息为终态，此处从权威重拉，
        // 不新建气泡（否则与重拉到的首片消息重复）。
        const convId = currentSessionId.value;
        if (convId) {
          void sessionStore.conversation.loadMessages(convId).then((msgs) => {
            bubbles.value = msgs.map(msgToBubble);
            procIdx.clear();
          });
        }
        runRefs.length = 0;
      } else {
        const finalBubble: BubbleMsg = {
          id: newId(),
          role: "ai",
          text: full,
          ts: Date.now(),
          refs: runRefs.length ? [...runRefs] : undefined,
          metrics,
        };
        bubbles.value.push(finalBubble);
        persistBubble(finalBubble);
        runRefs.length = 0;
      }
      sessionRef.value?.updateCurrent({ preview: full.replace(/\s+/g, " ").trim().slice(0, 44) });
      if (state.value !== "say") state.value = "idle";
      break;
    }
    case "interrupted":
      runRefs.length = 0; // 打断：本次 run 的引用作废
      if (streamingIdx.value !== null) {
        const haltedBubble = bubbles.value[streamingIdx.value];
        haltedBubble.halted = true;
        if (haltedBubble.id) syncBubble(haltedBubble);
        streamingIdx.value = null;
      } else {
        const interruptedBubble: BubbleMsg = { role: "ai", text: "已打断", halted: true, ts: Date.now() };
        bubbles.value.push(interruptedBubble);
        persistBubble(interruptedBubble);
      }
      state.value = "idle";
      break;
    case "speaking_done":
      state.value = "idle";
      break;
    case "notice":
      // 轻提示（插件展开等，§12-2 要知情）：居中淡色小字，不弹窗不打断
      {
        const noticeBubble: BubbleMsg = { role: "sys", text: e.text ?? "", ts: Date.now() };
        bubbles.value.push(noticeBubble);
        persistBubble(noticeBubble);
      }
      break;
    case "reminder":
      // 主动提醒：落气泡 + 通知父级切回本页（大窗已可见，宠物窗自己管亮窗，两边互不抢）
      {
        const reminderBubble: BubbleMsg = { role: "ai", text: e.text ?? "到点了", icon: "clock", ts: Date.now() };
        bubbles.value.push(reminderBubble);
        persistBubble(reminderBubble);
      }
      emit("reminder");
      break;
    case "error":
      state.value = "idle";
      streamingIdx.value = null;
      runRefs.length = 0;
      pushWarn(e.text ?? "出错了");
      flashValence("error");
      break;
    case "listening":
      state.value = "listen";
      break;
    case "listening_done":
      // 空识别（超时/没说话）：回 idle 并提示——不能进 think，run_done 不复位状态，会永远卡「思考中」
      if (e.text) {
        state.value = "think";
        const userBubble: BubbleMsg = { role: "user", text: e.text, ts: Date.now() };
        bubbles.value.push(userBubble);
        persistBubble(userBubble);
      } else {
        state.value = "idle";
        const missBubble: BubbleMsg = { role: "ai", text: "没听清，再试一次？", ts: Date.now() };
        bubbles.value.push(missBubble);
        persistBubble(missBubble);
      }
      break;
    case "speaking":
      state.value = "say";
      break;
    case "panel": {
      // 面板先成为当前任务的可恢复能力；不主动抢页面，用户从关联卡/活动胶囊展开。
      // 查重：最近一条「⇢ 协作」气泡存在（含重启恢复的旧气泡）→ 原地更新文案，不新增（修重启重复 push bug）
      const title = e.payload?.title || e.payload?.panel || "插件面板";
      const text = `⇢ 正在和「${title}」协作`;
      const existing = [...bubbles.value].reverse().find((b) => b.panelLink);
      if (existing) {
        existing.text = text;
        if (currentSessionId.value) sessionStore.conversation.upsertPanelLink(currentSessionId.value, text);
      } else {
        const linkBubble: BubbleMsg = { role: "ai", text, panelLink: true, ts: Date.now() };
        bubbles.value.push(linkBubble);
        if (currentSessionId.value) {
          const stored = sessionStore.conversation.upsertPanelLink(currentSessionId.value, text);
          linkBubble.id = stored.id; // 与 domain 消息 id 对齐，后续 syncMessage 可命中
        }
      }
      panelOpen.value = true;
      break;
    }
  }
}

// ---- 过程展示辅助（模板用）----
function procOk(p: ProcInfo): boolean {
  return p.result?.success !== false;
}
function procErrSuffix(p: ProcInfo): string {
  return procResultSuffix(p.result);
}
function procText(p: ProcInfo): string {
  return procDetail(p.action, p.result);
}

function onStatus(m: BrainStatusMsg) {
  if (m.status === "up") {
    if (brainDown.value) {
      brainDown.value = false;
      const b: BubbleMsg = { role: "ai", text: "✓ 大脑已恢复", ts: Date.now() };
      bubbles.value.push(b);
      persistBubble(b);
    }
    return;
  }
  // down / restarting：复位界面状态（进行中的 run/确认已随进程丢失）
  state.value = "idle";
  streamingIdx.value = null;
  if (!brainDown.value) {
    brainDown.value = true;
    const why = m.detail ? `（${m.detail}）` : "";
    pushWarn(`大脑掉线${why}，正在自动重启…`);
  }
}

async function submit(text: string, contexts: InputContext[] = []) {
  const contextPrefix = formatContextPrefix(contexts);
  const messageText = `${contextPrefix}${text}`;
  // 无会话时确保存在（首启直接输入）：M3 下 run 必须带会话 id，否则消息不落库。
  // 大窗走 ensure_active_conversation；小窗走 ensure_pet_conversation（固定会话，两窗互不干扰）。
  if (!currentSessionId.value) {
    const meta = await invoke<{ id: string } | null>("ensure_active_conversation").catch(() => null);
    if (meta?.id) {
      currentSessionId.value = meta.id;
      await sessionStore.conversation.refreshConversations().catch(() => {});
      currentSessionTitle.value = readSessionTitle(meta.id);
      sessionRef.value?.syncSessions?.();
    }
  }
  // 编辑重发：从被编辑的用户消息起截断，用新文本替换（其后对话作废）
  if (editTarget.value !== null) {
    bubbles.value = bubbles.value.slice(0, editTarget.value);
    if (currentSessionId.value) sessionStore.conversation.truncateMessages(currentSessionId.value, editTarget.value);
    editTarget.value = null;
    runRefs.length = 0;
  }
  // 首条用户消息 → 自动生成会话标题
  if (!sessionStarted) {
    const title = text.replace(/\s+/g, " ").trim().slice(0, 16);
    const t = title || "新对话";
    sessionRef.value?.updateCurrent({ title: t });
    if (currentSessionId.value) sessionStore.conversation.updateMetaTitle(currentSessionId.value, t);
    currentSessionTitle.value = t;
    sessionStarted = true;
  }
  // 若 AI 正在生成/播报，InputBar 已在发送前 emit interrupt（先打断再发）；
  // 这里兜底：state 异常卡 busy（无响应）时也允许发送（runInput 会覆盖 state）
  const userBubble: BubbleMsg = { role: "user", text: messageText, ts: Date.now() };
  bubbles.value.push(userBubble);
  persistBubble(userBubble);
  state.value = "think";
  try {
    // 15s 超时兜底：runInput invoke 挂起会让 state 一直卡 think（主按钮变"打断"，发不出新消息）
    await Promise.race([
      runInput(messageText, "pet", currentSessionId.value),
      new Promise<never>((_, rej) => setTimeout(() => rej(new Error("大脑响应超时")), 15000)),
    ]);
  } catch (err) {
    pushWarn("发送失败：" + String(err));
    state.value = "idle";
  }
}

function onMic() {
  // 不乐观置 listen：等大脑 listening 事件确认（语音栈不可用时大脑会回 error，别自欺卡死）
  void voiceStart("pet", false, currentSessionId.value).catch((err) => {
    pushWarn("语音启动失败：" + String(err));
  });
}

function onInterrupt() {
  void interrupt().catch((err) => {
    pushWarn("打断失败：" + String(err));
  });
}

// ---- 短暂 valence（success/error）：400ms 闪现后回 idle，期间不可打断 ----
let valenceTimer: ReturnType<typeof setTimeout> | null = null;
function flashValence(v: "success" | "error") {
  if (valenceTimer) clearTimeout(valenceTimer);
  state.value = v;
  valenceTimer = setTimeout(() => {
    if (state.value === v) state.value = "idle";
    valenceTimer = null;
  }, 400);
}

onMounted(async () => {
  // SessionStore 恢复编排：hydrate 三域后按活跃会话恢复气泡（与小窗镜面共用同一条会话）
  await sessionStore.restore().catch(() => {});
  let activeId = sessionStore.conversation.getActiveConversationId();
  if (!activeId) {
    // 首启无会话（或小窗已建会话但本域未同步）：走 Rust 确保并刷新列表
    const meta = await invoke<{ id: string } | null>("ensure_active_conversation").catch(() => null);
    if (meta?.id) {
      await sessionStore.conversation.refreshConversations().catch(() => {});
      activeId = meta.id;
    }
  }
  if (activeId) {
    currentSessionId.value = activeId;
    await restoreConversation(activeId);
  }
  // 技能 chip 动态数据：与左栏技能同源（list_plugins），不另起炉灶
  try { plugins.value = await invoke<{ id: string; name: string }[]>("list_plugins").catch(() => []); } catch { plugins.value = []; }
  unlisten = await onBrainEvent(onEvent);
  unlistenStatus = await onBrainStatus(onStatus);
  unlistenPerms = await onBrainPermissions((p) => { perms.value = p; });
  unlistenPanelClosed = await onPanelClosed(() => {
    if (!panelOpen.value) return;
    panelOpen.value = false;
    const b: BubbleMsg = { role: "ai", text: "⇠ 协作结束", ts: Date.now() };
    bubbles.value.push(b);
    persistBubble(b);
  });
  // 首启引导（生产打包首跑：装 Python 环境/下模型，大脑还没起来，走 Tauri 事件直推）
  unlistenSetup = await listen<{ stage: string; detail: string }>("setup-progress", (e) => {
    const b: BubbleMsg = { role: "sys", text: e.payload.detail, ts: Date.now() };
    bubbles.value.push(b);
    persistBubble(b);
  });
  unlistenSetupErr = await listen<string>("setup-error", (e) => {
    pushWarn(e.payload);
  });
  unlistenSetupCfg = await listen<string>("setup-config-needed", () => void onSetupNeeded());
  // 跨窗镜面：小窗向当前会话发了消息 → 本页重拉（用户消息无事件流，只能靠此信号）
  unlistenUpdated = await listen<{ conversationId: string; from: string }>("conversation-updated", (e) => {
    const { conversationId, from } = e.payload;
    if (from === getCurrentWindow().label) return; // 自己发的，本窗已渲染
    if (!currentSessionId.value || conversationId !== currentSessionId.value) return; // 不是当前会话
    if (streamingIdx.value !== null) return; // 流式中不抢刷新（会重建气泡打断渲染）
    void sessionStore.conversation.loadMessages(conversationId).then((msgs) => {
      bubbles.value = msgs.map(msgToBubble);
      procIdx.clear(); // 过程行下标随气泡重建作废
    });
  });
  // 小窗已改为固定会话（不跟随大窗，也不再广播切会话）→ 大窗无需订阅 active-conversation-changed
  // 主动拉一次配置：setup-config-needed 可能先于挂载发出而丢——靠拉取兜底
  try {
    const cfg = await invoke<{ has_key: boolean }>("get_setup_config");
    if (!cfg.has_key) void onSetupNeeded();
  } catch { /* 忽略，事件路径仍兜底 */ }
  emit("state", state.value); // 父级侧边栏团子拿初始态
});
onUnmounted(() => {
  unlisten?.();
  unlistenStatus?.();
  unlistenPerms?.();
  unlistenPanelClosed?.();
  unlistenSetup?.();
  unlistenSetupErr?.();
  unlistenSetupCfg?.();
  unlistenUpdated?.();
  if (valenceTimer !== null) clearTimeout(valenceTimer);
  if (thinkNoteTimer !== null) clearInterval(thinkNoteTimer);
});
</script>

<template>
  <!-- thinking：AI 思考时对话区泛紫微光（与左栏大脑转紫呼应） -->
  <div class="chat-page" :class="{ thinking: state === 'think' }">
    <SetupWizard v-if="setupNeeded" :model="setupCfg.model" :base-url="setupCfg.baseUrl" :voice="setupCfg.voice" @saved="onSetupSaved" />

    <!-- 三栏 AI 工作台：内心+会话（复合栏）｜对话｜AI 进程 -->
    <!-- 用 wrapper div 包左/右栏：scoped CSS 才能命中（直接 class 加在子组件根会因 scope 不匹配而失效） -->
    <div v-else class="chat-cols" :class="{ 'left-collapsed': !props.leftRailOpen, 'right-collapsed': !props.rightRailOpen }">
    <!-- 左栏折叠时露出 36×36 团子头像按钮（展开入口）；展开态无按钮——左栏有更多折叠方式（点空白/脑会话 tab 切换/键盘等），与右栏信息密度需求不同 -->
    <button v-if="!props.leftRailOpen" class="rail-avatar-reopen" type="button" title="展开左栏" aria-label="展开左栏" @click="emit('toggleLeft')">
      <Avatar :state="state" :size="28" compact />
    </button>
    <button
      class="rail-toggle rail-toggle-right"
      :class="{ collapsed: !props.rightRailOpen }"
      type="button"
      :aria-pressed="props.rightRailOpen"
      :title="props.rightRailOpen ? '隐藏右栏' : '显示右栏'"
      :aria-label="props.rightRailOpen ? '隐藏右栏' : '显示右栏'"
      @click="emit('toggleRight')"
    >
      <YbIcon name="panel-right" :size="14" />
    </button>
    <!-- 左：内心 + 会话 复合栏（tab 切换：AgentBrain 人格展示 / SessionList 历史导航） -->
    <div class="col-left"><BrainSession ref="sessionRef" :state="state" @chat="onInfoChat" @toggle="emit('toggleLeft')" @select="onSessionSelect" @active="onSessionActive" @new-chat="onSessionNew" /></div>

    <div class="chat-main">
    <PermissionsBanner v-if="missingPerms && perms" :perms="perms" />

    <div class="bubbles" ref="bubblesRef" @scroll="onBubblesScroll">
      <div v-if="!bubbles.length && !showTyping" class="empty-hint">
        <div class="eh-glow"><Avatar :state="state" :size="64" /></div>
        <p class="eh-title">{{ greeting }}，叫我做什么都行～</p>
        <p class="eh-sub">整理会议纪要 · 规划今日 · 记住你的偏好</p>
        <div class="chips">
          <button v-for="c in SUGGEST_CHIPS" :key="c.text" class="chip" @click="submit(c.text)">
            <YbIcon :name="c.icon" :size="12" />{{ c.text }}
          </button>
        </div>
      </div>
      <template v-for="(b, i) in bubbles" :key="i">
        <!-- 跨日日期分隔（今天/昨天/更早） -->
        <div v-if="showDateDivider(i)" class="date-divider"><span>{{ fmtDay(b.ts) }}</span></div>

        <!-- 「⇢ 协作」关联气泡：可点击，在当前任务内展开能力工作面。 -->
        <button v-if="b.panelLink" class="assoc" @click="emit('openPanel')">
          {{ b.text }}<span class="assoc-arrow">展开 ›</span>
        </button>

        <!-- 过程工作卡：图标 + 描述 + 进度条（进行中）/ ✅❌（完成），点「详情」展开参数与结果 -->
        <div v-else-if="b.proc" class="proc">
          <div
            class="proc-card"
            :class="{ done: b.proc.done && procOk(b.proc), fail: b.proc.done && !procOk(b.proc) }"
            :aria-expanded="b.proc.expanded"
            @click="b.proc && (b.proc.expanded = !b.proc.expanded)"
          >
            <YbIcon
              class="proc-ic"
              :name="b.proc.done ? (procOk(b.proc) ? 'check' : 'x') : 'spinner'"
              :spin="!b.proc.done"
              :size="13"
            />
            <div class="proc-main">
              <span class="proc-label">{{ b.proc.label }}{{ b.proc.done ? procErrSuffix(b.proc) : "" }}</span>
              <span v-if="!b.proc.done" class="proc-track"><i /></span>
            </div>
            <span class="proc-toggle">{{ b.proc.expanded ? "收起" : "详情" }}</span>
          </div>
          <pre v-if="b.proc.expanded" class="proc-detail">{{ procText(b.proc) }}</pre>
        </div>

        <!-- AI 消息：无气泡主文 + 头像 + hover 操作 + 溯源（"参考了 ▾"） -->
        <div v-else-if="b.role === 'ai'" class="msg-row">
          <div class="ai-line">
            <Avatar class="ai-ava" :state="state" :size="22" compact />
            <Bubble :role="b.role" :text="b.text" plain :streaming="i === streamingIdx" :halted="b.halted" :icon="b.icon" />
          </div>
          <!-- run 统计 indicator bar（token/费用/耗时；hover 看明细） -->
          <UsageBar v-if="b.metrics" :metrics="b.metrics" />
          <div v-if="b.refs?.length" class="refs">
            <button class="refs-toggle" @click="b.refsOpen = !b.refsOpen">
              <span>参考了 {{ b.refs.length }} 项</span>
              <i :class="{ open: b.refsOpen }" />
            </button>
            <Transition name="refs-fade">
              <ul v-if="b.refsOpen" class="refs-list">
                <li v-for="(r, ri) in b.refs" :key="ri" :class="{ fail: !r.ok }">
                  <YbIcon :name="r.ok ? 'check' : 'x'" :size="10" />
                  <span class="refs-label">{{ r.label }}</span>
                  <span class="refs-detail">{{ r.detail }}</span>
                </li>
              </ul>
            </Transition>
          </div>
          <div class="msg-actions">
            <button @click="copyText(b.text)">复制</button>
            <button :title="'有帮助'" @click="onFeedback(true)"><YbIcon name="thumb-up" :size="12" /></button>
            <button :title="'没帮助'" @click="onFeedback(false)"><YbIcon name="thumb-down" :size="12" /></button>
            <button @click="regenerate(i)">{{ b.halted ? "重试" : "重写" }}</button>
          </div>
        </div>

        <!-- 用户消息：气泡 + hover 复制/编辑（改完从该条起替换） -->
        <div v-else-if="b.role === 'user'" class="msg-row user-msg">
          <Bubble :role="b.role" :text="b.text" :streaming="i === streamingIdx" :halted="b.halted" :icon="b.icon" />
          <div class="msg-actions">
            <button @click="copyText(b.text)">复制</button>
            <button @click="onEditMessage(i)">编辑</button>
          </div>
        </div>

        <Bubble v-else :role="b.role" :text="b.text" :streaming="i === streamingIdx" :halted="b.halted" :icon="b.icon" />
      </template>

      <template v-if="showTyping">
        <div class="ai-line">
          <Avatar class="ai-ava" :state="state" :size="22" compact />
          <Bubble role="ai" text="" typing />
          <span class="think-note">{{ thinkNote }}</span>
        </div>
      </template>
    </div>

    <!-- 向上滚离底部时：跳到最新浮钮 -->
    <button v-show="showJump" class="jump-new" @click="scrollBubbles(true)">↓ 最新</button>

    <div class="input-slot">
      <div class="skill-row">
        <span class="skill-hint">呼出技能</span>
        <button v-for="c in skillChips" :key="c.key" class="skill-chip" :title="c.draft" @click="onSkillChip(c)">
          <YbIcon :name="c.icon" :size="11" />{{ c.label }}
        </button>
      </div>
      <InputBar :busy="busy" :listening="state === 'listen'" :draft="draftRef" @submit="submit" @mic="onMic" @interrupt="onInterrupt" />
    </div>
    </div>

    <!-- 右：只描述当前会话的目标、阻塞、上下文、关联能力与产出 -->
    <div class="col-context">
      <HomeContextPanel
        :session-id="currentSessionId"
        :session-title="currentSessionTitle"
        :session-state="state"
        :has-conversation="Boolean(bubbles.length)"
        :processes="sessionProcesses"
        @chat="onInfoChat"
      />
    </div>
    </div>
  </div>
</template>

<style scoped>
.chat-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--yb-content-bg);
}
/* 三栏工作台：内心+会话（左）｜对话（中）｜AI 进程（右） */
.chat-cols {
  flex: 1;
  min-height: 0;
  display: flex;
  min-width: 0;
  position: relative;
}
.rail-toggle {
  position: absolute;
  top: 8px;
  z-index: 10;
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  padding: 0;
  border: 1px solid transparent;
  border-radius: var(--yb-radius-sm);
  background: color-mix(in srgb, var(--yb-content-bg) 86%, transparent);
  color: var(--yb-text-faint);
  cursor: pointer;
  opacity: 0.74;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.rail-toggle:hover,
.rail-toggle:focus-visible {
  border-color: var(--yb-surface-border);
  background: var(--yb-surface-2);
  color: var(--yb-accent);
  opacity: 1;
}
.rail-toggle-right { right: 8px; }
.rail-toggle.collapsed {
  color: var(--yb-accent);
  opacity: 0.9;
}
.rail-avatar-reopen {
  position: absolute;
  left: 8px;
  top: 8px;
  z-index: 10;
  width: 36px;
  height: 36px;
  display: grid;
  place-items: center;
  padding: 0;
  border: 1px solid var(--yb-surface-border);
  border-radius: 50%;
  background: var(--yb-surface-2);
  box-shadow: var(--yb-shadow-soft);
  cursor: pointer;
}
.rail-avatar-reopen:hover,
.rail-avatar-reopen:focus-visible {
  border-color: var(--yb-accent);
  background: var(--yb-accent-soft);
}
.chat-cols.left-collapsed > .col-left,
.chat-cols.right-collapsed > .col-context {
  display: none;
}
.chat-main {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  position: relative; /* 锚定"跳到最新"浮钮 */
}
/* 宽屏全展开；逐档收栏保证对话区始终可用。
 * 左栏收起/展开由 Home.vue 的 leftRailOpen 状态控制（非全屏默认收起，按钮始终可点）；
 * 这里只保留右栏在极小屏的兜底。 */
@media (max-width: 900px) {
  .chat-cols > .col-context {
    display: none;
  }
  .rail-toggle-right {
    display: none;
  }
}
/* AI 思考：对话区泛紫微光（与 AgentBrain think 光晕呼应） */
.chat-page.thinking {
  background:
    radial-gradient(90% 60% at 50% 30%, rgba(142, 124, 240, 0.05), transparent 65%),
    var(--yb-content-bg);
}
/* AI 消息行：角色头像 + 气泡（人格化：团子本尊在说话） */
.ai-line {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  align-self: flex-start;
  max-width: 100%;
}
.ai-ava {
  flex-shrink: 0;
  margin-bottom: 2px;
  opacity: 0.92;
}
.bubbles {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: var(--yb-space-2);
  overflow-y: auto;
  padding: var(--yb-space-3) var(--yb-space-5) 0;
  scrollbar-width: thin;
  /* 顶部渐隐：滚出视口的消息柔和淡出，不被硬边「切断」 */
  mask-image: linear-gradient(180deg, transparent, #000 14px);
  -webkit-mask-image: linear-gradient(180deg, transparent, #000 14px);
}
/* 底部输入区：无边框无背景——InputBar 自带胶囊浮起，悬在对话下方更轻盈 */
.input-slot {
  flex-shrink: 0;
  padding: var(--yb-space-3) var(--yb-space-5) var(--yb-space-4);
}
/* 气泡内容限宽：AI 左 / 用户右自然交替；fit-content 由 Bubble 自身管，这里只封顶长内容。
 * 旧 min(70%, 640px) 在窄对话栏下压太狠，让短文本（"打开工具箱"）也被挤两行。 */
.bubbles :deep(.bubble) {
  max-width: min(88%, 720px);
}
/* 无气泡 AI 主文：放宽到 760px（主回复更舒展，结构化卡在 plain 层内） */
.bubbles :deep(.bubble.plain) {
  max-width: min(100%, 760px);
}
.bubbles::-webkit-scrollbar {
  width: 6px;
}
.bubbles::-webkit-scrollbar-thumb {
  background: var(--yb-surface-border);
  border-radius: var(--yb-radius-pill);
}
/* 「⇢ 协作」关联气泡：拟 AI 气泡但可点击，accent 细边 + hover 上浮（派生入口）
 * fit-content：短标题"⇢ 正在和「xxx」协作 展开 ›"按内容收缩，不被窄栏撑两行。 */
.assoc {
  align-self: flex-start;
  width: fit-content;
  max-width: 92%;
  display: inline-flex;
  align-items: center;
  gap: var(--yb-space-2);
  padding: var(--yb-space-2) var(--yb-space-3);
  border: 1px dashed var(--yb-accent);
  border-radius: var(--yb-radius-md) var(--yb-radius-md) var(--yb-radius-md) var(--yb-radius-xs);
  background: var(--yb-accent-soft);
  color: var(--yb-text);
  font-size: var(--yb-fs-lg);
  line-height: var(--yb-lh-base);
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  animation: pop var(--yb-dur-fast) var(--yb-ease-out);
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.assoc:hover {
  transform: translateY(-1px);
  box-shadow: var(--yb-shadow-soft);
}
.assoc-arrow {
  color: var(--yb-accent-deep);
  font-size: var(--yb-fs-md);
  white-space: nowrap;
}
/* 过程工作卡：白底圆角卡居中，工具图标 + 描述 + 进度条（进行中）/ ✅❌（完成） */
.proc {
  align-self: center;
  max-width: 92%;
  display: flex;
  flex-direction: column;
  align-items: center;
  animation: pop var(--yb-dur-fast) var(--yb-ease-out);
}
.proc-card {
  display: flex;
  align-items: center;
  gap: 9px;
  min-width: 220px;
  max-width: 100%;
  padding: 8px 10px 8px 12px;
  border: 1px solid rgba(var(--yb-c-sky-rgb), 0.12);
  border-radius: var(--yb-radius-md);
  background: rgba(255, 255, 255, 0.7);
  box-shadow: var(--yb-shadow-1), inset 0 1px 0 rgba(255, 255, 255, 0.9);
  color: var(--yb-text-dim);
  font-family: inherit;
  font-size: var(--yb-fs-xs);
  line-height: var(--yb-lh-base);
  cursor: pointer;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.proc-card:hover {
  border-color: rgba(var(--yb-c-sky-rgb), 0.3);
  box-shadow: var(--yb-shadow-2), inset 0 1px 0 rgba(255, 255, 255, 0.9);
}
.proc-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
/* 进行中进度条：accent 渐变流光 */
.proc-track {
  position: relative;
  height: 2px;
  border-radius: var(--yb-radius-pill);
  background: rgba(var(--yb-c-sky-rgb), 0.12);
  overflow: hidden;
}
.proc-track i {
  position: absolute;
  inset: 0;
  width: 40%;
  border-radius: var(--yb-radius-pill);
  background: linear-gradient(90deg, transparent, var(--yb-accent), transparent);
  animation: proc-slide 1.1s ease-in-out infinite;
}
@keyframes proc-slide {
  from { transform: translateX(-100%); }
  to { transform: translateX(350%); }
}
/* 进行中的转圈图标用 accent，成功转 success：颜色本身就是状态信号 */
.proc-ic {
  flex-shrink: 0;
  color: var(--yb-accent);
}
.proc-card.done .proc-ic {
  color: var(--yb-intent-ok);
}
.proc-label {
  min-width: 0;
  text-align: left;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.proc-card.fail,
.proc-card.fail .proc-ic {
  color: var(--yb-danger);
}
.proc-toggle {
  flex-shrink: 0;
  opacity: 0.55;
  font-size: var(--yb-fs-xs);
}
.proc-detail {
  margin: 4px 0 0;
  padding: 8px 10px;
  background: var(--yb-code-bg);
  border-radius: var(--yb-radius-sm);
  font-family: var(--yb-mono);
  font-size: var(--yb-fs-xs);
  line-height: var(--yb-lh-base);
  color: var(--yb-text-dim);
  max-width: 100%;
  max-height: 220px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  text-align: left;
}
@keyframes pop {
  from { opacity: 0; transform: scale(0.97); }
  to { opacity: 1; transform: none; }
}
/* 空状态：团子 + accent 光晕 + 主副句 + 建议卡（精致引导） */
.empty-hint {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-lg);
}
/* 团子背后的氛围光晕：radial accent 淡出，AI 感 */
.eh-glow {
  width: 132px;
  height: 132px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: radial-gradient(75% 75% at 50% 35%, var(--yb-accent-soft), rgba(var(--yb-c-sky-rgb), 0) 72%);
  margin-bottom: 8px;
}
.eh-title {
  margin: 0;
  font-size: 22px;
  font-weight: var(--yb-fw-bold);
  letter-spacing: -0.01em;
  color: var(--yb-text-strong);
}
.eh-sub {
  margin: 0 0 8px;
  font-size: var(--yb-fs-md);
  color: var(--yb-text-dim);
}
.chips {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: var(--yb-space-2);
}
.chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  border: 1px solid var(--yb-surface-border);
  border-radius: var(--yb-radius-pill);
  background: var(--yb-surface-solid);
  box-shadow: var(--yb-shadow-1);
  color: var(--yb-accent-deep);
  font-size: var(--yb-fs-lg);
  font-family: inherit;
  cursor: pointer;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.chip svg {
  color: var(--yb-accent);
}
.chip:hover {
  background: var(--yb-accent-soft);
  border-color: var(--yb-accent);
  color: var(--yb-accent-deep);
  transform: translateY(-1px);
  box-shadow: var(--yb-shadow-2);
}

/* ---- 消息行：hover 显示操作（复制/重写/反馈/编辑） ---- */
.msg-row {
  position: relative;
  max-width: 100%;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
}
.user-msg {
  align-self: flex-end;
  align-items: flex-end;
}
.msg-actions {
  position: absolute;
  top: -18px;
  right: 0;
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 2px;
  border-radius: var(--yb-radius-sm);
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid var(--yb-surface-border);
  box-shadow: var(--yb-shadow-1);
  opacity: 0;
  pointer-events: none;
  transform: translateY(2px);
  transition: opacity var(--yb-dur-fast) var(--yb-ease-out), transform var(--yb-dur-fast) var(--yb-ease-out);
  z-index: 6;
}
.msg-row:hover .msg-actions,
.msg-row:focus-within .msg-actions {
  opacity: 1;
  pointer-events: auto;
  transform: none;
}
.msg-actions button {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 7px;
  border: none;
  border-radius: var(--yb-radius-xs);
  background: transparent;
  color: var(--yb-text-dim);
  font-family: inherit;
  font-size: var(--yb-fs-xs);
  cursor: pointer;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.msg-actions button:hover {
  background: var(--yb-accent-soft);
  color: var(--yb-accent-deep);
}

/* ---- 溯源：AI 回复"参考了 ▾" ---- */
.refs {
  margin-top: 4px;
  margin-left: 30px;
  max-width: min(70%, 640px);
}
.refs-toggle {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 8px;
  border: none;
  border-radius: var(--yb-radius-pill);
  background: transparent;
  color: var(--yb-text-faint);
  font-family: inherit;
  font-size: var(--yb-fs-xs);
  cursor: pointer;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.refs-toggle:hover {
  color: var(--yb-accent-deep);
  background: var(--yb-accent-soft);
}
.refs-toggle i {
  width: 5px;
  height: 5px;
  border-right: 1.5px solid currentColor;
  border-bottom: 1.5px solid currentColor;
  transform: rotate(45deg);
  transition: transform var(--yb-dur-fast) var(--yb-ease-out);
}
.refs-toggle i.open {
  transform: rotate(225deg);
}
.refs-list {
  margin: 4px 0 0;
  padding: 6px 8px;
  list-style: none;
  border: 1px solid var(--yb-surface-border);
  border-radius: var(--yb-radius-sm);
  background: rgba(255, 255, 255, 0.6);
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.refs-list li {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  font-size: var(--yb-fs-xs);
  color: var(--yb-text-dim);
  min-width: 0;
}
.refs-list li > svg {
  flex-shrink: 0;
  margin-top: 2px;
  color: var(--yb-intent-ok);
}
.refs-list li.fail > svg {
  color: var(--yb-danger);
}
.refs-label {
  flex-shrink: 0;
  color: var(--yb-text);
  font-weight: var(--yb-fw-medium);
}
.refs-detail {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--yb-text-faint);
}
.refs-fade-enter-active,
.refs-fade-leave-active {
  transition: opacity var(--yb-dur-fast) var(--yb-ease-out);
}
.refs-fade-enter-from,
.refs-fade-leave-to {
  opacity: 0;
}

/* ---- 思考中状态文案 ---- */
.think-note {
  align-self: center;
  margin-left: 4px;
  padding: 3px 8px;
  border-radius: var(--yb-radius-pill);
  color: var(--yb-text-faint);
  font-size: var(--yb-fs-xs);
  white-space: nowrap;
}

/* ---- 跨日日期分隔 ---- */
.date-divider {
  align-self: center;
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 6px 0 2px;
  color: var(--yb-text-faint);
  font-size: var(--yb-fs-xs);
}
.date-divider::before,
.date-divider::after {
  content: "";
  width: 36px;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--yb-line));
}
.date-divider::after {
  background: linear-gradient(90deg, var(--yb-line), transparent);
}

/* ---- 跳到最新浮钮 ---- */
.jump-new {
  position: absolute;
  right: 26px;
  bottom: 96px;
  z-index: 5;
  padding: 4px 12px;
  border: 1px solid var(--yb-surface-border);
  border-radius: var(--yb-radius-pill);
  background: rgba(255, 255, 255, 0.94);
  box-shadow: var(--yb-shadow-2);
  color: var(--yb-accent-deep);
  font-family: inherit;
  font-size: var(--yb-fs-xs);
  cursor: pointer;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.jump-new:hover {
  transform: translateY(-1px);
  border-color: var(--yb-accent);
  box-shadow: var(--yb-shadow-2);
}

/* ---- 技能/场景快速呼出（输入区上方） ---- */
.skill-row {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}
.skill-hint {
  font-size: var(--yb-fs-xs);
  color: var(--yb-text-faint);
  margin-right: 2px;
}
.skill-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  border: 1px solid var(--yb-border-base);
  border-radius: var(--yb-radius-pill);
  background: var(--yb-surface-2);
  color: var(--yb-text-dim);
  font-family: inherit;
  font-size: var(--yb-fs-sm);
  cursor: pointer;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.skill-chip svg {
  color: var(--yb-accent);
}
.skill-chip:hover {
  background: var(--yb-accent-soft);
  border-color: var(--yb-accent);
  color: var(--yb-accent-deep);
  transform: translateY(-1px);
}
</style>
