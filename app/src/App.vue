<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow, PhysicalPosition } from "@tauri-apps/api/window";
import { WebviewWindow } from "@tauri-apps/api/webviewWindow";
import Avatar from "./components/Avatar.vue";
import InputBar from "./components/InputBar.vue";
import QuickPanel from "./components/QuickPanel.vue";
import Bubble from "./components/Bubble.vue";
import SpeechBubble from "./components/SpeechBubble.vue";
import PermissionsBanner from "./components/PermissionsBanner.vue";
import SetupWizard from "./components/SetupWizard.vue";
import SurfaceLine from "./components/SurfaceLine.vue";
import {
  onBrainEvent,
  onBrainStatus,
  onBrainPermissions,
  onRunDone,
  onPanelClosed,
  onPendingConfirms,
  onSettings,
  getSettingsOnce,
  openHomeWindow,
  emitRecapOpen,
  runInput,
  invokeContext,
  sendConfirmBatch,
  voiceStart,
  interrupt,
  panelAction,
  onInvokeAction,
  onSnipCaptured,
  visionQuery,
  type BrainEvent,
  type BrainStatusMsg,
  type BrainPermissions,
  type PendingConfirm,
  type SettingsValues,
  canRememberSkill,
  rememberLabelForSkill,
} from "./lib/brain";
import { formatContextPrefix, type InputContext } from "./lib/at-mention";
import {
  openPanel,
  setInteractiveFull,
  setMainSize,
  setHotRects,
} from "./lib/window";
import { SUGGESTIONS } from "./lib/suggestions";
import { matchExplicitOpen } from "./lib/explicit-intent";
import { decideSurface, type Attention, type Presentation } from "./lib/surface-policy";
import { deactivateAll, petFormOf, surfaceCount, type SurfaceAttr } from "./lib/pet-surface";
import { procLabel, procSkip, procResultSuffix } from "./lib/proc";
import { sessionStore, clearLegacySessionKeys } from "./state/store";
import YbIcon from "./components/YbIcon.vue";

type AvatarState = "idle" | "listen" | "think" | "work" | "say" | "success" | "error" | "notify" | "drowsy" | "stretch";
// pstate：过程行状态（图标随态渲染，文案不再拼 emoji）；halted：被打断；icon：行首语义图标
type BubbleMsg = {
  role: "user" | "ai" | "sys";
  text: string;
  pstate?: "run" | "ok" | "fail";
  halted?: boolean;
  icon?: "clock" | "alert" | "doc";
  /** 晨间反刍 deep-link：morning_recap 提醒气泡携带 day 字符串时，点击切到 home 回顾视图 */
  recap?: string;
  /** 表面属性（Phase 1.5）：与 pstate 正交。有则该行渲染为面板入口 */
  surface?: SurfaceAttr;
};

/** Rust SessionDb 消息的序列化形状（camelCase）——小窗恢复拉取用 */
type PetMessage = {
  id: string;
  conversationId: string;
  seq: number;
  role: string;
  payload: { text: string; halted?: boolean; icon?: string };
  ts: number;
  ephemeral?: boolean;
};

const state = ref<AvatarState>("idle");
// 环境态原料：attentionNeeded=有事找你（提醒/收起时的播报，展开即消）；drowsy=发呆（连续纯待命超时）
const attentionNeeded = ref(false);
const drowsy = ref(false);
/** 展示态：运行态优先；idle 时按 有事 > 发呆 > 普通 推导（发呆只在收起态露脸） */
const petState = computed<AvatarState>(() => {
  if (state.value !== "idle") return state.value;
  if (attentionNeeded.value) return "notify";
  if (!expanded.value && drowsy.value) return "drowsy";
  return "idle";
});
const bubbles = ref<BubbleMsg[]>([]);
const streamingIdx = ref<number | null>(null); // 正在接收 chunk 的 bubble 下标
/** 小窗固定会话 id（方案 A）：永远用同一个会话，不镜像大窗活跃会话。
 *  run 带它使消息归属、重启可恢复；固定性从架构上消灭串台（大窗切会话不影响本窗）。 */
const petConvId = ref("");
const pendingConfirms = ref<PendingConfirm[]>([]);
const pending = computed(() => pendingConfirms.value[0] ?? null);
const pendingCanRemember = computed(() => canRememberSkill(pending.value?.skill ?? ""));
const rememberPending = ref(false);
const approvalGuard = ref<null | "allowed" | "denied">(null);
let approvalGuardTimer: ReturnType<typeof setTimeout> | null = null;
const brainDown = ref(false); // 大脑掉线/重启中（守护在恢复）
const perms = ref<BrainPermissions | null>(null); // macOS 权限状态（null=未收到）
// 感知观察中叠加点（Avatar observing prop）：总开关 + 任一采集源开启即视为观察中
const observing = ref(false);
function syncObserving(s: SettingsValues | null) {
  observing.value = !!(
    s?.["perception.master"] &&
    (s?.["perception.app"] || s?.["perception.activity"] || s?.["perception.screen"])
  );
}
const expanded = ref(false);
/** 快捷面板（单窗三态 quick 内容层）：hover 团子显示 3 圆 + 输入条，同窗渲染零 resize */
const quick = ref(false);
/** 收起态回复气泡：长按语音/快捷输入条的回复默认只走气泡，不展开对话窗；
 *  点击气泡展开完整对话（气泡内容迁入气泡流）。气泡显示期间快捷面板不弹。 */
const speech = ref<string | null>(null);
const speechStreaming = ref(false);
const speechVisible = ref(false);
let speechTimer: ReturnType<typeof setTimeout> | null = null;
/** 团子窗口内 top（CSS 像素）：正常 100（脚下输入条，再下是插件）；
 *  窗口贴近屏幕顶时动态上移（macOS 不允许窗口出屏），让团子贴菜单栏下缘。
 *  团子屏幕 y = 窗口y + petY，min(100, 窗口y+40) 保证接近顶部时连续上贴。 */
const petY = ref(100);
let scaleCached = 1; // Retina 缩放（窗口创建后不变，onMoved 计算用）
let unlistenMoved: (() => void) | null = null;
const panelOpen = ref(false); // 面板浮窗当前打开状态
// 过程展示：action.id → 过程行（sys 淡色小字）在 bubbles 里的下标，结果回来原地更新
const procIdx = new Map<string, number>();
// explicit run 标记：插件视图点击 / 窄规则命中两个来源共用；run_done、final_reply、interrupted 或发起失败时清理。
// 刻意不设过期时间：文本路径要等两轮 LLM 往返，任何墙钟窗口都会在慢模型上静默失效。
// 注意 run_done 是不带 surface 的全局广播（任何窗口的任何 run 结束都会触发），所以无关 run
// 可能提前清掉标记 —— 方向是「该开的窗没开」，退化成一条可点行，绝不会反向违反「模型不得自动开窗」。
let requestedPlugin = "";
function markExplicit(pluginId: string): void {
  requestedPlugin = pluginId;
}
function clearExplicit(): void {
  requestedPlugin = "";
}

// action id → 过程行下标：panel 事件按 origin 找回该行补表面属性。
// 必须与 procIdx 分开：procIdx 在 action_result 就删了，
// 而 panel 事件在 action_result 之后才到（loop.py:331 → :337）。
const surfaceAnchor = new Map<string, number>();

/** 告警气泡：⚠️ 前缀改行首 alert 图标渲染（文案纯净，图标走 YbIcon） */
function pushWarn(text: string) {
  bubbles.value.push({ role: "ai", text, icon: "alert" });
}

// ---- 首启设置向导（缺 LLM key 时 Rust 发 setup-config-needed，大脑未启动）----
const setupNeeded = ref(false);
const setupCfg = ref({ model: "glm-4.6", baseUrl: "", voice: "zh-CN-XiaoxiaoNeural" });
async function onSetupNeeded() {
  setupNeeded.value = true;
  if (!expanded.value) void expand();
  try {
    setupCfg.value = await invoke("get_setup_config");
  } catch { /* 用默认值 */ }
}
function onSetupSaved() {
  setupNeeded.value = false;
  bubbles.value.push({ role: "sys", text: "配置已保存，大脑启动中…" });
}

/** 常驻轻提示（reminder 等「有事找你」）：展开对话窗 + 落一条提醒气泡。 */
function openBubbleSticky(text: string) {
  if (expanded.value) return;
  bubbles.value.push({ role: "ai", text, icon: "alert" });
  void expand();
}
let unlisten: (() => void) | null = null;
let unlistenRunDone: (() => void) | null = null;
let unlistenStatus: (() => void) | null = null;
let unlistenPerms: (() => void) | null = null;
let unlistenPanelClosed: (() => void) | null = null;
let unlistenSetup: (() => void) | null = null;
let unlistenSetupErr: (() => void) | null = null;
let unlistenSetupCfg: (() => void) | null = null;
let unlistenInvoke: (() => void) | null = null;
let unlistenInvokeSel: (() => void) | null = null;
let unlistenInvokeAction: (() => void) | null = null;
let unlistenSnip: (() => void) | null = null;
let unlistenApprovals: (() => void) | null = null;
let unlistenSettings: (() => void) | null = null;
let unlistenCursorEnter: (() => void) | null = null;
let unlistenCursorLeave: (() => void) | null = null;
let unlistenConvUpdated: (() => void) | null = null;
let rectTimer: ReturnType<typeof setInterval> | null = null;

