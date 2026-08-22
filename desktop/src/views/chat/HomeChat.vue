<script setup lang="ts">
// 大窗主屏：预设换装配（缺省三栏）。与宠物窗同一条大脑会话（surface=pet）。
import { ref, computed, watch, nextTick, onMounted, onUnmounted, provide } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import InputBar from "../../components/common/InputBar.vue";
import PermissionsBanner from "../../components/pet/PermissionsBanner.vue";
import SetupWizard from "../../components/pet/SetupWizard.vue";
import AgentBrain from "../brain/AgentBrain.vue";
import HomeGlance from "../HomeGlance.vue";
import HomeLife from "../HomeLife.vue";
import HomePluginGlance from "../plugins/HomePluginGlance.vue";
import HomeContextPanel from "../HomeContextPanel.vue";
import HomeWidget from "../HomeWidget.vue";
import SessionList from "./SessionList.vue";
import HomeFrame from "../HomeFrame.vue";
import HomeDeskWork from "../HomeDeskWork.vue";
import HomeHostAsk from "../HomeHostAsk.vue";
import HomeFloatNotes from "../HomeFloatNotes.vue";
import { useLiveAssembly } from "../../lib/home/home-chrome.ts";
import { defaultPeek, faceOf } from "../../lib/home/home-assembly.ts";
import { viewOf } from "../../lib/home/home-assembly-ui.ts";
import { livePluginIds } from "../../composables/useAssembly";
import { syncPluginParts } from "../../lib/assembly/parts";
import { deskKind, deskPathOpen, isResumeDeskWork, shouldStampDeskPath, type DeskKind, type DeskWork } from "../../lib/home/home-desk-presence.ts";
import {
  HOME_CHAT_SESSION,
  type BubbleMsg,
  type ProcInfo,
  type HomeAvatarState as AvatarState,
} from "../../lib/home/home-chat-session.ts";
import {
  onBrainEvent,
  onBrainStatus,
  onBrainPermissions,
  onPanelClosed,
  runInput,
  voiceStart,
  interrupt,
  getWidgetsOnce,
  onWidgets,
  getSetupConfig,
  ensureActiveConversation,
  listPlugins,
  panelAction,
  type BrainPermissions,
  type BrainStatusMsg,
} from "../../lib/brain";
import { groupPages, groupThread, paperErrorNotice, paperStamps, runAnswer, runIsLive } from "../../lib/work-thread";
import { formatContextPrefix, type InputContext } from "../../lib/at-mention";
import { sessionStore } from "../../state/store";
import YbIcon from "../../components/common/YbIcon.vue";
import { useChatFlow } from "../../composables/useChatFlow";

type SkillChip = { key: string; label: string; icon: "clock" | "doc" | "sparkle" | "chat" | "plug"; draft: string };

/** 告警气泡：⚠️ 前缀改行首 alert 图标渲染（文案纯净，图标走 YbIcon） */
const emit = defineEmits<{
  state: [AvatarState];
  openPanel: [];
  reminder: [];
  closeWork: [];
  focusWork: [];
  workBody: [el: HTMLElement | null];
}>();
const props = defineProps<{
  draft?: string;
  workstation?: { panel?: string; title: string; plugin: string; objectTitle?: string } | null;
  lendEar?: boolean;
  workBusy?: boolean;
  workFocus?: boolean;
}>();

