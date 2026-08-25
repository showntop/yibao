<script setup lang="ts">
import { ref, computed, watch, watchEffect, nextTick, onMounted, onUnmounted } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow, PhysicalPosition } from "@tauri-apps/api/window";
import Avatar from "../../components/pet/Avatar.vue";
import InputBar from "../../components/common/InputBar.vue";
import QuickPanel from "../../components/pet/QuickPanel.vue";
import SpeechBubble from "../../components/pet/SpeechBubble.vue";
import PermissionsBanner from "../../components/pet/PermissionsBanner.vue";
import SetupWizard from "../../components/pet/SetupWizard.vue";
import PluginLauncher from "../../components/pet/PluginLauncher.vue";
import BubbleFlow from "../../components/pet/BubbleFlow.vue";
import PendingConfirmCard from "../../components/pet/PendingConfirmCard.vue";
import {
  onBrainEvent,
  onBrainStatus,
  onBrainPermissions,
  onRunDone,
  onPanelClosed,
  onSettings,
  getSettingsOnce,
  getSetupConfig,
  expandChat,
  setPetExpanded,
  hideInvokeBar,
  ensurePetConversation as ensurePetConversationCmd,
  getConversationMessages,
  listPlugins,
  openHomeWindow,
  emitRecapOpen,
  runInput,
  invokeContext,
  voiceStart,
  interrupt,
  panelAction,
  onInvokeAction,
  onSnipCaptured,
  visionQuery,
  type BrainPermissions,
} from "../../lib/brain";
import { formatContextPrefix, type InputContext } from "../../lib/at-mention";
import {
  openPanel,
  setInteractiveFull,
  setMainSize,
  setHotRects,
} from "../../lib/window";
import { SUGGESTIONS } from "../../lib/suggestions";
import { matchExplicitOpen } from "../../lib/explicit-intent";
import { decideSurface } from "../../lib/surface/surface-policy";
import { deactivateAll, petFormOf, surfaceCount } from "../../lib/surface/pet-surface";
import { procLabel, procSkip, procResultSuffix } from "../../lib/proc";
import { squashSpaces, truncate } from "../../lib/text";
import { sessionStore, clearLegacySessionKeys } from "../../state/store";
import { usePetState } from "../../composables/usePetState";
import { usePetApproval } from "../../composables/usePetApproval";
import { usePetBubbles, type BubbleMsg } from "../../composables/usePetBubbles";
import { usePetSpeech } from "../../composables/usePetSpeech";
import { usePetEvents } from "../../composables/usePetEvents";
import YbIcon from "../../components/common/YbIcon.vue";
import { inputMenuOpen, addMenuOpen } from "../../lib/input-menu";

/** 小窗固定会话 id（方案 A）：永远用同一个会话，不镜像大窗活跃会话。
 *  run 带它使消息归属、重启可恢复；固定性从架构上消灭串台（大窗切会话不影响本窗）。 */