const statusText = computed(
  () => ({
    idle: "待命中", listen: "聆听中", think: "思考中…", work: "操作中…", say: "说话中…",
    success: "完成", error: "出错了", notify: "有事找你", drowsy: "发呆中", stretch: "伸展中",
  }[petState.value]),
);
// success/error 是短暂 valence（不可打断），不算 busy
const busy = computed(() =>
  state.value === "listen" || state.value === "think" ||
  state.value === "work" || state.value === "say",
);
const suggestions = SUGGESTIONS;
const missingPerms = computed(() => perms.value !== null && (!perms.value.ax || !perms.value.screen || !perms.value.input));
// 「正在输入」占位：run 受理（think）到首个 chunk 之间气泡流还是空的，用三点呼吸占位；
// 复用 state/streamingIdx 判断——首 chunk 建起 streaming 气泡即让位，终态（idle/error）自动消失
const showTyping = computed(() => state.value === "think" && streamingIdx.value === null);

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

// 对话区挂在 v-if="expanded" 上：收起即拆 DOM，滚动位置归零。
// 再展开时 bubbles 已在内存、length 不变，上面的 watch 不触发，会停在最顶。
// 收起前记下「是否贴底 / 滚动偏移」，展开后恢复；贴底或没有记录则滚到最新。
const STICK_BOTTOM_PX = 80;
let stickBottom = true;
let savedScrollTop = 0;
function captureBubbleScroll() {
  const el = bubblesRef.value;
  if (!el) return;
  stickBottom = el.scrollHeight - el.scrollTop - el.clientHeight < STICK_BOTTOM_PX;
  savedScrollTop = el.scrollTop;
}
function restoreBubbleScroll() {
  void nextTick(() => {
    requestAnimationFrame(() => {
      const el = bubblesRef.value;
      if (!el) return;
      el.scrollTop = stickBottom ? el.scrollHeight : savedScrollTop;
    });
  });
}

/** 单窗热区上报：idle 只报团子盒（pet），quick 追加面板元素（ui，.wb-zone）。
 *  Rust 据此放行鼠标穿透 + 驱动 enter/leave；窗口相对坐标，拖动自动跟随。 */
function syncHotRects() {
  void nextTick(() => {
    const rects: { x: number; y: number; w: number; h: number; kind: string }[] = [];
    document.querySelectorAll<HTMLElement>(".pet").forEach((n) => {
      const r = n.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) {
        rects.push({ x: r.left, y: r.top, w: r.width, h: r.height, kind: "pet" });
      }
    });
    // v-show 隐藏的元素仍有 rect，只在 quick 显示时上报面板热区
    if (quick.value) {
      document.querySelectorAll<HTMLElement>(".wb-zone").forEach((n) => {
        const r = n.getBoundingClientRect();
        if (r.width > 0 && r.height > 0) {
          rects.push({ x: r.left, y: r.top, w: r.width, h: r.height, kind: "ui" });
        }
      });
    }
    // 收起态回复气泡热区（点击展开；ui 只放行点击不驱动 enter）
    if (speechVisible.value) {
      document.querySelectorAll<HTMLElement>(".speech-zone").forEach((n) => {
        const r = n.getBoundingClientRect();
        if (r.width > 0 && r.height > 0) {
          rects.push({ x: r.left, y: r.top, w: r.width, h: r.height, kind: "ui" });
        }
      });
    }
    void setHotRects(rects.length ? rects : null).catch(() => {});
  });
}

/** 顶部边界自适应：macOS 不允许窗口顶部出屏（诊断确认负 y setPosition 被忽略，
 *  窗口顶最低停在菜单栏下缘）。故窗口贴近顶部时「团子锚点在窗口内上移」：
 *     f = clamp(窗口y - 24, 0, 100)   （窗口内正常位 = 100）
 *     团子屏幕 y = max(窗口y + f, 24)  →  团子窗口内 y = 屏幕y - 窗口y
 *  效果：窗口 y ∈ [0,24] 时团子恒贴菜单栏下缘（屏幕 y=24）；拖离顶部后线性过渡
 *  回窗口内 100。快捷态顺序：团子 → 输入条 → 插件。
 *  热区随 petY 变化实时上报。 */
function onWindowMoved(p: { x: number; y: number }) {
  const winY = p.y / (scaleCached || 1);
  const f = Math.max(0, Math.min(100, winY - 24));
  const target = Math.max(winY + f, 24) - winY;
  if (target !== petY.value) {
    petY.value = target;
    syncHotRects();
  }
}

let idlePos = { x: 0, y: 0 }; // 收起态窗口位置（物理像素），展开时记录、收起时还原
async function expand() {
  attentionNeeded.value = false; // 用户来看了 = 事已知，notify 态消
  expanded.value = true;
  // 收起态气泡内容迁入气泡流（点击气泡/点团子展开都带上）
  if (speech.value) {
    bubbles.value.push({ role: "ai", text: speech.value });
    stickBottom = true; // 收起态新回复迁入，打开应对准最新
  }
  speechVisible.value = false;
  speech.value = null;
  speechStreaming.value = false;
  if (speechTimer) { clearTimeout(speechTimer); speechTimer = null; }
  restoreBubbleScroll();
  // 先记录收起态位置（collapse 还原用），再交给 Rust 展开（定位 + clamp + 缩放一步完成）
  try {
    const p = await getCurrentWindow().outerPosition();
    idlePos = { x: p.x, y: p.y };
  } catch { /* 忽略 */ }
  await invoke("expand_chat").catch(() => {});
  restoreBubbleScroll(); // 窗口 320×300 → 360×520 后 clientHeight 变了，贴底要再对准一次
}

/** 显示收起态回复气泡（回复类事件用；与快捷面板互斥，区域重叠） */
function showSpeechBubble() {
  speechVisible.value = true;
  quick.value = false;
  syncHotRects();
}
/** 隐藏并清空气泡；timer 到期调用 */
function hideSpeechBubble() {
  speechVisible.value = false;
  speech.value = null;
  speechStreaming.value = false;
  syncHotRects();
}

/** 点击收起态气泡：展开完整对话窗（气泡内容由 expand() 迁入气泡流）。 */
function onSpeechExpand() {
  void expand();
}
async function collapse() {
  captureBubbleScroll(); // 必须在 v-if 拆掉 .bubbles 之前
  expanded.value = false;
  quick.value = false;
  void setMainSize(320, 300).catch(() => {});
  syncHotRects(); // 切回团子盒热区（v-if 渲染后 nextTick 生效），避免穿透失效
  // 还原收起态位置（团子回窗口内 (112,100) 锚点）
  await getCurrentWindow()
    .setPosition(new PhysicalPosition(idlePos.x, idlePos.y))
    .catch(() => {});
}

// ---- 发呆（drowsy）：连续 5 分钟纯待命则入睡相；任何运行态变化/碰团子即醒并重计时 ----
let drowsyTimer: ReturnType<typeof setTimeout> | null = null;
function armDrowsy() {
  if (drowsyTimer) clearTimeout(drowsyTimer);
  drowsyTimer = setTimeout(() => { drowsy.value = true; }, 5 * 60_000);
}
watch(
  state,
  (s) => {
    if (s === "idle") armDrowsy();
    else {
      if (drowsyTimer) { clearTimeout(drowsyTimer); drowsyTimer = null; }
      drowsy.value = false;
    }
  },
  { immediate: true },
);
function onPetHover() {
  // 只负责醒团子（发呆重置）。弹工作台交给 pet-cursor-enter（Rust 56×56 内缩热区），
  // 否则 88×88 的 pet-wrap 透明边也会触发 pointerenter，鼠标没到身体就弹出。
  if (state.value !== "idle") return;
  drowsy.value = false;
  armDrowsy();
}

/** 拖动开始/结束（Avatar 通知）：收起态拖团子期间窗口必须整窗可交互——贴顶区
 *  团子位置实时变化、热区同步有 IPC 窗口期，若不禁止穿透，Rust 会把光标判成
 *  「不在热区」而切穿透 → 丢失 pointer 事件 → 拖动中断。chat 态不启用（header 走系统拖拽）。 */
function onPetDragStart() {
  if (expanded.value) return;
  void setInteractiveFull(true).catch(() => {});
}
function onPetDragEnd() {
  if (expanded.value) return;
  void setInteractiveFull(false).catch(() => {});
}

/** 鼠标穿透→可交互切换 nudge：Rust 判定光标进入/离开团子热区时发信号，
 *  给 .pet 补挂 yb-hover class —— 穿透切换瞬间 WebKit 不自动重算 CSS :hover，
 *  不补这一下，首次移入团子必须点一下才触发上移动效（此后 mousemove 正常驱动）。 */
function setPetHover(on: boolean) {
  document.querySelectorAll<HTMLElement>(".pet").forEach((el) => {
    if (on) {
      el.classList.add("yb-hover");
      el.dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
    } else {
      el.classList.remove("yb-hover");
      el.dispatchEvent(new MouseEvent("mouseout", { bubbles: true }));
    }
  });
}

/** 快捷面板提交：收起面板 → 直接 submit，回复默认走气泡（不展开对话窗）。 */
function onQuickSubmit(text: string, contexts: InputContext[] = []) {
  quick.value = false;
  syncHotRects();
  void submit(text, contexts);
}

