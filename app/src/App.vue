<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import Avatar from "./components/Avatar.vue";
import SpeechBubble from "./components/SpeechBubble.vue";
import InputBar from "./components/InputBar.vue";
import ConfirmDialog from "./components/ConfirmDialog.vue";
import Bubble from "./components/Bubble.vue";
import PermissionsBanner from "./components/PermissionsBanner.vue";
import SetupWizard from "./components/SetupWizard.vue";
import {
  onBrainEvent,
  onBrainStatus,
  onBrainPermissions,
  onPanelClosed,
  runInput,
  sendConfirm,
  voiceStart,
  interrupt,
  panelAction,
  type BrainEvent,
  type BrainStatusMsg,
  type BrainPermissions,
} from "./lib/brain";
import { resetWindowSize, openPanel, setInteractiveFull } from "./lib/window";

type AvatarState = "idle" | "listen" | "think" | "work" | "say" | "success" | "error";
type BubbleMsg = { role: "user" | "ai" | "sys"; text: string };

const state = ref<AvatarState>("idle");
const bubbles = ref<BubbleMsg[]>([]);
const streamingIdx = ref<number | null>(null); // 正在接收 chunk 的 bubble 下标
const pending = ref<{ id: string; skill: string; desc: string } | null>(null);
const brainDown = ref(false); // 大脑掉线/重启中（守护在恢复）
const perms = ref<BrainPermissions | null>(null); // macOS 权限状态（null=未收到）
const expanded = ref(false);
const panelOpen = ref(false); // 面板协作会话进行中（关联气泡只插一次，panel 刷新不重复插）

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
let bubbleTimer: ReturnType<typeof setTimeout> | null = null;

/** 打开气泡（仅收起态）：撑宽窗口 + 置位。 */
function openBubble() {
  if (expanded.value || bubbleOn.value) return;
  bubbleOn.value = true;
}
/** 立刻收起气泡（清计时）。 */
function closeBubbleNow() {
  if (bubbleTimer) { clearTimeout(bubbleTimer); bubbleTimer = null; }
  if (!bubbleOn.value) return;
  bubbleOn.value = false;
  bubbleText.value = "";
}
/** 延迟收起（读完再看一会儿）。 */
function scheduleBubbleClose(ms: number) {
  if (bubbleTimer) clearTimeout(bubbleTimer);
  bubbleTimer = setTimeout(() => { closeBubbleNow(); }, ms);
}
let unlisten: (() => void) | null = null;
let unlistenStatus: (() => void) | null = null;
let unlistenPerms: (() => void) | null = null;
let unlistenPanelClosed: (() => void) | null = null;
let unlistenSetup: (() => void) | null = null;
let unlistenSetupErr: (() => void) | null = null;
let unlistenSetupCfg: (() => void) | null = null;

