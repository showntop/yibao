<script setup lang="ts">
// 大窗主屏：预设换装配（缺省三栏）。与宠物窗同一条大脑会话（surface=pet）。
import { ref, computed, watch, nextTick, onMounted, onUnmounted, provide } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import InputBar from "./InputBar.vue";
import PermissionsBanner from "./PermissionsBanner.vue";
import SetupWizard from "./SetupWizard.vue";
import BrainSession from "./BrainSession.vue";
import HomeContextPanel from "./HomeContextPanel.vue";
import HomeWidget from "./HomeWidget.vue";
import SessionList from "./SessionList.vue";
import HomeFrame from "./HomeFrame.vue";
import { useLiveAssembly } from "../lib/home-chrome";
import { defaultPeek, faceOf } from "../lib/home-assembly";
import { viewOf } from "../lib/home-assembly-ui";
import {
  HOME_CHAT_SESSION,
  type BubbleMsg,
  type ProcInfo,
  type RunRef,
  type HomeAvatarState as AvatarState,
} from "../lib/home-chat-session";
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
  type RunMetrics,
} from "../lib/brain";
import { procLabel, procSkip, procResultSuffix, procDetail } from "../lib/proc";
import { groupPages, groupThread, paperErrorNotice, paperStamps, runAnswer, runIsLive } from "../lib/work-thread";
import { formatContextPrefix, type InputContext } from "../lib/at-mention";
import { sessionStore } from "../state/store";
import { newId } from "../state/domains/conversation";
import type { MessageInput } from "../state/domains/conversation";
import type { Message } from "../state/types";
import YbIcon from "./YbIcon.vue";

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
}>();
const props = defineProps<{
  draft?: string;
}>();

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
const sessionRef = ref<InstanceType<typeof SessionList> | null>(null);
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

// ---- 跳到最新 ----
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

/** 工具行插入前封口当前流式段，让后续 chunk 在过程卡之后另起一条 AI 消息。 */
function sealStreaming() {
  if (streamingIdx.value === null) return;
  const b = bubbles.value[streamingIdx.value];
  if (b?.id) syncBubble(b);
  streamingIdx.value = null;
}

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
        sealStreaming();
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
function paperShowProc(p: ProcInfo): boolean {
  return !p.done || !procOk(p);
}

const assembly = useLiveAssembly();
const chatFace = computed(() => faceOf(assembly.value, "chat", "thread"));
const chatView = computed(() => viewOf("chat", chatFace.value));
const leftOpen = ref(true);

/** 一次工作合成一条线索：正文与工具按时序穿插，页脚只出现在整轮收束。 */
const thread = computed(() => groupThread(bubbles.value, showDateDivider));
function threadKey(item: ReturnType<typeof groupThread>[number]): string {
  return item.type === "run" ? `run-${item.start}` : `${item.type}-${item.index}`;
}

/** 纸上只摊当前页：一句用户话 + 一轮工作。默认最后一页。 */
const pages = computed(() => groupPages(bubbles.value));
const pageIndex = ref(0);
const page = computed(() => pages.value[pageIndex.value] ?? null);
const paperEmpty = computed(() => page.value == null);
const paperDuty = computed(() => page.value != null && page.value.userIndex === null);
const paperTitle = computed(() => {
  const i = page.value?.userIndex;
  return i == null ? "" : (bubbles.value[i]?.text ?? "");
});
const paperLabel = computed(() => {
  const n = pages.value.length;
  return n ? `第 ${pageIndex.value + 1} / ${n} 页` : "";
});
const stampLabels = computed(() => {
  const p = page.value;
  if (!p) return [];
  return paperStamps(
    p.runIndices
      .map((i) => bubbles.value[i])
      .filter((b): b is BubbleMsg & { proc: ProcInfo } => Boolean(b?.proc))
      .map((b) => b.proc.label),
  );
});
const peekOpen = ref(defaultPeek(assembly.value));
watch(
  () => defaultPeek(assembly.value),
  (next, prev) => {
    if (prev === undefined || next !== prev) peekOpen.value = next;
  },
  { immediate: true },
);
watch(
  () => pages.value.length,
  (n, prev) => {
    pageIndex.value = Math.max(0, n - 1);
    if (chatFace.value === "paper" && n > (prev ?? 0) && n > 0) peekOpen.value = true;
  },
);
function flipPage(delta: number) {
  const n = pages.value.length;
  if (!n) return;
  pageIndex.value = Math.min(n - 1, Math.max(0, pageIndex.value + delta));
}