const petConvId = ref("");
const brainDown = ref(false); // 大脑掉线/重启中（守护在恢复）
const perms = ref<BrainPermissions | null>(null); // macOS 权限状态（null=未收到）
const expanded = ref(false);
// 桌宠状态机（Avatar 展示态/发呆/闪现）独立成 composable
const {
  state,
  petState,
  attentionNeeded,
  statusText,
  busy,
  observing,
  syncObserving,
  flashState,
  flashValence,
  onPetHover,
  dispose: disposePetState,
} = usePetState(expanded);
// 气泡流域（消息流 + 流式下标 + 滚动跟随 + 过程行/表面锚点索引）独立成 composable
const {
  bubbles,
  streamingIdx,
  procIdx,
  surfaceAnchor,
  pushWarn,
  openBubbleSticky,
  bubblesRef,
  scrollBubbles,
  captureBubbleScroll,
  restoreBubbleScroll,
  resetScroll,
} = usePetBubbles({ expanded, expand });
// 气泡流滚动容器桥接：BubbleFlow 子组件 expose 的元素 → usePetBubbles.bubblesRef
//（v-if 切插件视图时容器重建，watchEffect 持续同步）
const bubbleFlowRef = ref<InstanceType<typeof BubbleFlow> | null>(null);
watchEffect(() => {
  bubblesRef.value = bubbleFlowRef.value?.el ?? null;
});
/** 快捷面板（单窗三态 quick 内容层）：hover 团子显示 3 圆 + 输入条，同窗渲染零 resize */
const quick = ref(false);
// 收起态回复气泡域（内容/流式/显隐 + 自动收起定时器）独立成 composable
const {
  speech,
  speechStreaming,
  speechVisible,
  showSpeechBubble,
  scheduleAutoHide: speechScheduleHide,
  cancelAutoHide: speechCancelHide,
  dispose: disposeSpeech,
} = usePetSpeech({ quick, syncHotRects });
/** 团子窗口内 top（CSS 像素）：默认 16（贴窗口顶，下方留给输入条+插件）；
 *  窗口贴近屏幕顶时继续上移（macOS 不允许窗口出屏），让团子贴菜单栏下缘。
 *  团子屏幕 y = max(窗口y + 16, 24) → petY = max(16, 24 - 窗口y)。 */
const petY = ref(16);
let scaleCached = 1; // Retina 缩放（窗口创建后不变，onMoved 计算用）
let unlistenMoved: (() => void) | null = null;
const panelOpen = ref(false); // 面板浮窗当前打开状态
// explicit run 标记：插件视图点击 / 窄规则命中两个来源共用；run_done、final_reply、interrupted 或发起失败时清理。
// 刻意不设过期时间：文本路径要等两轮 LLM 往返，任何墙钟窗口都会在慢模型上静默失效。
// run_done 已带 conversation_id（并发对话），监听处按归属过滤——别窗 run 收尾不再提前清掉
// 本窗标记；无归属的旧帧仍清（方向是「该开的窗没开」，退化成一条可点行，绝不会反向违反
// 「模型不得自动开窗」）。
let requestedPlugin = "";
function markExplicit(pluginId: string): void {
  requestedPlugin = pluginId;
}
function clearExplicit(): void {
  requestedPlugin = "";
}

