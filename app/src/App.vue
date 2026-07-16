<script setup lang="ts">
import { ref, onMounted, onUnmounted } from "vue";
import Avatar from "./components/Avatar.vue";
import InputBar from "./components/InputBar.vue";
import ConfirmDialog from "./components/ConfirmDialog.vue";
import Bubble from "./components/Bubble.vue";
import { onBrainEvent, runInput, sendConfirm, type BrainEvent } from "./lib/brain";

type AvatarState = "idle" | "listen" | "think" | "work";
type BubbleMsg = { role: "user" | "ai"; text: string };

const state = ref<AvatarState>("idle");
const bubbles = ref<BubbleMsg[]>([]);
const pending = ref<{ id: string; skill: string; desc: string } | null>(null);
let unlisten: (() => void) | null = null;

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
      break;
    case "action_result":
      if (e.result?.success) {
        bubbles.value.push({
          role: "ai",
          text: "✓ " + JSON.stringify(e.result.data ?? {}),
        });
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

onMounted(async () => {
  unlisten = await onBrainEvent(onEvent);
});
onUnmounted(() => unlisten?.());
</script>

<template>
  <div class="shell">
    <Avatar :state="state" />
    <div class="bubbles">
      <Bubble v-for="(b, i) in bubbles" :key="i" :role="b.role" :text="b.text" />
    </div>
    <InputBar v-if="!pending" @submit="submit" />
    <ConfirmDialog
      v-else
      :skill="pending.skill"
      :desc="pending.desc"
      @approve="() => decide(true)"
      @deny="() => decide(false)"
    />
  </div>
</template>

<style scoped>
.shell {
  display: flex;
  flex-direction: column;
  gap: 8px;
  height: 100vh;
  box-sizing: border-box;
  padding: 12px;
  font-family: -apple-system, "PingFang SC", system-ui, sans-serif;
}
.bubbles {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 6px;
  overflow-y: auto;
  padding: 2px;
}
</style>
