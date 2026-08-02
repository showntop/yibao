<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { WebviewWindow } from "@tauri-apps/api/webviewWindow";
import Avatar from "./components/Avatar.vue";
import SpeechBubble from "./components/SpeechBubble.vue";
import InputBar from "./components/InputBar.vue";
import Bubble from "./components/Bubble.vue";
import PermissionsBanner from "./components/PermissionsBanner.vue";
import SetupWizard from "./components/SetupWizard.vue";
import {
  onBrainEvent,
  onBrainStatus,
  onBrainPermissions,
  onPanelClosed,
  onPendingConfirms,
  onSettings,
  getSettingsOnce,
  openHomeWindow,
  runInput,
  invokeContext,
  sendConfirmBatch,
  voiceStart,
  interrupt,
  panelAction,
  type BrainEvent,
  type BrainStatusMsg,
  type BrainPermissions,
  type PendingConfirm,
  type SettingsValues,
  canRememberSkill,
} from "./lib/brain";
import { resetWindowSize, openPanel, setInteractiveFull, setBubbleOn } from "./lib/window";
import { SUGGESTIONS } from "./lib/suggestions";
import { procLabel, procSkip, procResultSuffix } from "./lib/proc";
import YbIcon from "./components/YbIcon.vue";

type AvatarState = "idle" | "listen" | "think" | "work" | "say" | "success" | "error" | "notify" | "drowsy";
// pstate：过程行状态（图标随态渲染，文案不再拼 emoji）；halted：被打断；icon：行首语义图标
type BubbleMsg = {
  role: "user" | "ai" | "sys";
  text: string;
  pstate?: "run" | "ok" | "fail";
  halted?: boolean;
  icon?: "clock" | "alert" | "doc";
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
const pendingConfirms = ref<PendingConfirm[]>([]);
const pending = computed(() => pendingConfirms.value[0] ?? null);
const pendingCanRemember = computed(() => canRememberSkill(pending.value?.skill ?? ""));
const rememberPending = ref(false);
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
const panelOpen = ref(false); // 面板协作会话进行中（关联气泡只插一次，panel 刷新不重复插）
// 过程展示：action.id → 过程行（sys 淡色小字）在 bubbles 里的下标，结果回来原地更新
const procIdx = new Map<string, number>();

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

// ---- 说话态气泡（B）：流式 chunk 拼到 bubbleText（天然打字机）；说话时窗口撑出，说完缩回 ----
const bubbleOn = ref(false);
const bubbleText = ref("");
const bubbleBusy = ref(false); // 走马灯滚动中（自动收起暂停，滚完再收）
const bubbleSticky = ref(false); // 常驻气泡（有事找你）：不自动收起，点团子展开才走
let bubbleTimer: ReturnType<typeof setTimeout> | null = null;
let pendingBubbleClose = false; // 滚动中收到收起请求：挂起，等 settled

/** 打开气泡（仅收起态）：置位即显（窗口固定不缩放，气泡在团子左侧腾出的一条里）。 */
function openBubble() {
  if (expanded.value || bubbleOn.value) return;
  bubbleOn.value = true;
}
/** 常驻轻提示气泡（reminder 等「有事找你」）：文本一步到位，不排任何自动收起。 */
function openBubbleSticky(text: string) {
  if (expanded.value) return;
  bubbleSticky.value = true;
  bubbleOn.value = true;
  bubbleText.value = text;
}
/** 立刻收起气泡（清计时）。 */
function closeBubbleNow() {
  if (bubbleTimer) { clearTimeout(bubbleTimer); bubbleTimer = null; }
  pendingBubbleClose = false;
  bubbleBusy.value = false;
  bubbleSticky.value = false;
  if (!bubbleOn.value) return;
  bubbleOn.value = false;
  bubbleText.value = "";
}
/** 延迟收起（读完再看一会儿）；常驻气泡不收；走马灯滚动中先挂起，滚完（settled）再留 1.6s 收尾。 */
function scheduleBubbleClose(ms: number) {
  if (bubbleSticky.value) return;
  if (bubbleTimer) clearTimeout(bubbleTimer);
  if (bubbleBusy.value) {
    pendingBubbleClose = true;
    return;
  }
  bubbleTimer = setTimeout(() => { closeBubbleNow(); }, ms);
}
/** 走马灯起跑：滚完前别收（清掉已排的收起计时）。 */
function onBubbleBusy() {
  bubbleBusy.value = true;
  if (bubbleTimer) { clearTimeout(bubbleTimer); bubbleTimer = null; }
}
/** 走马灯滚到底：若有挂起的收起请求，留 1.6s 读完尾巴再收。 */
function onBubbleSettled() {
  bubbleBusy.value = false;
  if (pendingBubbleClose) {
    pendingBubbleClose = false;
    scheduleBubbleClose(1600);
  }
}
let unlisten: (() => void) | null = null;
let unlistenStatus: (() => void) | null = null;
let unlistenPerms: (() => void) | null = null;
let unlistenPanelClosed: (() => void) | null = null;
let unlistenSetup: (() => void) | null = null;
let unlistenSetupErr: (() => void) | null = null;
let unlistenSetupCfg: (() => void) | null = null;
let unlistenInvoke: (() => void) | null = null;
let unlistenInvokeSel: (() => void) | null = null;
let unlistenApprovals: (() => void) | null = null;
let unlistenSettings: (() => void) | null = null;

const statusText = computed(
  () => ({
    idle: "待命中", listen: "聆听中", think: "思考中…", work: "操作中…", say: "说话中…",
    success: "完成", error: "出错了", notify: "有事找你", drowsy: "发呆中",
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

async function expand() {
  // 固定窗口方案：不缩放。先收气泡（仅切内容），再切聊天视图
  closeBubbleNow();
  attentionNeeded.value = false; // 用户来看了 = 事已知，notify 态消
  expanded.value = true;
}
async function collapse() {
  expanded.value = false;
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
  if (state.value !== "idle") return;
  drowsy.value = false;
  armDrowsy();
}

// ---- 插件启动器（双击团子）----
type PetView = "chat" | "plugins";
interface PluginInfo { id: string; name: string }
const view = ref<PetView>("chat");
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
    plugins.value = (await invoke<PluginInfo[]>("list_plugins")).slice(0, 8);
  } catch (err) {
    plugins.value = [];
    pluginErr.value = String(err);
  }
}

/** 点插件 → 调它的 list 直调（约定的主面板入口）；panel 事件回来会自动 openPanel + 收起对话。 */
async function launchPlugin(p: PluginInfo) {
  pluginErr.value = "";
  try {
    await panelAction(`${p.id}.list`, {});
  } catch (err) {
    pluginErr.value = "启动失败：" + String(err);
  }
}

/** header「扩充」钮 → 打开大窗（完整 APP 主界面，与小窗互斥，宠物窗保持纯粹）。 */
function openHome() {
  void openHomeWindow().catch(() => {});
}

// ---- 全局唤起（⌘⇧Y 反射键 / ⌘⇧U 划词唤起，Rust 抓完选中文字发事件）----
const inputBarRef = ref<{ focus: () => void } | null>(null);
const selectionCtx = ref<string | null>(null); // 划词上下文（chip 展示，发送时拼进给大脑的消息）
const ctxPreview = computed(() => {
  const t = (selectionCtx.value ?? "").replace(/\s+/g, " ").trim();
  return t.length > 42 ? t.slice(0, 42) + "…" : t;
});

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
  await expand();
  const t = text?.trim();
  // 上下文截断 4000 字：够一整页文档，又不至于一条消息烧穿上下文
  if (t) selectionCtx.value = t.length > 4000 ? t.slice(0, 4000) : t;
  void nextTick(() => inputBarRef.value?.focus());
}

function onEvent(e: BrainEvent) {
  // 会话分流：面板场景的对话事件只归面板窗；panel 事件例外（管开窗 + 关联气泡，两窗都收）
  if (e.surface && e.surface !== "pet" && e.kind !== "panel") return;
  switch (e.kind) {
    case "action_proposed":
      state.value = "work";
      // 过程行：技能短标签 + pstate 驱动图标（use_plugin 跳过——成功有 notice，不重复）
      if (e.action?.id && !procSkip(e.action)) {
        procIdx.set(e.action.id, bubbles.value.length);
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
      break;
    }
    case "final_reply_chunk": {
      // 流式增量：拼到当前 streaming bubble（首片时新建）
      if (streamingIdx.value === null) {
        bubbles.value.push({ role: "ai", text: e.text ?? "" });
        streamingIdx.value = bubbles.value.length - 1;
      } else {
        bubbles.value[streamingIdx.value].text += e.text ?? "";
      }
      // 收起态：撑出气泡，镜像流式文本（打字机效果）；新回复接管气泡，常驻提示让位
      if (!expanded.value) {
        bubbleSticky.value = false;
        openBubble();
        bubbleText.value = bubbles.value[streamingIdx.value].text;
      }
      break;
    }
    case "final_reply": {
      // 以完整文本为准收尾（兜底 chunk 丢失）；语音中保持 say 等 speaking_done
      const full = e.text ?? "";
      if (streamingIdx.value !== null) {
        bubbles.value[streamingIdx.value].text = full;
        streamingIdx.value = null;
      } else {
        bubbles.value.push({ role: "ai", text: full });
      }
      if (state.value !== "say") state.value = "idle";
      // 收起态：兜底显示完整文本；若无语音（非 say），读完即收
      if (!expanded.value) {
        bubbleSticky.value = false;
        openBubble();
        bubbleText.value = full;
        if (state.value !== "say") scheduleBubbleClose(2200);
      }
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
      closeBubbleNow();
      break;
    case "speaking_done":
      state.value = "idle";
      if (bubbleOn.value && !expanded.value) scheduleBubbleClose(1600); // 说完，留 1.6s 读完再收
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
      const text = e.text ?? "到点了";
      bubbles.value.push({ role: "ai", text, icon: "clock" });
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
      closeBubbleNow();
      break;
    case "listening":
      state.value = "listen";
      break;
    case "listening_done":
      // 空识别（超时/没说话）：回 idle 并提示——不能进 think，run_done 不复位状态，会永远卡「思考中」
      if (e.text) {
        state.value = "think";
        bubbles.value.push({ role: "user", text: e.text });
      } else {
        state.value = "idle";
        bubbles.value.push({ role: "ai", text: "没听清，再试一次？" });
      }
      break;
    case "speaking":
      state.value = "say";
      break;
    case "panel": {
      // 面板 = 独立浮窗（工作模式）：交给面板窗，宠物窗收回球形态；
      // 主对话框只留一条「派生」关联气泡，协作过程不镜像（会话分流）
      const title = e.payload?.title || e.payload?.panel || "插件面板";
      if (!panelOpen.value) {
        panelOpen.value = true;
        bubbles.value.push({ role: "ai", text: `⇢ 正在和「${title}」协作` });
      }
      void openPanel();
      if (expanded.value) void collapse();
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

async function submit(text: string) {
  bubbles.value.push({ role: "user", text });
  state.value = "think";
  // 划词上下文：气泡只显示用户打的字，给大脑的消息自包含拼好（大脑看不到前台选中）
  let msg = text;
  if (selectionCtx.value) {
    msg = `用户在前台应用选中了一段文字：\n「${selectionCtx.value}」\n\n用户的指示：${text}`;
    bubbles.value.push({ role: "sys", text: `已附带选中文字 ${selectionCtx.value.length} 字`, icon: "doc" });
    selectionCtx.value = null;
  }
  try {
    await runInput(msg);
  } catch (err) {
    pushWarn("发送失败：" + String(err));
    state.value = "idle";
  }
}

async function decide(approved: boolean, remember = false) {
  if (!pending.value) return;
  const { id } = pending.value;
  state.value = "think";
  try {
    await sendConfirmBatch([{ id, approved, remember: pendingCanRemember.value && remember }]);
    rememberPending.value = false;
  } catch (err) {
    pushWarn("确认失败：" + String(err));
  }
}

async function decideAllPending(approved: boolean) {
  if (pendingConfirms.value.length < 2) return;
  const items = pendingConfirms.value.map(({ id }) => ({ id, approved, remember: false }));
  state.value = "think";
  try {
    await sendConfirmBatch(items);
  } catch (err) {
    pushWarn("批量确认失败：" + String(err));
    state.value = "idle";
  }
}

function onMic() {
  // 不乐观置 listen：等大脑 listening 事件确认（语音栈不可用时大脑会回 error，别自欺卡死）
  void voiceStart().catch((err) => {
    pushWarn("语音启动失败：" + String(err));
  });
}

function onMicContinuous() {
  // 长按团子 = 连续对话：答完接着听，说「退出」或点团子结束（会话提示由大脑 notice 落气泡）
  void voiceStart(undefined, true).catch((err) => {
    pushWarn("语音启动失败：" + String(err));
  });
}

function onInterrupt() {
  if (!busy.value) return;
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

function onKeydown(e: KeyboardEvent) {
  if (e.key === "Escape" && expanded.value) void collapse();
}

// 展开时整窗可交互；说话气泡只把「气泡带」加成第二热区（点气泡=展开），其余透明区照常穿透到桌面
watch(expanded, (v) => {
  void setInteractiveFull(v);
});
watch(bubbleOn, (v) => {
  void setBubbleOn(v);
});

onMounted(async () => {
  await resetWindowSize();
  void setInteractiveFull(false);
  void setBubbleOn(false);
  unlisten = await onBrainEvent(onEvent);
  unlistenStatus = await onBrainStatus(onStatus);
  unlistenPerms = await onBrainPermissions(onPerms);
  unlistenPanelClosed = await onPanelClosed(() => {
    if (!panelOpen.value) return;
    panelOpen.value = false;
    bubbles.value.push({ role: "ai", text: "⇠ 协作结束" });
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
  // 全局唤起：⌘⇧Y 反射键（pet-show 确保展开）/ ⌘⇧U 划词唤起（展开 + 上下文 chip）
  unlistenInvoke = await listen("pet-show", () => void onPetShow());
  unlistenInvokeSel = await listen<{ text: string | null }>("pet-invoke-selection", (e) =>
    void onPetInvokeSelection(e.payload.text),
  );
  // 主动拉一次配置：首启引导若秒过（venv/模型已在），setup-config-needed 可能先于挂载发出而丢——靠拉取兜底
  try {
    const cfg = await invoke<{ has_key: boolean }>("get_setup_config");
    if (!cfg.has_key) void onSetupNeeded();
  } catch { /* 忽略，事件路径仍兜底 */ }
  window.addEventListener("keydown", onKeydown);
});
onUnmounted(() => {
  unlisten?.();
  unlistenStatus?.();
  unlistenPerms?.();
  unlistenPanelClosed?.();
  unlistenSetup?.();
  unlistenSetupErr?.();
  unlistenSetupCfg?.();
  unlistenInvoke?.();
  unlistenInvokeSel?.();
  unlistenApprovals?.();
  unlistenSettings?.();
  window.removeEventListener("keydown", onKeydown);
  if (clickTimer !== null) clearTimeout(clickTimer);
  if (bubbleTimer !== null) clearTimeout(bubbleTimer);
  if (valenceTimer !== null) clearTimeout(valenceTimer);
  if (drowsyTimer !== null) clearTimeout(drowsyTimer);
});
</script>

<template>
  <div class="shell" :class="{ exp: expanded }">
    <!-- 常态：宠物球 + 状态文字 -->
    <template v-if="!expanded">
      <div class="speech-slot" v-if="bubbleOn">
        <SpeechBubble
          :text="bubbleText"
          :streaming="streamingIdx !== null"
          @expand="expand"
          @busy="onBubbleBusy"
          @settled="onBubbleSettled"
        />
      </div>
      <div class="pet-wrap" @pointerenter="onPetHover">
        <Avatar class="pet" :state="petState" :size="88" :observing="observing" @click="onPetClick" @longpress="onMicContinuous" />
      </div>
    </template>

    <!-- 对话：header（头像+名称+状态+收起，一体化贴边）/ 内容区（权限引导/气泡流/输入条） -->
    <template v-else>
      <header class="chat-header flip" data-tauri-drag-region>
        <Avatar :state="petState" :size="38" :observing="observing" @click="collapse" />
        <div class="meta" data-tauri-drag-region>
          <span class="name">译宝</span>
          <span class="status" :class="petState"><i class="dot" />{{ statusText }}</span>
        </div>
        <button class="collapse-btn" title="收起" @click="collapse">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
            <line x1="6" y1="12" x2="18" y2="12" />
          </svg>
        </button>
        <button class="collapse-btn expand-btn" title="打开大窗（完整界面）" @click="openHome">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M15 3h6v6" />
            <path d="M9 21H3v-6" />
            <path d="M21 3l-7 7" />
            <path d="M3 21l7-7" />
          </svg>
        </button>
      </header>

      <div class="chat-body">
      <SetupWizard v-if="setupNeeded" :model="setupCfg.model" :base-url="setupCfg.baseUrl" :voice="setupCfg.voice" @saved="onSetupSaved" />

      <template v-if="!setupNeeded">
      <PermissionsBanner v-if="missingPerms && perms" :perms="perms" />

      <!-- 插件启动器视图（双击团子进来）：列出插件，点击直达它的主面板 -->
      <div v-if="view === 'plugins'" class="bubbles">
        <div class="pl-head">
          <span class="pl-title">插件</span>
          <button class="pl-back" @click="view = 'chat'">‹ 对话</button>
        </div>
        <div v-if="pluginErr" class="pl-err"><YbIcon name="alert" :size="14" />{{ pluginErr }}</div>
        <button v-for="p in plugins" :key="p.id" class="pl-row" @click="launchPlugin(p)">
          <span class="pl-name">{{ p.name }}</span>
          <span class="pl-id">{{ p.id }}</span>
        </button>
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
        <Bubble
          v-for="(b, i) in bubbles"
          :key="i"
          :role="b.role"
          :text="b.text"
          :streaming="i === streamingIdx"
          :pstate="b.pstate"
          :halted="b.halted"
          :icon="b.icon"
        />
        <Bubble v-if="showTyping" role="ai" text="" typing />
      </div>

      <div v-if="view === 'chat'" class="input-slot">
        <!-- 划词上下文 chip：⌘⇧U 抓到选中文字后等待指示，可 × 掉；发送后自消 -->
        <div v-if="selectionCtx" class="ctx-chip">
          <YbIcon class="ctx-ic" name="doc" :size="13" />
          <span class="ctx-text" :title="selectionCtx">{{ ctxPreview }}</span>
          <button class="ctx-x" title="去掉上下文" @click="selectionCtx = null">×</button>
        </div>
        <InputBar v-if="!pending" ref="inputBarRef" :busy="busy" :listening="state === 'listen'" @submit="submit" @mic="onMic" @interrupt="onInterrupt" />
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
            本会话不再询问
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
  background:
    linear-gradient(180deg, rgba(var(--yb-c-sky-rgb), 0.09), rgba(var(--yb-c-sky-rgb), 0) 128px),
    var(--yb-shell-bg);
  -webkit-backdrop-filter: var(--yb-blur);
  backdrop-filter: var(--yb-blur);
  border: 1px solid var(--yb-glass-border);
  border-radius: var(--yb-radius-xl);
  box-shadow: var(--yb-shadow);
}
/* 内容区：header 贴边一体化，其余内容在这里呼吸 */
.chat-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: var(--yb-space-3);
  padding: var(--yb-space-3);
}
/* 常态：团子锚到右沿（right:34）——窗口向左撑开时团子原地不动；132 窗内 ≡ 居中 */
.pet-wrap {
  position: absolute;
  right: 22px;
  top: 12px;
  z-index: 3;
}
.pet-wrap .pet {
  position: static;
}
/* 说话态气泡槽：贴着团子左沿（右锚定，tail 指着团子），向左占满腾出的空间；
   与团子同一高度带（top/height 对齐 pet-wrap），气泡在带内垂直居中——tail 指着团子脸；
   justify-end：气泡右沿永远钉在团子旁，短气泡也不漂走 */
.speech-slot {
  position: absolute;
  left: 8px;
  right: 116px;
  top: 12px;
  height: 88px;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  z-index: 3;
}
/* 展开内容渐入：配合窗口补间，不突兀 */
.shell.exp .bubbles,
.shell.exp .input-slot {
  animation: fade-in 0.22s var(--yb-ease) 0.06s both;
}
/* 输入区：chip（划词上下文）贴左坐在输入条上方 */
.input-slot {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.quick-confirm,
.batch-confirm-notice {
  display: flex;
  align-items: center;
  gap: var(--yb-space-2);
  padding: 9px 10px;
  border: 1px solid var(--yb-danger-soft);
  border-radius: var(--yb-radius-md);
  background: var(--yb-surface-solid);
  box-shadow: var(--yb-shadow-sm);
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
.ctx-chip {
  align-self: flex-start;
  max-width: 100%;
}
@keyframes fade-in {
  from {
    opacity: 0;
    transform: translateY(6px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}
/* header：贴边一体化（非浮卡），浅天青底与对话区分开，底部一根 hairline */
.chat-header {
  position: relative; /* 收起钮绝对定位的锚 */
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px var(--yb-space-3) 9px;
  background: linear-gradient(180deg, rgba(var(--yb-c-sky-rgb), 0.14), rgba(var(--yb-c-sky-rgb), 0.08));
  border-bottom: 1px solid var(--yb-surface-border);
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
  gap: 3px;
  line-height: var(--yb-lh-tight);
  /* 拖动把手区（名称/状态文字上按住可拖窗） */
  cursor: default;
  user-select: none;
}
.name {
  font-size: var(--yb-fs-xl);
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
/* 收起：幽灵圆钮 + minus（macOS 最小化语义），hover 才显底；绝对定位钉在 header 最左 */
.collapse-btn {
  position: absolute;
  left: 10px;
  top: 50%;
  transform: translateY(-50%);
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--yb-text-dim);
  cursor: pointer;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.collapse-btn svg {
  width: 14px;
  height: 14px;
}
.collapse-btn:hover {
  background: var(--yb-well);
  color: var(--yb-text);
}
/* 「扩充」钮：与收起钮同款幽灵风，挨着它站（收起钮 left:10，扩充紧随其后）——打开设置大窗 */
.expand-btn {
  left: 40px;
}
.expand-btn svg {
  width: 14px;
  height: 14px;
}
.bubbles {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: var(--yb-space-2);
  overflow-y: auto;
  padding: 4px 2px 0;
  scrollbar-width: thin;
  /* 顶部渐隐：滚出视口的消息柔和淡出，不被硬边「切断」 */
  mask-image: linear-gradient(180deg, transparent, #000 14px);
  -webkit-mask-image: linear-gradient(180deg, transparent, #000 14px);
}
.bubbles::-webkit-scrollbar {
  width: 6px;
}
.bubbles::-webkit-scrollbar-thumb {
  background: var(--yb-surface-border);
  border-radius: var(--yb-radius-pill);
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
  font-weight: 600;
}
.pl-back {
  border: none;
  background: transparent;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-lg);
  cursor: pointer;
  padding: 3px 8px;
  border-radius: var(--yb-radius-sm);
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.pl-back:hover {
  color: var(--yb-accent-deep);
  background: var(--yb-surface-solid);
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
.pl-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--yb-space-2);
  padding: var(--yb-space-3) var(--yb-space-4);
  border: 1px solid var(--yb-surface-border);
  border-radius: var(--yb-radius-md);
  background: var(--yb-surface-solid);
  box-shadow: var(--yb-shadow-soft);
  cursor: pointer;
  font-family: inherit;
  text-align: left;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.pl-row:hover {
  border-color: var(--yb-accent);
  transform: translateY(-1px);
}
.pl-name {
  font-size: var(--yb-fs-lg);
  font-weight: 500;
  color: var(--yb-text);
}
.pl-id {
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-dim);
}
.pl-empty {
  flex: 1;
  display: grid;
  place-items: center;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-lg);
}
</style>