/** 快捷面板点插件/更多：收起面板 → 展开到插件页并启动；id 空 = 展开对话（待批准/更多）。 */
function onQuickLaunch(p: { id: string; name: string }) {
  quick.value = false;
  syncHotRects();
  if (p.id) {
    void expandTo("plugins");
    void launchPlugin(p);
  } else {
    void expandTo("chat");
  }
}

/** 快捷面板麦克风：收起面板 → 直接录音，回复默认走气泡。 */
function onQuickMic() {
  quick.value = false;
  syncHotRects();
  onMic();
}

/** 快捷面板打断：对话在生成中时直接打断，不展开。 */
function onQuickInterrupt() {
  onInterrupt();
}

// ---- 插件启动器（双击团子）----
type PetView = "chat" | "plugins";
interface PluginInfo { id: string; name: string }

/* 插件启动器：按 id 哈希到 5 色调色板（与 QuickPanel 一致，主题感知 CSS 变量）。
 * inline 复刻避免动 QuickPanel；后续若多处复用可抽 lib/icons.ts。 */
const ICON_PALETTE = [
  { bg: "var(--yb-icon-bg-0)", fg: "var(--yb-icon-fg-0)" },
  { bg: "var(--yb-icon-bg-1)", fg: "var(--yb-icon-fg-1)" },
  { bg: "var(--yb-icon-bg-2)", fg: "var(--yb-icon-fg-2)" },
  { bg: "var(--yb-icon-bg-3)", fg: "var(--yb-icon-fg-3)" },
  { bg: "var(--yb-icon-bg-4)", fg: "var(--yb-icon-fg-4)" },
] as const;
function djb2(s: string): number {
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}
function iconStyle(id: string) {
  const c = ICON_PALETTE[djb2(id) % ICON_PALETTE.length];
  return { background: c.bg, color: c.fg };
}
function initial(name: string): string {
  const ch = name.trim().charAt(0);
  return ch ? ch.toUpperCase() : "?";
}
const view = ref<PetView>("chat");
const allPlugins = ref<PluginInfo[]>([]);
const plugins = ref<PluginInfo[]>([]);
const pluginErr = ref("");
let clickTimer: ReturnType<typeof setTimeout> | null = null;

/** 单击=展开对话；双击=插件启动器（220ms 内第二次点击判双击，单击稍延迟是消歧代价）。
 *  聆听中单击团子 = 取消录音（收缩态唯一的取消入口），不进消歧计时。 */
function onPetClick() {
  if (state.value === "listen") {
    onInterrupt();
    return;
  }
  if (clickTimer !== null) {
    clearTimeout(clickTimer);
    clickTimer = null;
    void expandTo("plugins");
    return;
  }
  clickTimer = setTimeout(() => {
    clickTimer = null;
    void expandTo("chat");
  }, 220);
}

async function expandTo(v: PetView) {
  view.value = v;
  if (v === "plugins") void loadPlugins();
  if (!expanded.value) await expand();
}

async function loadPlugins() {
  pluginErr.value = "";
  try {
    // 上限 8 个：插件是精选的，不会多；超出说明该做设置页了
    allPlugins.value = await invoke<PluginInfo[]>("list_plugins");
    plugins.value = allPlugins.value.slice(0, 8);
  } catch (err) {
    allPlugins.value = [];
    plugins.value = [];
    pluginErr.value = String(err);
  }
}

/** 点插件 → 调它的 list 直调（约定的主面板入口）；panel 事件回来会自动 openPanel + 收起对话。 */
async function launchPlugin(p: PluginInfo) {
  pluginErr.value = "";
  markExplicit(p.id);
  try {
    await panelAction(`${p.id}.list`, {});
  } catch (err) {
    clearExplicit();
    pluginErr.value = "启动失败：" + String(err);
  }
}

/** 开面板浮窗 + 宠物窗收回球形态。
 *  行点击不必携带面板身份：可点行恒为最新那条，而面板窗内容渲染器本就
 *  跟着最新 panel 事件走，两者天然指向同一个面板。 */
function openPanelWindow(): void {
  panelOpen.value = true;
  void openPanel();
  if (expanded.value) void collapse();
}

/** header「大窗」钮 → 打开大窗（完整 APP 主界面，与小窗互斥，宠物窗保持纯粹）。 */
function openHome() {
  void openHomeWindow().catch(() => {});
}

let avatarClickTimer: ReturnType<typeof setTimeout> | null = null;
/** 单击团子收起；双击打开大窗。
 * 不能用原生 dblclick（Avatar 根元素 @pointerdown.prevent 阻止原生双击事件链），
 * 靠 450ms 判定窗口（接近系统双击间隔）——第二击取消定时器并放大窗。
 * 判定窗口太短（原 220ms）会让慢速双击的第一击先触发收起，窗口收起后第二击落空。 */
function onAvatarClick() {
  if (avatarClickTimer !== null) {
    clearTimeout(avatarClickTimer);
    avatarClickTimer = null;
    void openHome();
    return;
  }
  avatarClickTimer = setTimeout(() => {
    avatarClickTimer = null;
    void collapse();
  }, 450);
}

/** morning_recap 气泡点击 → 确保 home 窗可见 + 通知 HomeFeed 切到回顾 mode 并跳到当天。 */
function onRecapClick(day?: string) {
  if (!day) return;
  void openHomeWindow().catch(() => {});
  void emitRecapOpen(day);
}

// ---- 全局唤起（⌘⇧Y 反射键 / ⌘⇧U 划词唤起，Rust 抓完选中文字发事件）----
const inputBarRef = ref<{ focus: () => void } | null>(null);
const selectionCtx = ref<string | null>(null); // 划词上下文（chip 展示，发送时拼进给大脑的消息）
const ctxPreview = computed(() => {
  const t = (selectionCtx.value ?? "").replace(/\s+/g, " ").trim();
  return t.length > 42 ? t.slice(0, 42) + "…" : t;
});
const snipCtx = ref<{ width: number; height: number } | null>(null); // 截图即问：框选完成待提问

// 命名注意：brain.ts 的监听封装叫 onSnipCaptured，处理函数另名 onSnipReady 避免撞名
async function onSnipReady(r: { width: number; height: number }) {
  snipCtx.value = r;
  if (!expanded.value) await expand();
  void nextTick(() => inputBarRef.value?.focus());
}

async function onPetShow() {
  // Rust 侧已按 可见性×展开态 决策（显隐不经过前端）：pet-show 只需确保展开 + 输入就绪
  if (!expanded.value) await expand();
  void nextTick(() => inputBarRef.value?.focus());
  void invokeContext().catch(() => {}); // 截图唤起：抓屏描述暂存，下次 run 注入屏幕上下文（静默失败）
}

// 展开态同步给 Rust（全局热键在 Rust 侧决定 显示/展开/隐藏 的依据）
watch(expanded, (v) => {
  void invoke("set_pet_expanded", { expanded: v }).catch(() => {});
});

async function onPetInvokeSelection(text: string | null) {
  const t = text?.trim();
  // 上下文截断 4000 字：够一整页文档，又不至于一条消息烧穿上下文
  if (t) selectionCtx.value = t.length > 4000 ? t.slice(0, 4000) : t;
  if (t) return; // 有选中文字：动作条在光标旁待选，不展开宠物窗（静默优先；选动作才展开）
  await expand(); // 无选中文字：退化为旧唤起（展开 + 聚焦输入）
  void nextTick(() => inputBarRef.value?.focus());
}

// 唤起条动作：解释/翻译走现有 run（selectionCtx 自动拼入）；存素材 quiet 直调（不弹面板）
async function handleInvokeAction(action: string) {
  void invoke("hide_invoke_bar").catch(() => {}); // 兜底（条本身已自隐）
  const sel = selectionCtx.value;
  if (action === "explain" || action === "translate") {
    // 不展开：收起态气泡带会镜像流式（打字机），想细看点气泡即展开——看一眼就够的场景不打扰
    const q =
      action === "explain"
        ? "解释这段文字，讲清要点"
        : "把这段文字翻译成中文（如果它已经是中文，就翻译成英文）";
    if (sel) {
      await submit(q);
    } else {
      pushWarn("没有取到选中文字，请选中后再试");
    }
  } else if (action === "save") {
    if (!sel) {
      pushWarn("没有可存的选中文字");
      return;
    }
    await panelAction("zimeiti.invoke_mat_save", { text: sel });
    selectionCtx.value = null;
    flashValence("success"); // 400ms 成功闪现当回执（不展开、不弹面板，静默优先）
  }
}

