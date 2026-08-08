<script setup lang="ts">
// 大窗「对话」页：与宠物窗同一条大脑会话（surface=pet），事件处理同源 App.vue 气泡流；
// 差异：不管窗（无展开/收起/说话气泡），「⇢ 协作」关联气泡可点击跳插件页。
// 宠物窗隐藏时本页仍在后台收事件——同一条会话两边镜面，切回去气泡不丢。
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import Avatar from "./Avatar.vue";
import InputBar from "./InputBar.vue";
import Bubble from "./Bubble.vue";
import PermissionsBanner from "./PermissionsBanner.vue";
import SetupWizard from "./SetupWizard.vue";
import HomeContextBar from "./HomeContextBar.vue";
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
import { SUGGESTIONS } from "../lib/suggestions";
import { procLabel, procSkip, procResultSuffix, procDetail } from "../lib/proc";
import YbIcon from "./YbIcon.vue";

type AvatarState = "idle" | "listen" | "think" | "work" | "say" | "success" | "error";
// proc：过程展示（工具调用行，可点开展开参数/结果）；panelLink：「⇢ 协作」关联气泡
type ProcInfo = { label: string; action?: BrainEvent["action"]; result?: BrainEvent["result"]; done: boolean; expanded: boolean };
type BubbleMsg = {
  role: "user" | "ai" | "sys";
  text: string;
  panelLink?: boolean;
  proc?: ProcInfo;
  halted?: boolean;
  icon?: "clock" | "alert";
};

/** 告警气泡：⚠️ 前缀改行首 alert 图标渲染（文案纯净，图标走 YbIcon） */
function pushWarn(text: string) {
  bubbles.value.push({ role: "ai", text, icon: "alert" });
}

// state：同步给父级顶栏状态；openPanel：关联气泡点击 → 父级切插件页；reminder：父级切回本页
const emit = defineEmits<{ state: [AvatarState]; openPanel: []; reminder: [] }>();
// draft：主屏 Feed/信息面板点击带过来的自包含草稿，直接转给 InputBar（它自己 watch 填入+聚焦）
const props = defineProps<{ draft?: string }>();

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

const state = ref<AvatarState>("idle");
const bubbles = ref<BubbleMsg[]>([]);
const streamingIdx = ref<number | null>(null); // 正在接收 chunk 的 bubble 下标
const brainDown = ref(false); // 大脑掉线/重启中（守护在恢复）
const panelOpen = ref(false); // 面板协作会话进行中（关联气泡只插一次）
const perms = ref<BrainPermissions | null>(null); // macOS 权限状态（null=未收到）
// 过程展示：action.id → 过程行下标，结果回来原地更新 ✅/❌
const procIdx = new Map<string, number>();

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
  bubbles.value.push({ role: "sys", text: "配置已保存，大脑启动中…" });
}

// success/error 是短暂 valence（不可打断），不算 busy
const busy = computed(() =>
  state.value === "listen" || state.value === "think" ||
  state.value === "work" || state.value === "say",
);
const suggestions = SUGGESTIONS;
const missingPerms = computed(() => perms.value !== null && (!perms.value.ax || !perms.value.screen || !perms.value.input));
// 「正在输入」占位：run 受理（think）到首个 chunk 之间气泡流还是空的，用三点呼吸占位
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
watch(state, (s) => emit("state", s));

let unlisten: (() => void) | null = null;
let unlistenStatus: (() => void) | null = null;
let unlistenPerms: (() => void) | null = null;
let unlistenPanelClosed: (() => void) | null = null;
let unlistenSetup: (() => void) | null = null;
let unlistenSetupErr: (() => void) | null = null;
let unlistenSetupCfg: (() => void) | null = null;

