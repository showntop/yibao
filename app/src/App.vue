<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from "vue";
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
const busy = computed(() => state.value === "think" || state.value === "work" || state.value === "say");
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
async function toggleExpand() {
  if (expanded.value) await collapse();
  else await expand();
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
});
</script>

<template>
  <div class="shell" :class="{ exp: expanded }">
    <!-- 常态：宠物球 + 状态文字 -->
    <template v-if="!expanded">
      <Avatar class="pet" :state="state" @click="toggleExpand" @longpress="onMic" />
      <div class="status-collapsed" :class="state">{{ statusText }}</div>
    </template>

    <!-- 对话：header（头像+名称+状态+收起）/ (权限引导) / 气泡流 / 输入条 -->
    <template v-else>
      <header class="chat-header" :class="{ flip: dir.endsWith('e') }">
        <Avatar :state="state" :size="44" @click="collapse" />
        <div class="meta">
          <span class="name">译宝</span>
          <span class="status" :class="state">{{ statusText }}</span>
        </div>
        <button class="collapse-btn" title="收起" @click="collapse">—</button>
      </header>

      <PermissionsBanner v-if="missingPerms && perms" :perms="perms" />

      <div class="bubbles">
        <Bubble v-for="(b, i) in bubbles" :key="i" :role="b.role" :text="b.text" />
      </div>

      <div class="input-slot">
        <InputBar v-if="!pending" :busy="busy" @submit="submit" @mic="onMic" @interrupt="onInterrupt" />
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
</style>