const statusText = computed(
  () => ({
    idle: "待命中", listen: "聆听中", think: "思考中…", work: "操作中…", say: "说话中…",
    success: "完成", error: "出错了",
  }[state.value]),
);
// success/error 是短暂 valence（不可打断），不算 busy
const busy = computed(() =>
  state.value === "listen" || state.value === "think" ||
  state.value === "work" || state.value === "say",
);
const suggestions = ["记一条闪念", "看看选题看板", "帮我写点什么"];
const missingPerms = computed(() => perms.value !== null && (!perms.value.ax || !perms.value.screen));
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
  if (bubbleTimer) { clearTimeout(bubbleTimer); bubbleTimer = null; }
  bubbleOn.value = false;
  bubbleText.value = "";
  expanded.value = true;
}
async function collapse() {
  expanded.value = false;
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

function onEvent(e: BrainEvent) {
  // 会话分流：面板场景的对话事件只归面板窗；panel 事件例外（管开窗 + 关联气泡，两窗都收）
  if (e.surface && e.surface !== "pet" && e.kind !== "panel") return;
  switch (e.kind) {
    case "action_proposed":
      state.value = "work";
      break;
    case "confirmation_needed":
      state.value = "idle";
      pending.value = {
        id: e.confirmation_id ?? "",
        skill: e.action?.skill_id ?? "",
        desc: e.action?.description ?? "",
      };
      if (!expanded.value) void expand(); // 高风险确认必须可见
      break;
    case "action_result":
      // 双窗口：确认可能在面板窗作答，结果回来即收尾（成功短闪 400ms，spec 选项 ①）
      pending.value = null;
      flashValence("success");
      break;
    case "final_reply_chunk": {
      // 流式增量：拼到当前 streaming bubble（首片时新建）
      if (streamingIdx.value === null) {
        bubbles.value.push({ role: "ai", text: e.text ?? "" });
        streamingIdx.value = bubbles.value.length - 1;
      } else {
        bubbles.value[streamingIdx.value].text += e.text ?? "";
      }
      // 收起态：撑出气泡，镜像流式文本（打字机效果）
      if (!expanded.value) {
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
        openBubble();
        bubbleText.value = full;
        if (state.value !== "say") scheduleBubbleClose(2200);
      }
      break;
    }
    case "interrupted":
      if (streamingIdx.value !== null) {
        bubbles.value[streamingIdx.value].text += " ⛔";
        streamingIdx.value = null;
      } else {
        bubbles.value.push({ role: "ai", text: "⛔ 已打断" });
      }
      state.value = "idle";
      closeBubbleNow();
      break;
    case "speaking_done":
      state.value = "idle";
      if (bubbleOn.value && !expanded.value) scheduleBubbleClose(1600); // 说完，留 1.6s 读完再收
      break;
    case "notice":
      // 轻提示（插件展开等，§12-2 要知情）：居中淡色小字，不弹窗不打断
      bubbles.value.push({ role: "sys", text: e.text ?? "" });
      break;
    case "reminder": {
      // 主动提醒：宠物可能收起/隐藏 → 亮窗 + 展开，确保被看见（不抢焦点）
      bubbles.value.push({ role: "ai", text: "⏰ " + (e.text ?? "到点了") });
      void (async () => {
        try {
          const win = getCurrentWindow();
          if (!(await win.isVisible())) await win.show();
          if (!expanded.value) await expand();
        } catch { /* 亮窗失败也至少留了气泡 */ }
      })();
      break;
    }
    case "error":
      state.value = "idle";
      streamingIdx.value = null;
      pending.value = null; // 确认被拒（任一窗口作答）或出错
      bubbles.value.push({ role: "ai", text: "⚠️ " + (e.text ?? "出错了") });
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
  pending.value = null;
  if (!brainDown.value) {
    brainDown.value = true;
    const why = m.detail ? `（${m.detail}）` : "";
    bubbles.value.push({ role: "ai", text: `⚠️ 大脑掉线${why}，正在自动重启…` });
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
  try {
    await runInput(text);
  } catch (err) {
    bubbles.value.push({ role: "ai", text: "⚠️ 发送失败：" + String(err) });
    state.value = "idle";
  }
}

async function decide(approved: boolean) {
  if (!pending.value) return;
  const { id } = pending.value;
  pending.value = null;
  state.value = "think";
  try {
    await sendConfirm(id, approved);
  } catch (err) {
    bubbles.value.push({ role: "ai", text: "⚠️ 确认失败：" + String(err) });
  }
}

function onMic() {
  // 不乐观置 listen：等大脑 listening 事件确认（语音栈不可用时大脑会回 error，别自欺卡死）
  void voiceStart().catch((err) => {
    bubbles.value.push({ role: "ai", text: "⚠️ 语音启动失败：" + String(err) });
  });
}

function onInterrupt() {
  if (!busy.value) return;
  void interrupt().catch((err) => {
    bubbles.value.push({ role: "ai", text: "⚠️ 打断失败：" + String(err) });
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

// 展开或说话气泡时整窗可交互；否则仅团子热区可交互、其余穿透
watch([expanded, bubbleOn], () => {
  void setInteractiveFull(expanded.value || bubbleOn.value);
});

onMounted(async () => {
  await resetWindowSize();
  void setInteractiveFull(false);
  unlisten = await onBrainEvent(onEvent);
  unlistenStatus = await onBrainStatus(onStatus);
  unlistenPerms = await onBrainPermissions(onPerms);
  unlistenPanelClosed = await onPanelClosed(() => {
    if (!panelOpen.value) return;
    panelOpen.value = false;
    bubbles.value.push({ role: "ai", text: "⇠ 协作结束" });
  });
  // 首启引导（生产打包首跑：装 Python 环境/下模型，大脑还没起来，走 Tauri 事件直推）
  unlistenSetup = await listen<{ stage: string; detail: string }>("setup-progress", (e) => {
    if (e.payload.stage !== "done" && !expanded.value) void expand();
    bubbles.value.push({ role: "sys", text: e.payload.detail });
  });
  unlistenSetupErr = await listen<string>("setup-error", (e) => {
    if (!expanded.value) void expand();
    bubbles.value.push({ role: "ai", text: "⚠️ " + e.payload });
  });
  unlistenSetupCfg = await listen<string>("setup-config-needed", () => void onSetupNeeded());
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
  window.removeEventListener("keydown", onKeydown);
  if (clickTimer !== null) clearTimeout(clickTimer);
  if (bubbleTimer !== null) clearTimeout(bubbleTimer);
  if (valenceTimer !== null) clearTimeout(valenceTimer);
});
</script>

<template>
  <div class="shell" :class="{ exp: expanded }">
    <!-- 常态：宠物球 + 状态文字 -->
    <template v-if="!expanded">
      <div class="speech-slot" v-if="bubbleOn">
        <SpeechBubble :text="bubbleText" :streaming="streamingIdx !== null" @expand="expand" />
      </div>
      <div class="pet-wrap">
        <Avatar class="pet" :state="state" :size="88" @click="onPetClick" @longpress="onMic" />
      </div>
    </template>

    <!-- 对话：header（头像+名称+状态+收起，一体化贴边）/ 内容区（权限引导/气泡流/输入条） -->
    <template v-else>
      <header class="chat-header flip" data-tauri-drag-region>
        <Avatar :state="state" :size="38" @click="collapse" />
        <div class="meta" data-tauri-drag-region>
          <span class="name">译宝</span>
          <span class="status" :class="state"><i class="dot" />{{ statusText }}</span>
        </div>
        <button class="collapse-btn" title="收起" @click="collapse">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
            <line x1="6" y1="12" x2="18" y2="12" />
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
        <div v-if="pluginErr" class="pl-err">⚠️ {{ pluginErr }}</div>
        <button v-for="p in plugins" :key="p.id" class="pl-row" @click="launchPlugin(p)">
          <span class="pl-name">{{ p.name }}</span>
          <span class="pl-id">{{ p.id }}</span>
        </button>
        <div v-if="!plugins.length && !pluginErr" class="pl-empty">没有发现插件</div>
      </div>

      <div v-else class="bubbles" ref="bubblesRef">
        <div v-if="!bubbles.length && !showTyping" class="empty-hint">
          <Avatar :state="state" :size="56" />
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
        />
        <Bubble v-if="showTyping" role="ai" text="" typing />
      </div>

      <div v-if="view === 'chat'" class="input-slot">
        <InputBar v-if="!pending" :busy="busy" :listening="state === 'listen'" @submit="submit" @mic="onMic" @interrupt="onInterrupt" />
        <ConfirmDialog
          v-else
          :skill="pending.skill"
          :desc="pending.desc"
          @approve="() => decide(true)"
          @deny="() => decide(false)"
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
  font-size: 13px;
  line-height: 1.6;
  color: var(--yb-text);
}
.shell.exp {
  display: flex;
  flex-direction: column;
  background:
    linear-gradient(180deg, rgba(77, 144, 196, 0.09), rgba(77, 144, 196, 0) 128px),
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
/* 说话态气泡槽：团子左侧（窗口撑开后腾出的空间） */
.speech-slot {
  position: absolute;
  left: 8px;
  top: 14px;
  width: 188px;
  z-index: 3;
}
/* 展开内容渐入：配合窗口补间，不突兀 */
.shell.exp .bubbles,
.shell.exp .input-slot {
  animation: fade-in 0.22s var(--yb-ease) 0.06s both;
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
  background: linear-gradient(180deg, rgba(77, 144, 196, 0.14), rgba(77, 144, 196, 0.08));
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
  line-height: 1.2;
  /* 拖动把手区（名称/状态文字上按住可拖窗） */
  cursor: default;
  user-select: none;
}
.name {
  font-size: var(--yb-fs-xl);
  font-weight: 650;
  letter-spacing: 0.01em;
}
/* 状态 pill：软底小胶囊，比裸文字更有「状态感」 */
.status {
  font-size: 11px;
  color: var(--yb-text-dim);
  display: inline-flex;
  align-items: center;
  gap: 5px;
  line-height: 1.4;
  padding: 1px 8px;
  border-radius: 999px;
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
  transition: all 0.15s ease;
}
.collapse-btn svg {
  width: 14px;
  height: 14px;
}
.collapse-btn:hover {
  background: var(--yb-well);
  color: var(--yb-text);
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
  border-radius: 3px;
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
  font-size: 13px;
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
  border-radius: 999px;
  background: var(--yb-surface-solid);
  color: var(--yb-accent-deep);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s ease;
}
.chip:hover {
  background: var(--yb-accent-soft);
  border-color: var(--yb-accent);
  color: var(--yb-accent-deep);
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
  font-size: 13px;
  cursor: pointer;
  padding: 3px 8px;
  border-radius: 10px;
  transition: all 0.15s ease;
}
.pl-back:hover {
  color: var(--yb-accent-deep);
  background: var(--yb-surface-solid);
}
.pl-err {
  padding: 6px var(--yb-space-3);
  border-radius: 10px;
  background: var(--yb-danger-soft);
  color: var(--yb-danger);
  font-size: 13px;
}
.pl-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--yb-space-2);
  padding: var(--yb-space-3) var(--yb-space-4);
  border: 1px solid var(--yb-surface-border);
  border-radius: 14px;
  background: var(--yb-surface-solid);
  box-shadow: var(--yb-shadow-soft);
  cursor: pointer;
  font-family: inherit;
  text-align: left;
  transition: all 0.15s ease;
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
  font-size: 13px;
}
</style>