function onEvent(e: BrainEvent) {
  // 会话分流：面板场景的对话事件只归插件页；panel 事件例外（关联气泡，本页也收）
  if (e.surface && e.surface !== "pet" && e.kind !== "panel") return;
  switch (e.kind) {
    case "action_proposed":
      state.value = "work";
      // 过程行：🔧 技能短标签（use_plugin 跳过——成功有 notice，不重复）
      if (e.action?.id && !procSkip(e.action)) {
        procIdx.set(e.action.id, bubbles.value.length);
        bubbles.value.push({
          role: "sys",
          text: "",
          proc: { label: procLabel(e.action), action: e.action, done: false, expanded: false },
        });
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
        procIdx.delete(e.action!.id!);
      }
      break;
    }
    case "final_reply_chunk":
      // 流式增量：拼到当前 streaming bubble（首片时新建）
      if (streamingIdx.value === null) {
        bubbles.value.push({ role: "ai", text: e.text ?? "" });
        streamingIdx.value = bubbles.value.length - 1;
      } else {
        bubbles.value[streamingIdx.value].text += e.text ?? "";
      }
      break;
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
      break;
    case "speaking_done":
      state.value = "idle";
      break;
    case "notice":
      // 轻提示（插件展开等，§12-2 要知情）：居中淡色小字，不弹窗不打断
      bubbles.value.push({ role: "sys", text: e.text ?? "" });
      break;
    case "reminder":
      // 主动提醒：落气泡 + 通知父级切回本页（大窗已可见，宠物窗自己管亮窗，两边互不抢）
      bubbles.value.push({ role: "ai", text: e.text ?? "到点了", icon: "clock" });
      emit("reminder");
      break;
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
      // 面板 = 插件页的嵌入视图：本页只留一条「派生」关联气泡（可点击直达），协作过程不镜像
      const title = e.payload?.title || e.payload?.panel || "插件面板";
      if (!panelOpen.value) {
        panelOpen.value = true;
        bubbles.value.push({ role: "ai", text: `⇢ 正在和「${title}」协作`, panelLink: true });
      }
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

async function submit(text: string) {
  bubbles.value.push({ role: "user", text });
  state.value = "think";
  try {
    await runInput(text, "pet");
  } catch (err) {
    pushWarn("发送失败：" + String(err));
    state.value = "idle";
  }
}

function onMic() {
  // 不乐观置 listen：等大脑 listening 事件确认（语音栈不可用时大脑会回 error，别自欺卡死）
  void voiceStart("pet").catch((err) => {
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

onMounted(async () => {
  unlisten = await onBrainEvent(onEvent);
  unlistenStatus = await onBrainStatus(onStatus);
  unlistenPerms = await onBrainPermissions((p) => { perms.value = p; });
  unlistenPanelClosed = await onPanelClosed(() => {
    if (!panelOpen.value) return;
    panelOpen.value = false;
    bubbles.value.push({ role: "ai", text: "⇠ 协作结束" });
  });
  // 首启引导（生产打包首跑：装 Python 环境/下模型，大脑还没起来，走 Tauri 事件直推）
  unlistenSetup = await listen<{ stage: string; detail: string }>("setup-progress", (e) => {
    bubbles.value.push({ role: "sys", text: e.payload.detail });
  });
  unlistenSetupErr = await listen<string>("setup-error", (e) => {
    pushWarn(e.payload);
  });
  unlistenSetupCfg = await listen<string>("setup-config-needed", () => void onSetupNeeded());
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
  if (valenceTimer !== null) clearTimeout(valenceTimer);
});
</script>

<template>
  <div class="chat-page">
    <SetupWizard v-if="setupNeeded" :model="setupCfg.model" :base-url="setupCfg.baseUrl" :voice="setupCfg.voice" @saved="onSetupSaved" />

    <template v-if="!setupNeeded">
    <!-- 上下文带：AI 此刻 / 动态 / 插件快捷 / 回顾（对话头顶的一体化流，非独立面板） -->
    <HomeContextBar @chat="onInfoChat" />

    <PermissionsBanner v-if="missingPerms && perms" :perms="perms" />

    <div class="bubbles" ref="bubblesRef">
      <div v-if="!bubbles.length && !showTyping" class="empty-hint">
        <Avatar :state="state" :size="56" />
        <p>叫我做什么都行～</p>
        <div class="chips">
          <button v-for="c in suggestions" :key="c" class="chip" @click="submit(c)">{{ c }}</button>
        </div>
      </div>
      <template v-for="(b, i) in bubbles" :key="i">
        <!-- 「⇢ 协作」关联气泡：可点击，直达插件页（派生入口，§主/子 agent 关联） -->
        <button v-if="b.panelLink" class="assoc" @click="emit('openPanel')">
          {{ b.text }}<span class="assoc-arrow">前往 ›</span>
        </button>
        <!-- 过程行：图标随状态（进行中转圈 / 成功 / 失败），点「详情」展开参数与结果 -->
        <div v-else-if="b.proc" class="proc">
          <button
            class="proc-line"
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
            <span class="proc-label">{{ b.proc.label }}{{ b.proc.done ? procErrSuffix(b.proc) : "" }}</span>
            <span class="proc-toggle">{{ b.proc.expanded ? "收起" : "详情" }}</span>
          </button>
          <pre v-if="b.proc.expanded" class="proc-detail">{{ procText(b.proc) }}</pre>
        </div>
        <Bubble v-else :role="b.role" :text="b.text" :streaming="i === streamingIdx" :halted="b.halted" :icon="b.icon" />
      </template>
      <Bubble v-if="showTyping" role="ai" text="" typing />
    </div>

    <div class="input-slot">
      <InputBar :busy="busy" :listening="state === 'listen'" :draft="draftRef" @submit="submit" @mic="onMic" @interrupt="onInterrupt" />
    </div>
    </template>
  </div>
</template>

<style scoped>
.chat-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--yb-content-bg);
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
/* 底部输入区：hairline 分界（与主屏同款），不再靠父级 padding 浮着 */
.input-slot {
  flex-shrink: 0;
  padding: var(--yb-space-3) var(--yb-space-5) var(--yb-space-4);
  border-top: 1px solid var(--yb-border-base);
  background: var(--yb-content-bg);
}
.bubbles::-webkit-scrollbar {
  width: 6px;
}
.bubbles::-webkit-scrollbar-thumb {
  background: var(--yb-surface-border);
  border-radius: var(--yb-radius-pill);
}
/* 「⇢ 协作」关联气泡：拟 AI 气泡但可点击，accent 细边 + hover 上浮（派生入口） */
.assoc {
  align-self: flex-start;
  max-width: 88%;
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
/* 过程展示：工具调用行（居中淡色小字，同 sys 调性）+ 可展开详情 */
.proc {
  align-self: center;
  max-width: 92%;
  display: flex;
  flex-direction: column;
  align-items: center;
  animation: pop var(--yb-dur-fast) var(--yb-ease-out);
}
.proc-line {
  display: inline-flex;
  align-items: center;
  gap: var(--yb-space-1);
  background: transparent;
  border: none;
  border-radius: var(--yb-radius-sm);
  color: var(--yb-text-dim);
  font-family: inherit;
  font-size: var(--yb-fs-xs);
  line-height: var(--yb-lh-base);
  cursor: pointer;
  padding: 2px var(--yb-space-2);
  transition: color var(--yb-dur-fast) var(--yb-ease-out);
}
.proc-line:hover {
  color: var(--yb-text);
}
/* 进行中的转圈图标用 accent，成功转 success：颜色本身就是状态信号 */
.proc-ic {
  flex-shrink: 0;
  color: var(--yb-accent);
}
.proc-line.done .proc-ic {
  color: var(--yb-intent-ok);
}
.proc-label {
  text-align: left;
}
.proc-line.fail,
.proc-line.fail .proc-ic {
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
</style>
