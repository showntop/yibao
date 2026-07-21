<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from "vue";
import { invoke } from "@tauri-apps/api/core";
import Avatar from "./components/Avatar.vue";
import InputBar from "./components/InputBar.vue";
import ConfirmDialog from "./components/ConfirmDialog.vue";
import Bubble from "./components/Bubble.vue";
import PermissionsBanner from "./components/PermissionsBanner.vue";
import {
  onBrainEvent,
  onBrainStatus,
  onBrainPermissions,
  runInput,
  sendConfirm,
  voiceStart,
  interrupt,
  panelAction,
  type BrainEvent,
  type BrainStatusMsg,
  type BrainPermissions,
} from "./lib/brain";
import {
  expand as expandWin,
  collapse as collapseWin,
  resetCollapsedSize,
  openPanel,
  type Dir,
} from "./lib/window";

type AvatarState = "idle" | "listen" | "think" | "work" | "say";
type BubbleMsg = { role: "user" | "ai"; text: string };

const state = ref<AvatarState>("idle");
const bubbles = ref<BubbleMsg[]>([]);
const streamingIdx = ref<number | null>(null); // 正在接收 chunk 的 bubble 下标
const pending = ref<{ id: string; skill: string; desc: string } | null>(null);
const brainDown = ref(false); // 大脑掉线/重启中（守护在恢复）
const perms = ref<BrainPermissions | null>(null); // macOS 权限状态（null=未收到）
const expanded = ref(false);
const dir = ref<Dir>("nw"); // 展开方向（collapse 沿同一锚点缩回要用）
let unlisten: (() => void) | null = null;
let unlistenStatus: (() => void) | null = null;
let unlistenPerms: (() => void) | null = null;

const statusText = computed(
  () => ({ idle: "待命中", listen: "聆听中", think: "思考中…", work: "操作中…", say: "说话中…" }[state.value]),
);
const busy = computed(() => state.value !== "idle"); // listen/think/work/say 都可打断（聆听=取消录音）
const suggestions = ["记一条闪念", "看看选题看板", "帮我写点什么"];
const missingPerms = computed(() => perms.value !== null && (!perms.value.ax || !perms.value.screen));

async function expand() {
  expanded.value = true;
  dir.value = await expandWin();
}
async function collapse() {
  const d = dir.value;
  expanded.value = false;
  await collapseWin(d);
}

// ---- 插件启动器（双击团子）----
type PetView = "chat" | "plugins";
interface PluginInfo { id: string; name: string }
const view = ref<PetView>("chat");
const plugins = ref<PluginInfo[]>([]);
const pluginErr = ref("");
let clickTimer: ReturnType<typeof setTimeout> | null = null;

/** 单击=展开对话；双击=插件启动器（220ms 内第二次点击判双击，单击稍延迟是消歧代价）。 */
function onPetClick() {
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
      // 双窗口：确认可能在面板窗作答，结果回来即收尾（成功不再刷 ✓ 气泡）
      pending.value = null;
      break;
    case "final_reply_chunk": {
      // 流式增量：拼到当前 streaming bubble（首片时新建）
      if (streamingIdx.value === null) {
        bubbles.value.push({ role: "ai", text: e.text ?? "" });
        streamingIdx.value = bubbles.value.length - 1;
      } else {
        bubbles.value[streamingIdx.value].text += e.text ?? "";
      }
      break;
    }
    case "final_reply":
      // 以完整文本为准收尾（兜底 chunk 丢失）；语音中保持 say 等 speaking_done
      if (streamingIdx.value !== null) {
        bubbles.value[streamingIdx.value].text = e.text ?? "";
        streamingIdx.value = null;
      } else {
        bubbles.value.push({ role: "ai", text: e.text ?? "" });
      }
      if (state.value !== "say") state.value = "idle";
      break;
    case "interrupted":
      if (streamingIdx.value !== null) {
        bubbles.value[streamingIdx.value].text += " ⛔";
        streamingIdx.value = null;
      } else {
        bubbles.value.push({ role: "ai", text: "⛔ 已打断" });
      }
      state.value = "idle";
      break;
    case "speaking_done":
      state.value = "idle";
      break;
    case "error":
      state.value = "idle";
      streamingIdx.value = null;
      pending.value = null; // 确认被拒（任一窗口作答）或出错
      bubbles.value.push({ role: "ai", text: "⚠️ " + (e.text ?? "出错了") });
      break;
    case "listening":
      state.value = "listen";
      break;
    case "listening_done":
      state.value = "think";
      if (e.text) bubbles.value.push({ role: "user", text: e.text });
      break;
    case "speaking":
      state.value = "say";
      break;
    case "panel":
      // 面板 = 独立浮窗（工作模式）：交给面板窗，宠物窗收回球形态
      void openPanel();
      if (expanded.value) void collapse();
      break;
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
  state.value = "listen";
  void voiceStart();
}

function onInterrupt() {
  if (!busy.value) return;
  void interrupt().catch((err) => {
    bubbles.value.push({ role: "ai", text: "⚠️ 打断失败：" + String(err) });
  });
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === "Escape" && expanded.value) void collapse();
}

onMounted(async () => {
  await resetCollapsedSize();
  unlisten = await onBrainEvent(onEvent);
  unlistenStatus = await onBrainStatus(onStatus);
  unlistenPerms = await onBrainPermissions(onPerms);
  window.addEventListener("keydown", onKeydown);
});
onUnmounted(() => {
  unlisten?.();
  unlistenStatus?.();
  unlistenPerms?.();
  window.removeEventListener("keydown", onKeydown);
  if (clickTimer !== null) clearTimeout(clickTimer);
});
</script>