// ---- 首启设置向导（缺 LLM key 时 Rust 发 setup-config-needed，大脑未启动）----
const setupNeeded = ref(false);
const setupCfg = ref({ model: "glm-4.6", baseUrl: "", voice: "zh-CN-XiaoxiaoNeural" });
async function onSetupNeeded() {
  setupNeeded.value = true;
  if (!expanded.value) void expand();
  try {
    const cfg = await getSetupConfig();
    setupCfg.value = { model: cfg.model, baseUrl: cfg.base_url, voice: cfg.voice };
  } catch { /* 用默认值 */ }
}
function onSetupSaved() {
  setupNeeded.value = false;
  bubbles.value.push({ role: "sys", text: "配置已保存，大脑启动中…" });
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
/** hover 收起延迟（命令/文件菜单打开时鼠标移向菜单项用）：菜单关闭后再收起面板 */
let hoverCloseTimer: ReturnType<typeof setTimeout> | null = null;

const suggestions = SUGGESTIONS;
const missingPerms = computed(() => perms.value !== null && (!perms.value.ax || !perms.value.screen || !perms.value.input));
// 「正在输入」占位：run 受理（think）到首个 chunk 之间气泡流还是空的，用三点呼吸占位；
// 复用 state/streamingIdx 判断——首 chunk 建起 streaming 气泡即让位，终态（idle/error）自动消失
const showTyping = computed(() => state.value === "think" && streamingIdx.value === null);
// showTyping 呼吸占位出现/消失时滚动（bubbles 自身的滚动 watch 在 usePetBubbles 内）
watch(showTyping, () => scrollBubbles(true));

/** 单窗热区上报：idle 只报团子盒（pet），quick 追加面板元素（ui，.wb-zone）。
 *  Rust 据此放行鼠标穿透 + 驱动 enter/leave；窗口相对坐标，拖动自动跟随。
 *  ⚠️ 菜单（.at-menu）展开的区域也必须上报——Rust 只放行上报矩形，菜单区域漏报会被
 *  set_ignore_cursor_events 穿透到桌面，点击/滚轮根本进不了 webview（hover 是轮询
 *  间隙的假象）。菜单打开时按首帧 rect 上报（过滤后菜单变矮只是热区略大于实际，无害）。 */
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
      // 输入框命令/文件菜单热区：向下/向上展开超出 .wb-zone 的部分也要放行
      if (inputMenuOpen.value) {
        document.querySelectorAll<HTMLElement>(".at-menu").forEach((n) => {
          const r = n.getBoundingClientRect();
          if (r.width > 0 && r.height > 0) {
            rects.push({ x: r.left, y: r.top, w: r.width, h: r.height, kind: "ui" });
          }
        });
      }
      // 加号菜单（附件/引用）：桌宠下改成向下展开——超 .wb-input 几何也需放行
      if (addMenuOpen.value) {
        document.querySelectorAll<HTMLElement>(".add-menu").forEach((n) => {
          const r = n.getBoundingClientRect();
          if (r.width > 0 && r.height > 0) {
            rects.push({ x: r.left, y: r.top, w: r.width, h: r.height, kind: "ui" });
          }
        });
      }
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

// 菜单开/关都要重新上报热区（开=菜单区域放行；关=撤掉菜单热区恢复穿透）
watch([inputMenuOpen, addMenuOpen], () => syncHotRects());

/** 顶部边界自适应：macOS 不允许窗口顶部出屏（诊断确认负 y setPosition 被忽略，
 *  窗口顶最低停在菜单栏下缘）。故窗口贴近顶部时「团子锚点在窗口内上移」：
 *     f = clamp(窗口y - 24, 0, 100)   （窗口内正常位 = 100）
 *     团子屏幕 y = max(窗口y + f, 24)  →  团子窗口内 y = 屏幕y - 窗口y
 *  效果：窗口 y ∈ [0,24] 时团子恒贴菜单栏下缘（屏幕 y=24）；拖离顶部后线性过渡
 *  回窗口内 100。快捷态顺序：团子 → 输入条 → 插件。
 *  热区随 petY 变化实时上报。 */
function onWindowMoved(p: { x: number; y: number }) {
  const winY = p.y / (scaleCached || 1);
  // 默认贴窗口顶（petY=16），窗口贴屏顶时锁团子屏幕 y=24（菜单栏下缘）
  const target = Math.max(16, 24 - winY);
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
    resetScroll(); // 收起态新回复迁入，打开应对准最新
  }
  speechVisible.value = false;
  speech.value = null;
  speechStreaming.value = false;
  speechCancelHide();
  restoreBubbleScroll();
  // 先记录收起态位置（collapse 还原用），再交给 Rust 展开（定位 + clamp + 缩放一步完成）
  try {
    const p = await getCurrentWindow().outerPosition();
    idlePos = { x: p.x, y: p.y };
  } catch { /* 忽略 */ }
  await expandChat().catch(() => {});
  restoreBubbleScroll(); // 窗口 320×300 → 360×520 后 clientHeight 变了，贴底要再对准一次
}

// 待批确认域（队列 + 快批 + 回执占位）独立成 composable
const {
  pendingConfirms,
  pending,
  pendingCanRemember,
  rememberPending,
  approvalGuard,
  decide,
  decideAllPending,
  listen: listenApprovals,
  dispose: disposeApproval,
} = usePetApproval({
  petConvId,
  state,
  attentionNeeded,
  expanded,
  pushWarn,
  openBubbleSticky,
  expand,
});

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
  if (v === view.value) return;
  // 切走对话前记录滚动（BubbleFlow 即将卸载、滚动位置归零）；切回时恢复
  if (view.value === "chat") captureBubbleScroll();
  view.value = v;
  if (v === "plugins") void loadPlugins();
  if (!expanded.value) await expand();
  if (v === "chat") restoreBubbleScroll();
}