function onEvent(e: BrainEvent) {
  // 会话分流：面板场景的对话事件只归面板窗；panel 事件例外（管开窗 + 关联气泡，两窗都收）
  if (e.surface && e.surface !== "pet" && e.kind !== "panel") return;
  // M3 归属过滤：事件属于其他会话（大窗的 run / 别的会话）→ 跳过渲染。
  // 小窗固定会话：只渲染 petConvId 归属的事件，其余已落库到各自会话（切过去可见）。
  if (e.conversationId && petConvId.value && e.conversationId !== petConvId.value) return;
  switch (e.kind) {
    case "action_proposed":
      state.value = "work";
      // 过程行：技能短标签 + pstate 驱动图标（use_plugin 跳过——成功有 notice，不重复）
      if (e.action?.id && !procSkip(e.action)) {
        procIdx.set(e.action.id, bubbles.value.length);
        surfaceAnchor.set(e.action.id, bubbles.value.length);
        bubbles.value.push({ role: "sys", text: procLabel(e.action), pstate: "run" });
      }
      break;
    case "action_result": {
      // 双窗口：确认可能在面板窗作答，结果回来即收尾（成功短闪 400ms，spec 选项 ①）
      flashValence("success");
      // 过程行收尾：pstate 换图标（失败带 error 摘要）；无匹配行（面板直调等）不动
      const idx = e.action?.id !== undefined ? procIdx.get(e.action.id) : undefined;
      if (idx !== undefined) {
        const ok = e.result?.success !== false;
        bubbles.value[idx].pstate = ok ? "ok" : "fail";
        bubbles.value[idx].text = procLabel(e.action) + procResultSuffix(e.result);
        procIdx.delete(e.action!.id!);
      }
      // 唤起条/扩展存素材回执：LLM 摘要打标完成后到标题（quiet 不弹面板，气泡即凭证）
      if (e.action?.skill_id === "zimeiti.mat_save" && e.action?.id?.startsWith("pa_") && e.result?.success) {
        const title = (e.result as { data?: { title?: string } }).data?.title;
        const receipt = title ? `已存素材：《${title}》` : "已存素材";
        if (!expanded.value) {
          // 收起态（存素材时窗多半收着）：说话气泡直接告知 + 定时收起，不依赖系统通知权限
          speech.value = receipt;
          speechStreaming.value = false;
          showSpeechBubble();
          if (speechTimer) clearTimeout(speechTimer);
          speechTimer = setTimeout(hideSpeechBubble, 4000);
        } else {
          bubbles.value.push({ role: "sys", text: receipt, icon: "doc" });
        }
      }
      break;
    }
    case "final_reply_chunk": {
      // 收起态：回复进气泡（默认不展开对话窗）
      if (!expanded.value) {
        speech.value = (speech.value ?? "") + (e.text ?? "");
        speechStreaming.value = true;
        showSpeechBubble();
        break;
      }
      // 展开态：流式增量拼到当前 streaming bubble（首片时新建）
      if (streamingIdx.value === null) {
        bubbles.value.push({ role: "ai", text: e.text ?? "" });
        streamingIdx.value = bubbles.value.length - 1;
      } else {
        bubbles.value[streamingIdx.value].text += e.text ?? "";
      }
      break;
    }
    case "final_reply": {
      // 收起态：完整文本收尾进气泡 + 定时自动收起
      if (!expanded.value) {
        speech.value = e.text ?? "";
        speechStreaming.value = false;
        showSpeechBubble();
        if (speechTimer) clearTimeout(speechTimer);
        speechTimer = setTimeout(hideSpeechBubble, 8000);
        clearExplicit();
        break;
      }
      // 展开态：以完整文本为准收尾（兜底 chunk 丢失）；语音中保持 say 等 speaking_done
      const full = e.text ?? "";
      if (streamingIdx.value !== null) {
        bubbles.value[streamingIdx.value].text = full;
        streamingIdx.value = null;
      } else {
        bubbles.value.push({ role: "ai", text: full });
      }
      if (state.value !== "say") state.value = "idle";
      clearExplicit();
      break;
    }
    case "interrupted":
      if (streamingIdx.value !== null) {
        bubbles.value[streamingIdx.value].halted = true;
        streamingIdx.value = null;
      } else {
        bubbles.value.push({ role: "ai", text: "已打断", halted: true });
      }
      state.value = "idle";
      clearExplicit();
      break;
    case "speaking_done":
      state.value = "idle";
      break;
    case "notice":
      // 轻提示（插件展开等，§12-2 要知情）：居中淡色小字，不弹窗不打断；
      // 收起态看不到气泡流 → 标「有事找你」，点团子即见
      bubbles.value.push({ role: "sys", text: e.text ?? "" });
      if (!expanded.value) attentionNeeded.value = true;
      break;
    case "reminder": {
      // 主动提醒：轻提示而非弹窗——亮窗（若隐藏）+ notify 态 + 常驻气泡，等用户点团子来看；
      // 确认闸门（confirmation_needed）不在此列，仍是强制展开。
      // 自主权「气泡」档（e.level）：不主动亮窗，只标「有事找你」；缺省 level 按完整档（兼容旧 sidecar）。
      // morning_recap：气泡可点击 → deep-link 进 home 回顾视图（Task 12）
      const text = e.text ?? "到点了";
      const isRecap = e.type === "morning_recap";
      const recapDay = e.day;
      bubbles.value.push({ role: "ai", text, icon: "clock", recap: isRecap ? recapDay : undefined });
      // —— 反应式渲染（C 最小版）：确定性信号 → 一次性闪现；提醒纪律（档位/互斥/TTS）不变 ——
      const taskDone = e.task?.status === "done" || (e.type === "watch_command" && e.status === "completed");
      const taskFail = e.task?.status === "failed" || (e.type === "watch_command" && e.status === "failed");
      if (e.type === "health_nudge") {
        flashState("stretch", 1500); // 久坐 → 一套伸展操
      } else if (e.type === "late_night") {
        flashState("drowsy", 3000); // 深夜 → 打哈欠（Zz）
      } else if (taskDone || taskFail) {
        // 任务结果 = 轻反应：闪现 + 4s 自收气泡。不弹窗/不常驻/不标「有事找你」——
        // 欢呼不该以打断姿态出现（记录照落 bubbles/Feed，窗藏时闪现不可见也无妨）
        flashState(taskDone ? "success" : "error", taskDone ? 1200 : 900);
        speech.value = text;
        speechStreaming.value = false;
        showSpeechBubble();
        if (speechTimer) clearTimeout(speechTimer);
        speechTimer = setTimeout(hideSpeechBubble, 4000);
        break;
      }
      void (async () => {
        try {
          // 大小窗互斥：大窗开着时提醒由大窗呈现，别把宠物窗再弹出来
          const home = await WebviewWindow.getByLabel("home");
          if (home && (await home.isVisible())) return;
          const win = getCurrentWindow();
          const visible = await win.isVisible();
          if (!visible && e.level === "bubble") {
            attentionNeeded.value = true; // 气泡档：窗藏着就不打扰，点团子即见
            return;
          }
          if (!visible) await win.show();
          if (!expanded.value) {
            attentionNeeded.value = true;
            openBubbleSticky(text);
          }
        } catch { /* 亮窗失败也至少留了气泡 */ }
      })();
      break;
    }
    case "error":
      state.value = "idle";
      streamingIdx.value = null;
      pushWarn(e.text ?? "出错了");
      flashValence("error");
      break;
    case "listening":
      state.value = "listen";
      break;
    case "listening_done":
      // 空识别（超时/没说话）：回 idle 并提示——不能进 think，run_done 不复位状态，会永远卡「思考中」
      // 用户句必须无条件入列：长按团子发生在收起态，等 expanded 再 push 会把识别结果丢掉。
      if (e.text) {
        state.value = "think";
        bubbles.value.push({ role: "user", text: e.text });
      } else {
        state.value = "idle";
        bubbles.value.push({ role: "ai", text: "没听清，再试一次？" });
        if (!expanded.value) {
          speech.value = "没听清，再试一次？";
          speechStreaming.value = false;
          showSpeechBubble();
          if (speechTimer) clearTimeout(speechTimer);
          speechTimer = setTimeout(hideSpeechBubble, 8000);
        }
      }
      break;
    case "speaking":
      state.value = "say";
      break;
    case "panel": {
      // 面板不再无条件弹独立浮窗（调研 §16 反模式）：先把表面属性补到发起它的
      // 那一行上；无 origin 的刷新事件复用同 panel 最近行，否则新建。stage/focus 只可能在
      // explicit 时出现——裁决器非 explicit 本就封顶 peek。
      const panel = e.payload?.panel ?? "";
      const title = e.payload?.title || panel || "插件面板";
      const titleParts = title.split(" · ").map((part) => part.trim()).filter(Boolean);
      const surfaceTitle = titleParts[titleParts.length - 1] || title;
      const plugin = panel.split(":", 1)[0] || panel;
      const explicit = requestedPlugin === plugin;

      // cast 与 HomePlugins.vue:391-393 同款：PanelPayload 里这些字段是宽类型，
      // 而裁决器要窄联合。安全性由 sidecar 保证——_load_panels 已按
      // _SURFACE_LEVELS 过滤过非法值，ActionResult 的 Literal 类型同理。
      const decision = decideSurface({
        suggested: (e.payload?.presentation as Presentation | null | undefined) ?? null,
        attention: (e.payload?.attention as Attention | undefined) ?? "suggest",
        explicit,
        current: null,
        supported: e.payload?.surfaces as Presentation[] | undefined,
      });

      // 先记痕：开窗与否都留一条可点行，用户关窗后仍能一键回去
      deactivateAll(bubbles.value);
      const attr: SurfaceAttr = { panel, title: surfaceTitle, count: surfaceCount(e.payload?.data), live: true };
      const at = e.payload?.origin ? surfaceAnchor.get(e.payload.origin) : undefined;
      const row = at !== undefined ? bubbles.value[at] : undefined;
      if (row) {
        row.surface = attr;
      } else {
        let surfaceRow = -1;
        for (let i = bubbles.value.length - 1; i >= 0; i--) {
          if (bubbles.value[i].surface?.panel === panel) {
            surfaceRow = i;
            break;
          }
        }
        if (surfaceRow >= 0) bubbles.value[surfaceRow].surface = attr;
        else bubbles.value.push({ role: "sys", text: "", surface: attr });
      }
      if (e.payload?.origin) surfaceAnchor.delete(e.payload.origin);

      if (petFormOf(decision) === "window") openPanelWindow();
      break;
    }
  }
}