<template>
  <div class="shell" :class="{ exp: expanded }">
    <!-- 常态：宠物球 + 状态文字 -->
    <template v-if="!expanded">
      <Avatar class="pet" :state="state" @click="onPetClick" @longpress="onMic" />
      <div class="status-collapsed" :class="state">{{ statusText }}</div>
    </template>

    <!-- 对话：header（头像+名称+状态+收起）/ (权限引导) / 气泡流 / 输入条 -->
    <template v-else>
      <header class="chat-header" :class="{ flip: dir.endsWith('e') }">
        <Avatar :state="state" :size="44" @click="collapse" />
        <div class="meta">
          <span class="name">译宝</span>
          <span class="status" :class="state"><i class="dot" />{{ statusText }}</span>
        </div>
        <button class="collapse-btn" title="收起" @click="collapse">—</button>
      </header>

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

      <div v-else class="bubbles">
        <div v-if="!bubbles.length" class="empty-hint">
          <p>叫我做什么都行～</p>
          <div class="chips">
            <button v-for="c in suggestions" :key="c" class="chip" @click="submit(c)">{{ c }}</button>
          </div>
        </div>
        <Bubble v-for="(b, i) in bubbles" :key="i" :role="b.role" :text="b.text" />
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

<style scoped>
.shell {
  position: relative;
  height: 100vh;
  box-sizing: border-box;
  overflow: hidden;
  font-family: -apple-system, "PingFang SC", system-ui, sans-serif;
  color: var(--yb-text);
}
.shell.exp {
  padding: var(--yb-space-3);
  display: flex;
  flex-direction: column;
  gap: var(--yb-space-2);
  background: var(--yb-shell-bg);
  -webkit-backdrop-filter: var(--yb-blur);
  backdrop-filter: var(--yb-blur);
  border: 1px solid var(--yb-glass-border);
  border-radius: var(--yb-radius-xl);
  box-shadow: var(--yb-shadow);
}
/* 常态：宠物球（可拖可点开） */
.pet {
  position: absolute;
  left: 34px;
  top: 12px;
  z-index: 3;
  animation: fade-in 0.18s var(--yb-ease) both;
}
/* 展开内容渐入：配合窗口补间，不突兀 */
.shell.exp .chat-header,
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
.chat-header {
  display: flex;
  align-items: center;
  gap: 10px;
}
/* 锚点在右侧时（dir=ne/se）镜像头部，头像与收起锚点同侧 */
.chat-header.flip {
  flex-direction: row-reverse;
}
.chat-header.flip .meta {
  align-items: flex-end;
}
.meta {
  flex: 1;
  display: flex;
  flex-direction: column;
  line-height: 1.3;
}
.name {
  font-size: var(--yb-fs-xl);
  font-weight: 600;
}
.status {
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-dim);
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
/* 状态点：颜色跟团子状态色环同源 */
.status .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--dot, var(--yb-idle));
}
.status.idle {
  --dot: var(--yb-idle);
}
.status.listen {
  --dot: var(--yb-listen);
}
.status.think {
  --dot: var(--yb-think);
}
.status.work {
  --dot: var(--yb-work);
}
.status.say {
  --dot: var(--yb-say);
}
.status.think,
.status.work {
  color: var(--yb-accent);
}
.collapse-btn {
  width: 26px;
  height: 26px;
  flex-shrink: 0;
  border: none;
  border-radius: var(--yb-radius-sm);
  background: var(--yb-btn-neutral);
  color: var(--yb-text-dim);
  cursor: pointer;
  font-size: 14px;
  line-height: 1;
}
.collapse-btn:hover {
  filter: brightness(0.96);
}
.bubbles {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: var(--yb-space-2);
  overflow-y: auto;
  padding: 0 2px;
  scrollbar-width: thin;
}
.bubbles::-webkit-scrollbar {
  width: 6px;
}
.bubbles::-webkit-scrollbar-thumb {
  background: var(--yb-surface-border);
  border-radius: 3px;
}
/* 空状态：气泡区占位引导 */
.empty-hint {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--yb-space-3);
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-md);
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
  background: var(--yb-surface);
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-md);
  cursor: pointer;
  transition: color var(--yb-dur) var(--yb-ease), border-color var(--yb-dur) var(--yb-ease);
}
.chip:hover {
  color: var(--yb-accent);
  border-color: var(--yb-accent);
}
.status-collapsed {
  position: absolute;
  left: 0;
  right: 0;
  top: 86px;
  text-align: center;
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-dim);
  animation: fade-in 0.18s var(--yb-ease) both;
}
.status-collapsed.think,
.status-collapsed.work {
  color: var(--yb-accent);
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
  font-size: var(--yb-fs-md);
  cursor: pointer;
  padding: 3px 8px;
  border-radius: var(--yb-radius-sm);
}
.pl-back:hover {
  color: var(--yb-accent);
  background: var(--yb-btn-neutral);
}
.pl-err {
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
  background: var(--yb-surface);
  cursor: pointer;
  font-family: inherit;
  text-align: left;
  transition: border-color var(--yb-dur) var(--yb-ease), transform var(--yb-dur) var(--yb-ease);
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
  font-size: var(--yb-fs-md);
}
</style>