// 本地草稿：父级 draft 单向同步；右侧信息面板点动态也经此填入（强制重置触发 InputBar watch）
const draftRef = ref<string | undefined>(undefined);
const hostAskOpen = ref(false);
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
function onIdentityChat(d: string) {
  if (props.workstation && props.lendEar) {
    hostAskOpen.value = true;
    return;
  }
  onInfoChat(d);
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
const panelOpen = ref(false); // 面板协作会话进行中（关联气泡只插一次）——前移供气泡域 deps
const chatFlow = useChatFlow({
  getSessionId: () => currentSessionId.value,
  sessionRefUpdate: (p) => sessionRef.value?.updateCurrent(p),
  emitReminder: () => emit("reminder"),
  flashValence,
  panelOpen,
  setDraft: (t) => (draftRef.value = t),
});
const {
  state, bubbles, streamingIdx, editTarget, bubblesRef, showJump, runRefs,
  onEvent, restoreBubbles, pushBubble, pushWarn,
  scrollBubbles, onBubblesScroll,
  onEditMessage, copyText, onFeedback, regenerate,
  procOk, procErrSuffix, procText, paperShowProc,
} = chatFlow;

/** 恢复目标会话气泡：从 Rust 权威重拉（内存缓存可能被在途 run / 别的窗口写过） */
async function restoreConversation(id: string) {
  const restored = await restoreBubbles(id);
  // 气泡重建 → 依赖下标的瞬态由 restoreBubbles 作废（procIdx/runRefs）
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

const workKind = computed(() =>
  props.workstation
    ? deskKind(props.workstation.plugin, props.lendEar ? "handoff" : undefined)
    : "host",
);
const livePathLine = computed(() =>
  props.workstation ? deskPathOpen(workKind.value, props.workstation) : null,
);
let deskSurface: { kind: DeskKind; work: DeskWork } | null = null;
let lastFootprint: DeskWork | null = null;
let lastFootprintIndex = -1;

/** 桌面工作路径戳记：落气泡 + 持久化（气泡域 pushBubble 复用） */
function stampDeskOpen(kind: DeskKind, work: DeskWork) {
  const line = deskPathOpen(kind, work);
  const b: BubbleMsg = { role: "ai", text: line, panelLink: true, ts: Date.now() };
  pushBubble(b);
}

function closeDeskSurface() {
  deskSurface = null;
}

watch(
  () => [props.workstation, props.lendEar] as const,
  ([next, handoff]) => {
    const kind = next ? deskKind(next.plugin, handoff ? "handoff" : undefined) : null;
    if (deskSurface && (!next || !kind || deskSurface.kind !== kind || !isResumeDeskWork(deskSurface.work, next))) {
      closeDeskSurface();
    }
    if (!next) {
      hostAskOpen.value = false;
      return;
    }
    if (!kind) return;
    const since = lastFootprintIndex >= 0 ? bubbles.value.slice(lastFootprintIndex + 1) : bubbles.value;
    if (!shouldStampDeskPath(deskSurface?.work ?? null, lastFootprint, next, since)) {
      deskSurface = { kind, work: next };
      return;
    }
    stampDeskOpen(kind, next);
    lastFootprint = next;
    lastFootprintIndex = bubbles.value.length - 1;
    deskSurface = { kind, work: next };
  },
);
const brainDown = ref(false); // 大脑掉线/重启中（守护在恢复）
const perms = ref<BrainPermissions | null>(null); // macOS 权限状态（null=未收到）

// ---- 首启设置向导（缺 LLM key 时 Rust 发 setup-config-needed，大脑未启动；逻辑同源宠物窗）----
const setupNeeded = ref(false);
const setupCfg = ref({ model: "glm-4.6", baseUrl: "", voice: "zh-CN-XiaoxiaoNeural" });
async function onSetupNeeded() {
  setupNeeded.value = true;
  try {
    const cfg = await getSetupConfig();
    setupCfg.value = { model: cfg.model, baseUrl: cfg.base_url, voice: cfg.voice };
  } catch { /* 用默认值 */ }
}
function onSetupSaved() {
  setupNeeded.value = false;
  const b: BubbleMsg = { role: "sys", text: "配置已保存，大脑启动中…", ts: Date.now() };
  pushBubble(b);
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

let unlisten: (() => void) | null = null;
let unlistenStatus: (() => void) | null = null;
let unlistenPerms: (() => void) | null = null;
let unlistenPanelClosed: (() => void) | null = null;
let unlistenSetup: (() => void) | null = null;
let unlistenSetupErr: (() => void) | null = null;
let unlistenSetupCfg: (() => void) | null = null;
let unlistenUpdated: (() => void) | null = null;
let unlistenWidgets: (() => void) | null = null;

const assembly = useLiveAssembly();
const chatFace = computed(() => faceOf(assembly.value, "chat", "thread"));
const chatView = computed(() => viewOf("chat", chatFace.value));
const sessionFace = computed(() => faceOf(assembly.value, "sessions", "list"));
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
watch(
  () => props.workFocus,
  (focused) => {
    if (focused) {
      leftOpen.value = false;
      peekOpen.value = false;
    }
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
      pushBubble(b);
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
    const meta = await ensureActiveConversation().catch(() => null);
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
  pushBubble(userBubble);
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

function onHostAsk(text: string) {
  void submit(text);
}

const hostAskNotes = computed(() =>
  bubbles.value.slice(-5).map((bubble) => ({ role: bubble.role, text: bubble.text })),
);

function onHostAskEsc(e: KeyboardEvent) {
  if (e.key !== "Escape" || !hostAskOpen.value) return;
  e.preventDefault();
  e.stopImmediatePropagation();
  hostAskOpen.value = false;
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

/** /命令 local 动作：截图/打开插件/新建会话/帮助（InputBar 上抛） */
function onSlashLocal(id: string) {
  if (id === "snip") {
    void invoke("start_snip").catch(() => pushWarn("截图启动失败"));
  } else if (id === "plugins") {
    emit("openPanel"); // 打开工作台面板（面板协作视图）
  } else if (id === "new-conversation") {
    void newConversation();
  } else if (id === "help") {
    void submit("介绍一下你的能力：能调用哪些插件、支持哪些斜杠命令、如何高效使用。");
  }
}

/** 新建会话：创建即活跃，重置本窗气泡/状态（SessionList 列表由 refresh + sync 同步） */
async function newConversation() {
  try {
    const meta = await sessionStore.conversation.createConversation("新对话");
    currentSessionId.value = meta.id;
    await sessionStore.conversation.refreshConversations().catch(() => {});
    sessionRef.value?.sync();
    onSessionNew();
  } catch {
    pushWarn("新建会话失败");
  }
}

/** /命令 插件动作：api.toml command=true 的直调方法（如 toolbox.json_format） */
function onSlashPlugin(p: { pluginId: string; method: string }) {
  void panelAction(p.method, {}).catch(() => pushWarn("插件命令执行失败"));
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
  livePathLine,
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
  window.addEventListener("keydown", onHostAskEsc, true);
  // SessionStore 恢复编排：hydrate 三域后按活跃会话恢复气泡（与小窗镜面共用同一条会话）
  await sessionStore.restore().catch(() => {});
  let activeId = sessionStore.conversation.getActiveConversationId();
  if (!activeId) {
    // 首启无会话（或小窗已建会话但本域未同步）：走 Rust 确保并刷新列表
    const meta = await ensureActiveConversation().catch(() => null);
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
  try { plugins.value = await listPlugins().catch(() => []); } catch { plugins.value = []; }
  unlisten = await onBrainEvent(onEvent);
  unlistenStatus = await onBrainStatus(onStatus);
  unlistenPerms = await onBrainPermissions((p) => { perms.value = p; });
  unlistenPanelClosed = await onPanelClosed(() => {
    if (!panelOpen.value) return;
    panelOpen.value = false;
    closeDeskSurface();
  });
  // 首启引导（生产打包首跑：装 Python 环境/下模型，大脑还没起来，走 Tauri 事件直推）
  unlistenSetup = await listen<{ stage: string; detail: string }>("setup-progress", (e) => {
    const b: BubbleMsg = { role: "sys", text: e.payload.detail, ts: Date.now() };
    pushBubble(b);
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
    void restoreBubbles(conversationId);
  });
  // 小窗已改为固定会话（不跟随大窗，也不再广播切会话）→ 大窗无需订阅 active-conversation-changed
  // 主动拉一次配置：setup-config-needed 可能先于挂载发出而丢——靠拉取兜底
  try {
    const cfg = await getSetupConfig();
    if (!cfg.has_key) void onSetupNeeded();
  } catch { /* 忽略，事件路径仍兜底 */ }
  try {
    const result = await getWidgetsOnce().catch(() => ({ widgets: [] as { panel: string }[] }));
    syncPluginParts(result.widgets ?? []);
    unlistenWidgets = await onWidgets((payload) => syncPluginParts(payload?.widgets ?? []));
  } catch { /* sidecar unavailable */ }
  emit("state", state.value); // 父级侧边栏团子拿初始态
});
onUnmounted(() => {
  window.removeEventListener("keydown", onHostAskEsc, true);
  unlisten?.();
  unlistenStatus?.();
  unlistenPerms?.();
  unlistenPanelClosed?.();
  unlistenSetup?.();
  unlistenSetupErr?.();
  unlistenSetupCfg?.();
  unlistenUpdated?.();
  unlistenWidgets?.();
  if (valenceTimer !== null) clearTimeout(valenceTimer);
  if (thinkNoteTimer !== null) clearInterval(thinkNoteTimer);
});
</script>

<template>
  <div class="chat-page" :class="{ 'lend-ear': props.lendEar, 'at-work': Boolean(props.workstation) }">
    <SetupWizard v-if="setupNeeded" :model="setupCfg.model" :base-url="setupCfg.baseUrl" :voice="setupCfg.voice" @saved="onSetupSaved" />

    <HomeFrame
      v-else
      :thinking="state === 'think'"
      :state="state"
      v-model:peek="peekOpen"
      v-model:left="leftOpen"
    >
      <template #identity>
        <AgentBrain :state="state" only="identity" @chat="onIdentityChat" />
      </template>
      <template #mind>
        <AgentBrain :state="state" only="mind" @chat="onInfoChat" />
      </template>
      <template #today>
        <AgentBrain :state="state" only="today" @chat="onInfoChat" />
      </template>
      <template #need>
        <HomeGlance only="need" @chat="onInfoChat" />
      </template>
      <template #tasks>
        <HomeGlance only="tasks" @chat="onInfoChat" />
      </template>
      <template #remind>
        <HomeGlance only="remind" @chat="onInfoChat" />
      </template>
      <template #spark>
        <HomeLife only="spark" @chat="onInfoChat" />
      </template>
      <template #glimpse>
        <HomeLife only="glimpse" @chat="onInfoChat" />
      </template>
      <template #catch>
        <HomeLife only="catch" @chat="onInfoChat" />
      </template>
      <template #scratch>
        <HomeLife only="scratch" @chat="onInfoChat" />
      </template>
      <template v-for="id in livePluginIds" :key="id" #[id]>
        <HomePluginGlance
          :panel="id"
          :live-panel="props.workstation?.panel"
          :live-kind="workKind"
          @fold="emit('closeWork')"
        />
      </template>
      <template #sessions>
        <HomeWidget id="sessions" :fill="sessionFace === 'list'">
          <SessionList
            ref="sessionRef"
            @select="onSessionSelect"
            @active="onSessionActive"
            @new-chat="onSessionNew"
          />
        </HomeWidget>
      </template>

      <template #chat>
        <HomeDeskWork
          v-if="props.workstation"
          :plugin="props.workstation.plugin"
          :title="props.workstation.title"
          :object-title="props.workstation.objectTitle"
          :busy="props.workBusy"
          :focused="props.workFocus"
          :lend-ear="props.lendEar"
          :kind="workKind"
          @close="emit('closeWork')"
          @focus="emit('focusWork')"
          @ask="hostAskOpen = true"
          @body="emit('workBody', $event)"
        />
        <template v-else>
          <PermissionsBanner v-if="missingPerms && perms" :perms="perms" />
          <component :is="chatView" />
        </template>
        <HomeFloatNotes />
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
        <div v-if="!props.lendEar" class="input-slot">
          <div v-if="chatFace === 'thread'" class="skill-row">
            <span class="skill-hint">呼出技能</span>
            <button v-for="c in skillChips" :key="c.key" class="skill-chip" :title="c.draft" @click="onSkillChip(c)">
              <YbIcon :name="c.icon" :size="11" />{{ c.label }}
            </button>
          </div>
          <InputBar :busy="busy" :listening="state === 'listen'" :draft="draftRef" @submit="submit" @mic="onMic" @interrupt="onInterrupt" @slash-local="onSlashLocal" @slash-plugin="onSlashPlugin" />
        </div>
      </template>
    </HomeFrame>
    <div v-if="hostAskOpen && props.workstation" class="host-ask-slot">
      <HomeHostAsk
        :busy="busy"
        :listening="state === 'listen'"
        :notes="hostAskNotes"
        @submit="onHostAsk"
        @close="hostAskOpen = false"
      />
    </div>
  </div>
</template>

<style scoped>
.chat-page {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  position: relative;
}
.chat-page.lend-ear :deep(.host.kind-input) {
  display: none;
}
.host-ask-slot {
  position: absolute;
  z-index: 8;
  left: 16px;
  bottom: 16px;
  max-width: calc(100% - 32px);
}
.input-slot {
  box-sizing: border-box;
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  padding: 0;
}
.skill-row {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  margin-bottom: 6px;
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

