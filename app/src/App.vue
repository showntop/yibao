<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from "vue";
import Avatar from "./components/Avatar.vue";
import InputBar from "./components/InputBar.vue";
import ConfirmDialog from "./components/ConfirmDialog.vue";
import Bubble from "./components/Bubble.vue";
import { onBrainEvent, runInput, sendConfirm, voiceStart, type BrainEvent } from "./lib/brain";
import {
  expand as expandWin,
  collapse as collapseWin,
  resetCollapsedSize,
  type Dir,
} from "./lib/window";

type AvatarState = "idle" | "listen" | "think" | "work" | "say";
type BubbleMsg = { role: "user" | "ai"; text: string };

const state = ref<AvatarState>("idle");
const bubbles = ref<BubbleMsg[]>([]);
const pending = ref<{ id: string; skill: string; desc: string } | null>(null);
const expanded = ref(false);
const dir = ref<Dir>("nw");
let unlisten: (() => void) | null = null;

const statusText = computed(
  () => ({ idle: "待命中", listen: "聆听中", think: "思考中…", work: "操作中…", say: "说话中…" }[state.value]),
);

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
      if (e.result?.success) {
        bubbles.value.push({ role: "ai", text: "✓ " + JSON.stringify(e.result.data ?? {}) });
      }
      break;
    case "final_reply":
      state.value = "idle";
      bubbles.value.push({ role: "ai", text: e.text ?? "" });
      break;
    case "error":
      state.value = "idle";
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

function onKeydown(e: KeyboardEvent) {
  if (e.key === "Escape" && expanded.value) void collapse();
}

onMounted(async () => {
  await resetCollapsedSize();
  unlisten = await onBrainEvent(onEvent);
  window.addEventListener("keydown", onKeydown);
});
onUnmounted(() => {
  unlisten?.();
  window.removeEventListener("keydown", onKeydown);
});
</script>

<template>
  <div class="shell" :class="expanded ? ['exp', dir] : []">
    <Avatar class="pet" :state="state" @click="toggleExpand" />

    <div v-if="expanded" class="meta">
      <span class="name">译宝</span>
      <span class="status" :class="state">{{ statusText }}</span>
    </div>

    <div v-if="expanded" class="bubbles">
      <Bubble v-for="(b, i) in bubbles" :key="i" :role="b.role" :text="b.text" />
    </div>

    <div v-if="expanded" class="input-slot">
      <InputBar v-if="!pending" @submit="submit" @mic="onMic" />
      <ConfirmDialog
        v-else
        :skill="pending.skill"
        :desc="pending.desc"
        @approve="() => decide(true)"
        @deny="() => decide(false)"
      />
    </div>

    <div v-else class="status-collapsed" :class="state">{{ statusText }}</div>
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
  padding: 12px;
  display: flex;
  flex-direction: column;
  background: var(--yb-bg);
  -webkit-backdrop-filter: var(--yb-blur);
  backdrop-filter: var(--yb-blur);
  border: 1px solid var(--yb-glass-border);
  border-radius: 18px;
  box-shadow: var(--yb-shadow);
}
/* 形象钉在展开方向的源角；收起态默认左上偏移(=居中) */
.pet {
  position: absolute;
  left: 34px;
  top: 12px;
  z-index: 3;
}
.exp.ne .pet {
  left: auto;
  right: 34px;
}
.exp.sw .pet {
  top: auto;
  bottom: 12px;
}
.exp.se .pet {
  left: auto;
  top: auto;
  right: 34px;
  bottom: 12px;
}
.meta {
  position: absolute;
  z-index: 2;
  display: flex;
  flex-direction: column;
  line-height: 1.3;
}
.exp.nw .meta {
  left: 110px;
  top: 16px;
  align-items: flex-start;
}
.exp.ne .meta {
  right: 110px;
  top: 16px;
  align-items: flex-end;
}
.exp.sw .meta {
  left: 110px;
  bottom: 16px;
  align-items: flex-start;
}
.exp.se .meta {
  right: 110px;
  bottom: 16px;
  align-items: flex-end;
}
.name {
  font-size: 15px;
  font-weight: 600;
}
.status {
  font-size: 11.5px;
  color: var(--yb-text-dim);
}
.status.think,
.status.work {
  color: var(--yb-accent);
}
.bubbles {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 7px;
  overflow-y: auto;
  padding: 0 2px;
  scrollbar-width: thin;
  order: 1;
}
.bubbles::-webkit-scrollbar {
  width: 6px;
}
.bubbles::-webkit-scrollbar-thumb {
  background: rgba(0, 0, 0, 0.15);
  border-radius: 3px;
}
/* 形象在上(nw/ne) → bubbles 避让顶部；形象在下(sw/se) → 避让底部 */
.exp.nw .bubbles,
.exp.ne .bubbles {
  margin-top: 80px;
}
.exp.sw .bubbles,
.exp.se .bubbles {
  margin-bottom: 80px;
}
/* input 默认在底(order 2)；形象在下(sw/se)时 input 移到顶(order 0) */
.input-slot {
  order: 2;
}
.exp.sw .input-slot,
.exp.se .input-slot {
  order: 0;
}
.status-collapsed {
  position: absolute;
  left: 0;
  right: 0;
  top: 86px;
  text-align: center;
  font-size: 11.5px;
  color: var(--yb-text-dim);
}
.status-collapsed.think,
.status-collapsed.work {
  color: var(--yb-accent);
}
</style>