function runBusy(indices: number[]): boolean {
  return runIsLive(bubbles.value, indices, streamingIdx.value);
}
function runHalted(indices: number[]): boolean {
  return indices.some((i) => bubbles.value[i].halted);
}
function runShowFooter(indices: number[]): boolean {
  return !runBusy(indices) || runHalted(indices);
}
function pageNotice(text: string, icon?: string) {
  if (icon === "alert") return paperErrorNotice(text) ?? { summary: text.trim() || "出错了", detail: text };
  return paperErrorNotice(text);
}
function noticeFor(b: BubbleMsg) {
  return pageNotice(b.text, b.icon);
}
function runMetricsOf(indices: number[]) {
  for (let k = indices.length - 1; k >= 0; k -= 1) {
    const metrics = bubbles.value[indices[k]].metrics;
    if (metrics) return metrics;
  }
  return undefined;
}
function runRefsOf(indices: number[]): BubbleMsg | undefined {
  for (let k = indices.length - 1; k >= 0; k -= 1) {
    const b = bubbles.value[indices[k]];
    if (b.refs?.length) return b;
  }
  return undefined;
}
function toggleRunRefs(indices: number[]) {
  const b = runRefsOf(indices);
  if (b) b.refsOpen = !b.refsOpen;
}
function copyRun(indices: number[]) {
  copyText(runAnswer(bubbles.value, indices));
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
      sessionRef.value?.sync();
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
  // 定向打断（并发对话 spec §E）：只停当前会话槽，不掐小窗/面板的在跑 run
  void interrupt(currentSessionId.value || undefined).catch((err) => {
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

provide(HOME_CHAT_SESSION, {
  bubbles,
  thread,
  state,
  greeting,
  suggestChips: SUGGEST_CHIPS,
  showTyping,
  streamingIdx,
  thinkNote,
  showJump,
  bubblesRef,
  pages,
  pageIndex,
  page,
  paperEmpty,
  paperDuty,
  paperTitle,
  paperLabel,
  stampLabels,
  peekOpen,
  threadKey,
  submit,
  fmtDay,
  openPanel: () => emit("openPanel"),
  procOk,
  procErrSuffix,
  procText,
  paperShowProc,
  runRefsOf,
  toggleRunRefs,
  runShowFooter,
  runMetricsOf,
  runHalted,
  copyRun,
  copyText,
  onFeedback,
  regenerate,
  onEditMessage,
  onBubblesScroll,
  scrollBubbles,
  flipPage,
  noticeFor,
});


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
  <div class="chat-page">
    <SetupWizard v-if="setupNeeded" :model="setupCfg.model" :base-url="setupCfg.baseUrl" :voice="setupCfg.voice" @saved="onSetupSaved" />

    <HomeFrame
      v-else
      :thinking="state === 'think'"
      :state="state"
      v-model:peek="peekOpen"
      v-model:left="leftOpen"
    >
      <BrainSession
        :state="state"
        @chat="onInfoChat"
        @toggle="leftOpen = !leftOpen"
      />
      <template #sessions>
        <HomeWidget id="sessions" fill>
          <SessionList
            ref="sessionRef"
            @select="onSessionSelect"
            @active="onSessionActive"
            @new-chat="onSessionNew"
          />
        </HomeWidget>
      </template>

      <template #chat>
        <PermissionsBanner v-if="missingPerms && perms" :perms="perms" />
        <component :is="chatView" />
      </template>

      <template #now>
        <HomeContextPanel
          :session-id="currentSessionId"
          :session-title="currentSessionTitle"
          :session-state="state"
          :has-conversation="Boolean(bubbles.length)"
          :processes="sessionProcesses"
          @chat="onInfoChat"
        />
      </template>

      <template #composer>
        <div class="input-slot">
          <div class="skill-row">
            <span class="skill-hint">呼出技能</span>
            <button v-for="c in skillChips" :key="c.key" class="skill-chip" :title="c.draft" @click="onSkillChip(c)">
              <YbIcon :name="c.icon" :size="11" />{{ c.label }}
            </button>
          </div>
          <InputBar :busy="busy" :listening="state === 'listen'" :draft="draftRef" @submit="submit" @mic="onMic" @interrupt="onInterrupt" />
        </div>
      </template>
    </HomeFrame>
  </div>
</template>

<style scoped>
.chat-page {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.input-slot {
  flex-shrink: 0;
  padding: 0 0 2px;
}
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