function onStatus(m: BrainStatusMsg) {
  if (m.status === "up") {
    if (brainDown.value) {
      brainDown.value = false;
      bubbles.value.push({ role: "ai", text: "✓ 大脑已恢复" });
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

function onPerms(p: BrainPermissions) {
  const wasMissing = missingPerms.value;
  perms.value = p;
  if (missingPerms.value) {
    if (!expanded.value) void expand(); // 权限引导必须可见
  } else if (wasMissing) {
    bubbles.value.push({ role: "ai", text: "✓ 权限就绪" });
  }
}

async function submit(text: string, contexts: InputContext[] = []) {
  const fullText = formatContextPrefix(contexts) + text;   // @ 文件/附件 chips 前缀进文本（气泡与大脑同源）
  bubbles.value.push({ role: "user", text: fullText });
  surfaceAnchor.clear();
  clearExplicit();
  state.value = "think";
  // 截图即问：有框选待提问 → 走 vision 直答（不占 run/对话历史）
  if (snipCtx.value) {
    bubbles.value.push({ role: "sys", text: `已附带区域截图 ${snipCtx.value.width}×${snipCtx.value.height}`, icon: "doc" });
    snipCtx.value = null;
    try {
      await visionQuery(fullText);
    } catch (err) {
      pushWarn("发送失败：" + String(err));
      state.value = "idle";
    }
    return;
  }
  // 划词上下文：气泡只显示用户打的字，给大脑的消息自包含拼好（大脑看不到前台选中）
  let msg = fullText;
  if (selectionCtx.value) {
    msg = `用户在前台应用选中了一段文字：\n「${selectionCtx.value}」\n\n用户的指示：${fullText}`;
    bubbles.value.push({ role: "sys", text: `已附带选中文字 ${selectionCtx.value.length} 字`, icon: "doc" });
    selectionCtx.value = null;
  }
  try {
    await ensurePetConversation(); // 兜底：首启未取到会话时先建（否则消息不落库）
    const wanted = matchExplicitOpen(text, allPlugins.value);   // 插件名匹配用原始文本（不含前缀）
    if (wanted) markExplicit(wanted);
    await runInput(msg, "pet", petConvId.value);
  } catch (err) {
    clearExplicit();
    pushWarn("发送失败：" + String(err));
    state.value = "idle";
  }
}

async function decide(approved: boolean, remember = false) {
  if (!pending.value) return;
  const { id } = pending.value;
  state.value = "think";
  beginApprovalGuard(approved);
  try {
    await sendConfirmBatch([{ id, approved, remember: pendingCanRemember.value && remember }]);
    rememberPending.value = false;
    releaseApprovalGuard();
  } catch (err) {
    clearApprovalGuard();
    pushWarn("确认失败：" + String(err));
    state.value = "idle";
  }
}

async function decideAllPending(approved: boolean) {
  if (pendingConfirms.value.length < 2) return;
  const items = pendingConfirms.value.map(({ id }) => ({ id, approved, remember: false }));
  state.value = "think";
  beginApprovalGuard(approved);
  try {
    await sendConfirmBatch(items);
    releaseApprovalGuard();
  } catch (err) {
    clearApprovalGuard();
    pushWarn("批量确认失败：" + String(err));
    state.value = "idle";
  }
}

/** 审批卡会乐观出队；保留短暂回执占位，吸收连点，避免同一位置瞬间变成「停止」。 */
function beginApprovalGuard(approved: boolean) {
  if (approvalGuardTimer) clearTimeout(approvalGuardTimer);
  approvalGuardTimer = null;
  approvalGuard.value = approved ? "allowed" : "denied";
}

function releaseApprovalGuard(delay = 850) {
  if (approvalGuardTimer) clearTimeout(approvalGuardTimer);
  approvalGuardTimer = setTimeout(() => {
    approvalGuard.value = null;
    approvalGuardTimer = null;
  }, delay);
}

function clearApprovalGuard() {
  if (approvalGuardTimer) clearTimeout(approvalGuardTimer);
  approvalGuardTimer = null;
  approvalGuard.value = null;
}

function onMic() {
  // 不乐观置 listen：等大脑 listening 事件确认（语音栈不可用时大脑会回 error，别自欺卡死）
  void ensurePetConversation().then(() => voiceStart("pet", false, petConvId.value)).catch((err) => {
    pushWarn("语音启动失败：" + String(err));
  });
}

function onMicContinuous() {
  // 长按团子 = 连续对话：答完接着听，说「退出」或点团子结束（会话提示由大脑 notice 落气泡）
  void ensurePetConversation().then(() => voiceStart("pet", true, petConvId.value)).catch((err) => {
    pushWarn("语音启动失败：" + String(err));
  });
}

function onInterrupt() {
  // 不看 busy：TTS 播完字后打断若晚到，state 可能已和音频脱节；多发一次 interrupt 无害。
  void interrupt().catch((err) => {
    pushWarn("打断失败：" + String(err));
  });
}

// ---- 短暂闪现（success/error/stretch/drowsy…）：ms 后回 idle，期间不可打断（busy 是 allowlist，闪现态天然不在内）----
let flashTimer: ReturnType<typeof setTimeout> | null = null;
function flashState(v: AvatarState, ms = 400) {
  if (flashTimer) clearTimeout(flashTimer);
  state.value = v;
  flashTimer = setTimeout(() => {
    if (state.value === v) state.value = "idle";
    flashTimer = null;
  }, ms);
}
function flashValence(v: "success" | "error") {
  flashState(v, 400);
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === "Escape" && expanded.value) void collapse();
}

// 展开时整窗可交互；说话气泡只把「气泡带」加成第二热区（点气泡=展开），其余透明区照常穿透到桌面
watch(expanded, (v) => {
  void setInteractiveFull(v);
});

/** 确保小窗固定会话存在（方案 A：不镜像大窗活跃会话；无则 Rust 侧新建 + 存 pet 指针） */
async function ensurePetConversation(): Promise<void> {
  if (petConvId.value) return;
  const meta = await invoke<{ id: string } | null>("ensure_pet_conversation").catch(() => null);
  if (meta?.id) petConvId.value = meta.id;
}

/** 从 Rust 权威重拉小窗固定会话消息渲染（启动恢复 / 跨窗刷新）。
 *  流式进行中不刷新——重建气泡会打断正在渲染的回复。 */
async function reloadMessages(): Promise<void> {
  if (!petConvId.value) return;
  if (streamingIdx.value !== null) return;
  const rows = await invoke<PetMessage[]>("get_conversation_messages", { id: petConvId.value, limit: 500 }).catch(() => null);
  if (!rows) return;
  bubbles.value = rows.map((m) => ({
    role: m.role,
    text: m.payload.text,
    halted: m.payload.halted,
    icon: m.payload.icon,
  }) as BubbleMsg);
  streamingIdx.value = null;
  procIdx.clear(); // 过程行下标随气泡重建作废
  surfaceAnchor.clear(); // 表面锚点同样指向气泡下标，随重建作废
  // 列表整表重建，旧偏移失效；收起期间重拉则下次打开对准最新
  stickBottom = true;
  savedScrollTop = 0;
  if (expanded.value) restoreBubbleScroll();
}

onMounted(async () => {
  void setInteractiveFull(false);
  syncHotRects(); // 初始上报团子热区（Rust 据此放行穿透 + 驱动 hover）
  // 会话恢复：与大窗镜面共用 active_conversation（Rust SQLite 权威），重启拉回消息。
  try {
    clearLegacySessionKeys();
    await sessionStore.restore();
    await ensurePetConversation();
    await reloadMessages();
    sessionStore.window.updateState("pet", { visible: true, focusedConversationId: petConvId.value });
  } catch { /* 恢复失败不阻塞宠物窗启动 */ }
  void loadPlugins();
  // 顶部边界自适应：监听窗口移动（拖动 setPosition 触发），贴顶时调整团子锚点
  try {
    scaleCached = (await getCurrentWindow().scaleFactor()) || 1;
  } catch { /* 默认 1 */ }
  unlistenMoved = await getCurrentWindow().onMoved((e) => onWindowMoved(e.payload));
  // 初始对齐团子锚点：窗口初始位置若贴近顶部，petY 应为压缩值（而非默认 100）
  try {
    const p0 = await getCurrentWindow().outerPosition();
    onWindowMoved({ x: p0.x, y: p0.y });
  } catch { /* 忽略 */ }
  unlisten = await onBrainEvent(onEvent);
  unlistenRunDone = await onRunDone(() => clearExplicit());
  unlistenStatus = await onBrainStatus(onStatus);
  unlistenPerms = await onBrainPermissions(onPerms);
  // 跨窗刷新：大窗向本窗固定会话发了消息（用户消息无事件流）→ 重拉。
  // 方案 A 下小窗不跟随大窗切会话（固定 pet 会话），故不订阅 active-conversation-changed。
  unlistenConvUpdated = await listen<{ conversationId: string; from: string }>("conversation-updated", (e) => {
    if (e.payload.from === getCurrentWindow().label) return; // 自己发的已渲染
    if (e.payload.conversationId !== petConvId.value) return;
    void reloadMessages();
  });
  unlistenPanelClosed = await onPanelClosed(() => {
    if (!panelOpen.value) return;
    panelOpen.value = false;
  });
  unlistenApprovals = onPendingConfirms((items) => {
    const previousCount = pendingConfirms.value.length;
    pendingConfirms.value = items.filter((item) => !item.surface || item.surface === "pet");
    if (pendingConfirms.value.length === 0) {
      rememberPending.value = false;
      return;
    }
    state.value = "idle";
    if (pendingConfirms.value.length > 1) {
      attentionNeeded.value = true;
      if (!expanded.value) {
        openBubbleSticky(`${pendingConfirms.value.length} 项待批准，可在小窗全部处理`);
      }
    } else if (previousCount === 0 && !expanded.value) {
      // 单条直接展开快批；多条先以常驻气泡提醒，用户展开后可整批处理。
      void expand();
    }
  });
  // 感知观察中叠加点：一次性取数兜底 + brain-settings 回推刷新（设置页改开关即时反映）
  void getSettingsOnce().then(syncObserving);
  unlistenSettings = await onSettings(syncObserving);
  // 首启引导（生产打包首跑：装 Python 环境/下模型，大脑还没起来，走 Tauri 事件直推）
  unlistenSetup = await listen<{ stage: string; detail: string }>("setup-progress", (e) => {
    if (e.payload.stage !== "done" && !expanded.value) void expand();
    bubbles.value.push({ role: "sys", text: e.payload.detail });
  });
  unlistenSetupErr = await listen<string>("setup-error", (e) => {
    if (!expanded.value) void expand();
    pushWarn(e.payload);
  });
  unlistenSetupCfg = await listen<string>("setup-config-needed", () => void onSetupNeeded());
  // 全局唤起：⌘⇧Y 反射键（pet-show 确保展开）/ ⌘⇧U 划词唤起（有选词静默待选动作，无选词展开 + 聚焦）
  unlistenInvoke = await listen("pet-show", () => void onPetShow());
  unlistenInvokeSel = await listen<{ text: string | null }>("pet-invoke-selection", (e) =>
    void onPetInvokeSelection(e.payload.text),
  );
  // 光标进团子热区：补 hover 态 + 显示快捷面板（单窗内容层切换，零 resize）；
  // 离开清 hover + 收起面板。enter 仅由 Rust 团子热区（kind=pet）驱动，面板/输入条不会误触发。
  unlistenCursorEnter = await listen("pet-cursor-enter", () => {
    setPetHover(true);
    if (speechVisible.value) return; // 回复气泡显示中不弹快捷面板（区域重叠）
    quick.value = true;
    syncHotRects();
  });
  unlistenCursorLeave = await listen("pet-cursor-leave", () => {
    setPetHover(false);
    quick.value = false;
    syncHotRects();
  });
  // 热区兜底同步（布局/字号变化自动跟随；窗口拖动由 Rust 叠加位置自动跟随）
  rectTimer = setInterval(syncHotRects, 2000);
  // 唤起条动作（invoke-bar 广播）：解释/翻译/存素材
  unlistenInvokeAction = await onInvokeAction((action) => { void handleInvokeAction(action); });
  // 截图即问（⌘⇧I 框选完成广播）：展开 + chip 提示提问
  unlistenSnip = await onSnipCaptured((r) => { void onSnipReady(r); });
  // 主动拉一次配置：首启引导若秒过（venv/模型已在），setup-config-needed 可能先于挂载发出而丢——靠拉取兜底
  try {
    const cfg = await invoke<{ has_key: boolean }>("get_setup_config");
    if (!cfg.has_key) void onSetupNeeded();
  } catch { /* 忽略，事件路径仍兜底 */ }
  window.addEventListener("keydown", onKeydown);
});
onUnmounted(() => {
  unlisten?.();
  unlistenRunDone?.();
  unlistenStatus?.();
  unlistenPerms?.();
  unlistenPanelClosed?.();
  unlistenSetup?.();
  unlistenSetupErr?.();
  unlistenSetupCfg?.();
  unlistenInvoke?.();
  unlistenInvokeSel?.();
  unlistenInvokeAction?.();
  unlistenSnip?.();
  unlistenApprovals?.();
  unlistenSettings?.();
  unlistenCursorEnter?.();
  unlistenCursorLeave?.();
  unlistenConvUpdated?.();
  unlistenMoved?.();
  if (rectTimer) clearInterval(rectTimer);
  if (speechTimer) clearTimeout(speechTimer);
  window.removeEventListener("keydown", onKeydown);
  if (clickTimer !== null) clearTimeout(clickTimer);
  if (flashTimer !== null) clearTimeout(flashTimer);
  if (drowsyTimer !== null) clearTimeout(drowsyTimer);
  if (approvalGuardTimer !== null) clearTimeout(approvalGuardTimer);
});
</script>

<template>
  <div class="shell" :class="{ quick: quick && !expanded, exp: expanded }">
    <!-- 收起/快捷态：团子 + 快捷面板同窗（恒 320×300，内容层切换，零 resize）。
         hover 由 Rust 团子热区驱动 pet-cursor-enter → v-show 面板；移开 480ms 自动收起。 -->
    <template v-if="!expanded">
      <div class="pet" :style="{ top: petY + 'px' }" @pointerenter="onPetHover">
        <Avatar
          class="pet-avatar"
          :state="petState"
          :size="96"
          :observing="observing"
          @click="onPetClick"
          @longpress="onMicContinuous"
          @drag-start="onPetDragStart"
          @drag-end="onPetDragEnd"
        />
      </div>
      <Transition name="quick">
        <QuickPanel
          v-show="quick"
          :busy="busy"
          :listening="state === 'listen'"
          :pet-y="petY"
          @submit="onQuickSubmit"
          @launch="onQuickLaunch"
          @mic="onQuickMic"
          @interrupt="onQuickInterrupt"
        />
      </Transition>
      <!-- 收起态回复气泡：长按语音/快捷输入回复默认只走气泡；点击展开完整对话 -->
      <Transition name="speech">
        <div
          v-if="speechVisible && speech"
          class="speech-zone"
          :style="{ top: petY + 'px' }"
          @click="onSpeechExpand"
        >
          <SpeechBubble :text="speech" :streaming="speechStreaming" />
        </div>
      </Transition>
    </template>

    <!-- 对话：header（头像+名称+状态+收起，一体化贴边）/ 内容区（权限引导/气泡流/输入条） -->
    <template v-else>
      <header class="chat-header flip" data-tauri-drag-region @dblclick="openHome">
        <div class="hbtns" data-tauri-drag-region @dblclick.stop>
          <button class="hbtn" title="收起" @click="collapse">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
              <line x1="6" y1="12" x2="18" y2="12" />
            </svg>
          </button>
          <button class="hbtn" title="打开大窗（完整界面）" @click="openHome">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M15 3h6v6" />
              <path d="M9 21H3v-6" />
              <path d="M21 3l-7 7" />
              <path d="M3 21l7-7" />
            </svg>
          </button>
          <button
            class="hbtn view-toggle"
            :class="{ active: view === 'plugins' }"
            :title="view === 'chat' ? '插件' : '返回对话'"
            :aria-label="view === 'chat' ? '打开插件' : '返回对话'"
            @click="expandTo(view === 'chat' ? 'plugins' : 'chat')"
          >
            <YbIcon :name="view === 'chat' ? 'plug' : 'chat'" :size="14" />
          </button>
        </div>
        <Avatar :state="petState" :size="34" :observing="observing" @click="onAvatarClick" />
        <div class="meta" data-tauri-drag-region>
          <span class="name">译宝</span>
          <span class="status" :class="petState"><i class="dot" />{{ statusText }}</span>
        </div>
      </header>

      <div class="chat-body">
      <SetupWizard v-if="setupNeeded" :model="setupCfg.model" :base-url="setupCfg.baseUrl" :voice="setupCfg.voice" @saved="onSetupSaved" />

      <template v-if="!setupNeeded">
      <PermissionsBanner v-if="missingPerms && perms" :perms="perms" />

      <!-- 插件启动器视图（双击团子进来）：列出插件，点击直达它的主面板 -->
      <div v-if="view === 'plugins'" class="bubbles">
        <div class="pl-head">
          <span class="pl-title">插件</span>
          <span class="pl-subtitle">选择一个能力继续</span>
        </div>
        <div v-if="pluginErr" class="pl-err"><YbIcon name="alert" :size="14" />{{ pluginErr }}</div>
        <div class="pl-grid">
          <button v-for="p in plugins" :key="p.id" class="pl-card" @click="launchPlugin(p)">
            <span class="pl-card-ico" :style="iconStyle(p.id)">{{ initial(p.name) }}</span>
            <span class="pl-card-name">{{ p.name }}</span>
            <span class="pl-card-id">{{ p.id }}</span>
          </button>
        </div>
        <div v-if="!plugins.length && !pluginErr" class="pl-empty">没有发现插件</div>
      </div>

      <div v-else class="bubbles" ref="bubblesRef">
        <div v-if="!bubbles.length && !showTyping" class="empty-hint">
          <Avatar :state="petState" :size="56" />
          <p>叫我做什么都行～</p>
          <div class="chips">
            <button v-for="c in suggestions" :key="c" class="chip" @click="submit(c)">{{ c }}</button>
          </div>
        </div>
        <template v-for="(b, i) in bubbles" :key="i">
          <SurfaceLine v-if="b.surface" :attr="b.surface" @open="openPanelWindow()" />
          <Bubble
            v-else
            :role="b.role"
            :text="b.text"
            :streaming="i === streamingIdx"
            :pstate="b.pstate"
            :halted="b.halted"
            :icon="b.icon"
            :class="{ 'recap-clickable': !!b.recap }"
            @click="onRecapClick(b.recap)"
          />
        </template>
        <Bubble v-if="showTyping" role="ai" text="" typing />
      </div>

      <div v-if="view === 'chat'" class="input-slot">
        <!-- 划词上下文 chip：⌘⇧U 抓到选中文字后等待指示，可 × 掉；发送后自消 -->
        <div v-if="selectionCtx" class="ctx-chip">
          <YbIcon class="ctx-ic" name="doc" :size="13" />
          <span class="ctx-text" :title="selectionCtx">{{ ctxPreview }}</span>
          <button class="ctx-x" title="去掉上下文" @click="selectionCtx = null">×</button>
        </div>
        <!-- 截图即问 chip：⌘⇧I 框选后等待提问，可 × 掉；发送后自消 -->
        <div v-if="snipCtx" class="ctx-chip">
          <YbIcon class="ctx-ic" name="doc" :size="13" />
          <span class="ctx-text">区域截图 {{ snipCtx.width }}×{{ snipCtx.height }}，想问什么？</span>
          <button class="ctx-x" title="去掉截图" @click="snipCtx = null">×</button>
        </div>
        <div v-if="approvalGuard" class="approval-guard" role="status" aria-live="polite">
          <YbIcon :name="approvalGuard === 'allowed' ? 'check' : 'x'" :size="15" :stroke="2" />
          <span>{{ approvalGuard === "allowed" ? "已允许，正在继续" : "已拒绝" }}</span>
        </div>
        <InputBar v-else-if="!pending" ref="inputBarRef" :busy="busy" :listening="state === 'listen'" @submit="submit" @mic="onMic" @interrupt="onInterrupt" />
        <div v-else-if="pendingConfirms.length > 1" class="batch-confirm-notice">
          <div class="batch-copy">
            <strong>{{ pendingConfirms.length }} 项待批准</strong>
            <span>逐项核对或分别记住选择，请打开收件箱。</span>
          </div>
          <div class="batch-actions">
            <button class="quick-deny" @click="decideAllPending(false)">全部拒绝</button>
            <button class="quick-allow" @click="decideAllPending(true)">全部批准</button>
            <button class="confirm-open" @click="openHome">打开收件箱</button>
          </div>
        </div>
        <div v-else class="quick-confirm">
          <div class="quick-copy">
            <strong><YbIcon class="qc-ic" name="alert" :size="14" />{{ pending.label || pending.skill }}</strong>
            <span v-if="pending.desc">{{ pending.desc }}</span>
          </div>
          <label v-if="pendingCanRemember" class="quick-remember">
            <input v-model="rememberPending" type="checkbox" />
            {{ rememberLabelForSkill(pending.skill) }}
          </label>
          <div class="quick-actions">
            <button class="quick-deny" @click="decide(false)">拒绝</button>
            <button class="quick-allow" @click="decide(true, rememberPending)">允许</button>
          </div>
        </div>
      </div>
      </template>
      </div>
    </template>
  </div>
</template>

<style scoped>
.shell {
  position: relative;
  height: 100vh;
  box-sizing: border-box;
  overflow: hidden;
  font-family: var(--yb-font);
  font-size: var(--yb-fs-lg);
  line-height: var(--yb-lh-base);
  color: var(--yb-text);
}
.shell.exp {
  display: flex;
  flex-direction: column;
  background: var(--yb-shell-bg);
  border: 1px solid var(--yb-border-strong);
  border-radius: 20px;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.96),
    inset 0 -1px 0 rgba(var(--yb-c-slate-rgb), 0.035);
  /* 背景淡入：窗口 resize 瞬间不再是透明硬切 */
  animation: shell-in 0.22s var(--yb-ease-out) both;
}
@keyframes shell-in {
  from { opacity: 0; }
  to { opacity: 1; }
}
/* 内容区：header 贴边一体化，其余内容在这里呼吸。
 * 顶部 padding 0：header 灰边下无空档，气泡区直接顶格。 */
.chat-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: var(--yb-space-3);
  padding: 0 var(--yb-space-3) var(--yb-space-3);
}
/* 收起/快捷态：恒窗 320×300 内，团子锚点 x:144、y 动态（正常 100，贴顶时由
 * onWindowMoved 下移，inline style 覆盖；输入条在脚下，插件在输入条下方
 * y: petY+98，与 QuickPanel 布局常量一致）。团子热区由 syncHotRects 上报 Rust
 * （kind=pet）放行穿透 + 驱动 hover；窗口整体可拖，拖动=移动窗口。 */