async function loadPlugins() {
  pluginErr.value = "";
  try {
    // 上限 8 个：插件是精选的，不会多；超出说明该做设置页了
    allPlugins.value = await listPlugins();
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

/** morning_recap 晨报气泡点击 → 打开主屏（回顾视图已退役，晨报正文自足在气泡里；
 *  emitRecapOpen 的事件通道留着，暂无监听者——将来回顾页回归时即插即用）。 */
function onRecapClick(day?: string) {
  if (!day) return;
  void openHomeWindow().catch(() => {});
  void emitRecapOpen(day);
}

// ---- 全局唤起（⌘⇧Y 反射键 / ⌘⇧U 划词唤起，Rust 抓完选中文字发事件）----
const inputBarRef = ref<{ focus: () => void } | null>(null);
const selectionCtx = ref<string | null>(null); // 划词上下文（chip 展示，发送时拼进给大脑的消息）
const ctxPreview = computed(() => {
  const t = squashSpaces(selectionCtx.value ?? "");
  return truncate(t, 42);
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
  void setPetExpanded(v).catch(() => {});
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
  void hideInvokeBar().catch(() => {}); // 兜底（条本身已自隐）
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

// 事件流处理（R-08）：onEvent/onStatus/onPerms 已迁 composables/usePetEvents.ts——
// 闭包依赖（refs/动作）经 ctx 透传，本文件只留装配
const {
  onEvent,
  onStatus,
  onPerms,
} = usePetEvents({
  petConvId, state, bubbles, streamingIdx, procIdx, surfaceAnchor,
  expanded, speech, speechStreaming, attentionNeeded, brainDown, perms,
  missingPerms, requestedPlugin,
  procSkip, procLabel, procResultSuffix, flashValence, flashState,
  showSpeechBubble, speechScheduleHide, clearExplicit, deactivateAll,
  surfaceCount, petFormOf, decideSurface, openPanelWindow, pushWarn,
  openBubbleSticky, expand,
});



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

function onMic() {
  // 不乐观置 listen：等大脑 listening 事件确认（语音栈不可用时大脑会回 error，别自欺卡死）
  void ensurePetConversation().then(() => voiceStart("pet", false, petConvId.value)).catch((err) => {
    pushWarn("语音启动失败：" + String(err));
  });
}

/** /命令 local 动作：截图/打开插件/新建会话/帮助（InputBar 上抛） */
function onSlashLocal(id: string) {
  if (id === "snip") {
    void invoke("start_snip").catch(() => pushWarn("截图启动失败"));
  } else if (id === "plugins") {
    void expandTo("plugins");
  } else if (id === "new-conversation") {
    // 小窗固定会话（方案 A）：新建会话引导去主窗
    pushWarn("小窗为固定会话，请在主窗新建会话");
  } else if (id === "help") {
    void submit("介绍一下你的能力：能调用哪些插件、支持哪些斜杠命令、如何高效使用。");
  }
}

/** /命令 插件动作：api.toml command=true 的直调方法（如 toolbox.json_format） */
function onSlashPlugin(p: { pluginId: string; method: string }) {
  void panelAction(p.method, {}).catch(() => pushWarn("插件命令执行失败"));
}

function onMicContinuous() {
  // 长按团子 = 连续对话：答完接着听，说「退出」或点团子结束（会话提示由大脑 notice 落气泡）
  void ensurePetConversation().then(() => voiceStart("pet", true, petConvId.value)).catch((err) => {
    pushWarn("语音启动失败：" + String(err));
  });
}

function onInterrupt() {
  // 不看 busy：TTS 播完字后打断若晚到，state 可能已和音频脱节；多发一次 interrupt 无害。
  // 定向打断（并发对话 spec §E）：只停小窗固定会话槽，不掐大窗/面板的在跑 run。
  void interrupt(petConvId.value || undefined).catch((err) => {
    pushWarn("打断失败：" + String(err));
  });
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
  const meta = await ensurePetConversationCmd().catch(() => null);
  if (meta?.id) petConvId.value = meta.id;
}

/** 从 Rust 权威重拉小窗固定会话消息渲染（启动恢复 / 跨窗刷新）。
 *  流式进行中不刷新——重建气泡会打断正在渲染的回复。 */
async function reloadMessages(): Promise<void> {
  if (!petConvId.value) return;
  if (streamingIdx.value !== null) return;
  const rows = await getConversationMessages(petConvId.value, 500).catch(() => null);
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
  resetScroll();
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
  // run_done 带 conversation_id（并发对话 spec §E）：只清本会话的 explicit 标记，
  // 别窗 run 收尾不再波及本窗（无归属的旧帧照常清，兼容旧 sidecar）。
  unlistenRunDone = await onRunDone((v) => {
    const cid = (v as { conversation_id?: string } | null)?.conversation_id;
    if (cid && petConvId.value && cid !== petConvId.value) return;
    clearExplicit();
  });
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
  unlistenApprovals = listenApprovals();
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
    if (hoverCloseTimer) clearTimeout(hoverCloseTimer);
    if (speechVisible.value) return; // 回复气泡显示中不弹快捷面板（区域重叠）
    quick.value = true;
    syncHotRects();
  });
  unlistenCursorLeave = await listen("pet-cursor-leave", () => {
    setPetHover(false);
    if (hoverCloseTimer) clearTimeout(hoverCloseTimer);
    if (inputMenuOpen.value) {
      // 输入框命令/文件菜单打开：鼠标移向菜单项（离开团子热区）时不能立即收起面板，
      // 否则面板连同菜单一起消失、点不中；菜单关闭后再延迟收起
      hoverCloseTimer = setTimeout(() => {
        if (!inputMenuOpen.value) {
          quick.value = false;
          syncHotRects();
        }
      }, 400);
      return;
    }
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
    const cfg = await getSetupConfig();
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
  if (hoverCloseTimer !== null) clearTimeout(hoverCloseTimer);
  disposeSpeech();
  window.removeEventListener("keydown", onKeydown);
  if (clickTimer !== null) clearTimeout(clickTimer);
  disposePetState();
  disposeApproval();
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
      <PluginLauncher v-if="view === 'plugins'" :plugins="plugins" :err="pluginErr" @launch="launchPlugin" />

      <BubbleFlow
        v-else
        ref="bubbleFlowRef"
        :bubbles="bubbles"
        :streaming-idx="streamingIdx"
        :show-typing="showTyping"
        :pet-state="petState"
        :suggestions="suggestions"
        @submit="submit"
        @recap-click="onRecapClick"
        @surface-open="openPanelWindow()"
      />

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
        <InputBar v-else-if="!pending" ref="inputBarRef" :busy="busy" :listening="state === 'listen'" @submit="submit" @mic="onMic" @interrupt="onInterrupt" @slash-local="onSlashLocal" @slash-plugin="onSlashPlugin" />
        <PendingConfirmCard
          v-else
          :pending="pending"
          :count="pendingConfirms.length"
          :can-remember="pendingCanRemember"
          v-model:remember="rememberPending"
          @decide="decide"
          @decide-all="decideAllPending"
          @open-home="openHome"
        />
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
  top: 16px;
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
  transition: opacity var(--yb-dur-fast) var(--yb-ease-inout), transform var(--yb-dur) var(--yb-ease-inout);
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
/* 审批结果反馈条（已允许/已拒绝）：确认卡本身在 components/pet/PendingConfirmCard */
.approval-guard {
  display: flex;
  align-items: center;
  gap: var(--yb-space-2);
  padding: 9px 10px;
  margin: 0 2px 10px;
  min-height: 34px;
  box-sizing: border-box;
  justify-content: center;
  color: var(--yb-text-dim);
  border: 1px solid var(--yb-border-base);
  border-radius: var(--yb-radius-md);
  background: var(--yb-surface-solid);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.7);
  pointer-events: auto;
  user-select: none;
}
.approval-guard .yb-icon {
  color: var(--yb-intent-ok);
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
</style>