.pet {
  position: absolute;
  left: 144px;
  top: 100px;
  width: 96px;
  height: 96px;
  pointer-events: auto;
  user-select: none;
}
.pet .pet-avatar {
  display: block;
}
.pet .pet-avatar :deep(.av) {
  cursor: grab;
}
/* 收起态回复气泡：团子左侧，宽度自适应（max-width = 左侧空间极限 136px，
 * 团子左缘 144 - 8 边距，整体布局右移 32 给气泡让出空间）；长回复走马灯不受宽度影响。跟随 petY。 */
.speech-zone {
  position: absolute;
  left: 8px;
  width: fit-content;
  max-width: 136px;
  cursor: pointer;
}

/* ---- 状态切换过渡（弹出质感）----
 * quick 面板：hover 弹出——从团子向下长出 + spring 回弹，收起时缩回淡出 */
.quick-enter-active {
  transition: opacity 0.16s ease-out, transform 0.38s var(--yb-ease-spring);
}
.quick-enter-from {
  opacity: 0;
  transform: translateY(-10px);
}
.quick-leave-active {
  transition: opacity 0.16s ease-in, transform 0.18s ease-in;
}
.quick-leave-to {
  opacity: 0;
  transform: translateY(-8px);
}
/* 3 圆逐个弹出：stagger + scale pop（backwards 填充——动画结束后恢复普通样式，不压 hover transform） */
.quick-enter-active :deep(.wb-dock) {
  animation: dock-pop 0.42s var(--yb-ease-spring) backwards;
}
.quick-enter-active :deep(.wb-dock:nth-of-type(1)) { animation-delay: 0.02s; }
.quick-enter-active :deep(.wb-dock:nth-of-type(2)) { animation-delay: 0.08s; }
.quick-enter-active :deep(.wb-dock:nth-of-type(3)) { animation-delay: 0.14s; }
@keyframes dock-pop {
  from { opacity: 0; transform: scale(0.55) translateY(-10px); }
  to { opacity: 1; transform: scale(1) translateY(0); }
}
/* 收起态气泡：淡入淡出（位移由 SpeechBubble 自带 rise 负责，避免叠影） */
.speech-enter-active {
  transition: opacity 0.25s var(--yb-ease-out);
}
.speech-leave-active {
  transition: opacity 0.18s var(--yb-ease-in);
}
.speech-enter-from,
.speech-leave-to {
  opacity: 0;
}
/* chat 头部先弹出，body 紧随（同源 transform-origin，整体像从团子处"弹开"） */
.shell.exp .chat-header {
  animation: chat-in 0.3s var(--yb-ease) 0.02s both;
  transform-origin: 18% 8%;
}
/* 展开内容弹出：body 从团子方位 scale 放大（spring 回弹，掩盖窗口 resize 硬切） */
.shell.exp .bubbles,
.shell.exp .input-slot {
  animation: chat-in 0.34s var(--yb-ease) 0.06s both;
  transform-origin: 18% 8%;
}
/* 输入区：chip（划词上下文）贴左坐在输入条上方 */
.input-slot {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.quick-confirm,
.batch-confirm-notice,
.approval-guard {
  display: flex;
  align-items: center;
  gap: var(--yb-space-2);
  padding: 9px 10px;
  margin: 0 2px 10px;
  border: 1px solid rgba(var(--yb-c-amber-rgb), 0.32);
  border-radius: var(--yb-radius-md);
  background: var(--yb-intent-pending-soft);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.7);
}
.approval-guard {
  min-height: 34px;
  box-sizing: border-box;
  justify-content: center;
  color: var(--yb-text-dim);
  background: var(--yb-surface-solid);
  border-color: var(--yb-border-base);
  pointer-events: auto;
  user-select: none;
}
.approval-guard .yb-icon {
  color: var(--yb-intent-ok);
}
.quick-copy,
.batch-copy {
  min-width: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
  line-height: var(--yb-lh-ui);
}
.quick-copy strong,
.batch-confirm-notice strong {
  overflow: hidden;
  color: var(--yb-text);
  font-size: var(--yb-fs-md);
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 快批条行首图标：待批准意图琥珀，与收件箱同语言 */
.qc-ic {
  color: var(--yb-intent-pending-ink);
  margin-right: var(--yb-space-1);
}
.quick-copy span,
.batch-confirm-notice span {
  overflow: hidden;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-sm);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.quick-remember {
  display: flex;
  align-items: center;
  gap: 4px;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-sm);
  white-space: nowrap;
}
.quick-remember input {
  margin: 0;
  accent-color: var(--yb-accent);
}
.quick-actions,
.batch-actions {
  display: flex;
  gap: 5px;
}
.quick-actions button,
.batch-actions button,
.confirm-open {
  min-height: 32px;
  padding: 6px 10px;
  border: 0;
  border-radius: var(--yb-radius-sm);
  cursor: pointer;
  font: inherit;
  white-space: nowrap;
}
.quick-deny {
  color: var(--yb-text-dim);
  background: var(--yb-btn-neutral);
}
.quick-allow,
.confirm-open {
  color: var(--yb-text-on-accent);
  background: var(--yb-accent);
}
.quick-actions button:focus-visible,
.batch-actions button:focus-visible,
.confirm-open:focus-visible {
  outline: none;
  box-shadow: var(--yb-focus-ring);
}
.ctx-chip {
  align-self: flex-start;
  max-width: 100%;
}
@keyframes chat-in {
  0% {
    opacity: 0;
    transform: scale(0.8) translateY(12px);
  }
  100% {
    opacity: 1;
    transform: none;
  }
}
/* header：与大窗同源的近白微光顶栏，保持品牌连续，不再使用独立的蓝灰色块。 */
.chat-header {
  position: relative; /* 收起钮绝对定位的锚 */
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 66px;
  box-sizing: border-box;
  padding: 8px var(--yb-space-3);
  background:
    linear-gradient(180deg, rgba(var(--yb-c-sky-rgb), 0.045), rgba(var(--yb-c-sky-rgb), 0) 100%),
    var(--yb-shell-bg);
  border-bottom: 1px solid var(--yb-border-base);
}
/* 锚点在右侧时（dir=ne/se）镜像头部，团子+meta 成团靠右（row-reverse 默认即靠右） */
.chat-header.flip {
  flex-direction: row-reverse;
}
/* meta 不撑开：挨着团子站，名称与状态胶囊互相居中 */
.meta {
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  line-height: var(--yb-lh-tight);
  /* 拖动把手区（名称/状态文字上按住可拖窗） */
  cursor: default;
  user-select: none;
}
.name {
  font-size: var(--yb-fs-lg);
  font-weight: var(--yb-fw-bold);
  letter-spacing: 0.01em;
}
/* 状态 pill：软底小胶囊，比裸文字更有「状态感」 */
.status {
  font-size: var(--yb-fs-xs);
  color: var(--yb-text-dim);
  display: inline-flex;
  align-items: center;
  gap: 5px;
  line-height: var(--yb-lh-ui);
  padding: 1px 8px;
  border-radius: var(--yb-radius-pill);
  background: var(--yb-well);
}
/* 状态点：颜色跟团子状态色环同源 */
.status .dot {
  width: 5px;
  height: 5px;
  flex-shrink: 0;
  border-radius: 50%;
  background: var(--dot, var(--yb-idle));
}
.status.idle {
  --dot: var(--yb-idle);
}
.status.listen {
  --dot: var(--yb-listen);
  background: var(--yb-danger-soft);
  color: var(--yb-danger);
}
.status.think,
.status.work {
  --dot: var(--yb-think);
  background: var(--yb-accent-soft);
  color: var(--yb-accent-deep);
}
.status.work {
  --dot: var(--yb-work);
}
.status.say {
  --dot: var(--yb-say);
  background: var(--yb-accent-soft);
  color: var(--yb-accent-deep);
}
.status.success {
  --dot: var(--yb-state-success);
}
.status.error {
  --dot: var(--yb-state-error);
  background: var(--yb-danger-soft);
  color: var(--yb-danger);
}
.status.notify {
  --dot: var(--yb-state-notify);
  background: var(--yb-intent-notify-soft);
  color: var(--yb-intent-notify);
}
.status.drowsy {
  --dot: var(--yb-state-idle);
}
/* header 按钮组：透明底融入 header（白底胶囊在浅天青 header 上突兀），
 * 每个按钮 hover 才显底 + active 按压，简洁有质感 */
.hbtns {
  position: absolute;
  left: 10px;
  top: 50%;
  transform: translateY(-50%);
  display: flex;
  gap: 3px;
  padding: 2px;
  background: transparent;
  border: none;
}
.hbtn {
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  border: none;
  border-radius: var(--yb-radius-sm);
  background: transparent;
  color: var(--yb-text-dim);
  cursor: pointer;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.hbtn:hover {
  background: var(--yb-surface-solid);
  color: var(--yb-text);
}
.hbtn.active {
  color: var(--yb-accent-deep);
  background: var(--yb-accent-soft);
  box-shadow: inset 0 0 0 1px rgba(var(--yb-c-sky-rgb), 0.16);
}
.hbtn:focus-visible {
  outline: none;
  box-shadow: var(--yb-focus-ring);
}
.hbtn:active {
  transform: scale(0.92);
}
.hbtn svg {
  width: 14px;
  height: 14px;
}
.bubbles :deep(.bubble.ai) {
  background: var(--yb-bubble-ai);
  border-color: rgba(var(--yb-c-slate-rgb), 0.15);
  box-shadow: none;
}
.bubbles {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: var(--yb-space-2);
  overflow-y: auto;
  padding: 4px 2px 0;
  scrollbar-width: thin;
  /* 顶部渐隐：滚出视口的消息柔和淡出。原先 mask 在 macOS WKWebView luminance 模式
   * 下可能误渲染为深色伪影（与 ::selection 叠加形成"深蓝条"），先关掉。 */
  /* mask-image: linear-gradient(180deg, transparent, #000 14px);
  -webkit-mask-image: linear-gradient(180deg, transparent, #000 14px); */
}
.bubbles::-webkit-scrollbar {
  width: 6px;
}
.bubbles::-webkit-scrollbar-thumb {
  background: var(--yb-surface-border);
  border-radius: var(--yb-radius-pill);
}
/* morning_recap 气泡可点击 deep-link 到回顾（class 经 fallthrough 落到 Bubble 根 div） */
.recap-clickable {
  cursor: pointer;
  transition: filter var(--yb-dur-fast) var(--yb-ease-out);
}
.recap-clickable:hover {
  filter: brightness(0.96);
}
/* 空状态：气泡区占位引导（小号团子 + 一句招呼 + 建议 chip） */
.empty-hint {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-lg);
}
.empty-hint p {
  margin: 0 0 2px;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: var(--yb-space-2);
}
.chip {
  padding: 5px 12px;
  border: 1px solid var(--yb-surface-border);
  border-radius: var(--yb-radius-pill);
  background: var(--yb-surface-solid);
  color: var(--yb-accent-deep);
  font-size: var(--yb-fs-lg);
  cursor: pointer;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.chip:hover {
  background: var(--yb-accent-soft);
  border-color: var(--yb-accent);
  color: var(--yb-accent-deep);
}
/* 划词上下文 chip：淡 accent 底胶囊，贴在输入条上方 */
.ctx-chip {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 10px;
  border-radius: var(--yb-radius-pill);
  background: var(--yb-accent-soft);
  border: 1px solid rgba(var(--yb-c-sky-rgb), 0.25);
  color: var(--yb-accent-deep);
  font-size: var(--yb-fs-md);
  line-height: var(--yb-lh-ui);
}
.ctx-ic {
  flex-shrink: 0;
}
.ctx-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ctx-x {
  flex-shrink: 0;
  width: 18px;
  height: 18px;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--yb-accent-deep);
  font-size: var(--yb-fs-lg);
  line-height: 1;
  cursor: pointer;
  display: grid;
  place-items: center;
  transition: background var(--yb-dur-fast) var(--yb-ease-out);
}
.ctx-x:hover {
  background: rgba(var(--yb-c-sky-rgb), 0.18);
}
/* ---- 插件启动器 ---- */
.pl-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 2px 4px;
}
.pl-title {
  font-size: var(--yb-fs-lg);
  font-weight: var(--yb-fw-bold);
}
.pl-subtitle {
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-sm);
}
.pl-err {
  display: flex;
  align-items: center;
  gap: var(--yb-space-1);
  padding: 6px var(--yb-space-3);
  border-radius: var(--yb-radius-sm);
  background: var(--yb-danger-soft);
  color: var(--yb-danger);
  font-size: var(--yb-fs-md);
}
/* 插件 grid（Launchpad 式）：2 列网格卡，上大 icon + 下名字/id，hover 上浮 */
.pl-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
  padding: 2px;
}
.pl-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 5px;
  padding: 14px 8px 12px;
  border: 1px solid var(--yb-surface-border);
  border-radius: var(--yb-radius-md);
  background: var(--yb-card-bg);
  box-shadow: var(--yb-shadow-1);
  cursor: pointer;
  font-family: inherit;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.pl-card:hover {
  border-color: var(--yb-accent);
  background: var(--yb-surface-solid);
  transform: translateY(-2px);
  box-shadow: var(--yb-shadow-2);
}
.pl-card:active {
  transform: scale(0.97);
}
.pl-card-ico {
  width: 42px;
  height: 42px;
  display: grid;
  place-items: center;
  border-radius: var(--yb-radius-md);
  font-size: 18px;
  font-weight: var(--yb-fw-bold);
  font-family: var(--yb-font);
}
.pl-card-name {
  font-size: var(--yb-fs-lg);
  font-weight: var(--yb-fw-medium);
  color: var(--yb-text);
  line-height: 1.3;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pl-card-id {
  font-size: var(--yb-fs-xs);
  color: var(--yb-text-dim);
  line-height: 1.2;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pl-empty {
  flex: 1;
  display: grid;
  place-items: center;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-lg);
}
</style>
