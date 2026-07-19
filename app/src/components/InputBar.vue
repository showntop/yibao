<script setup lang="ts">
import { ref, computed } from "vue";

defineProps<{ busy?: boolean }>();
const emit = defineEmits<{ (e: "submit", text: string): void; (e: "mic"): void; (e: "interrupt"): void }>();
const text = ref("");
const canSend = computed(() => text.value.trim().length > 0);

function send() {
  const t = text.value.trim();
  if (t) {
    emit("submit", t);
    text.value = "";
  }
}
</script>

<template>
  <form class="bar" @submit.prevent="send">
    <input v-model="text" placeholder="对译宝说点什么…" />
    <button type="button" class="mic" aria-label="语音输入" @click="emit('mic')">🎤</button>
    <button
      v-if="busy"
      type="button"
      class="stop"
      aria-label="打断（停止生成与播报）"
      title="打断"
      @click="emit('interrupt')"
    >
      ⏹
    </button>
    <button type="submit" class="send" :disabled="!canSend" aria-label="发送">↑</button>
  </form>
</template>

<style scoped>
.bar {
  display: flex;
  gap: var(--yb-space-2);
  align-items: center;
  padding: 6px 6px 6px var(--yb-space-3);
  border-radius: var(--yb-radius-lg);
  background: var(--yb-surface);
  border: 1px solid var(--yb-surface-border);
  box-shadow: var(--yb-shadow-soft);
}
input {
  flex: 1;
  border: none;
  background: transparent;
  font-size: var(--yb-fs-lg);
  outline: none;
  color: var(--yb-text);
}
input::placeholder {
  color: var(--yb-text-dim);
}
.mic,
.send {
  width: 30px;
  height: 30px;
  flex-shrink: 0;
  border-radius: 50%;
  border: none;
  cursor: pointer;
  display: grid;
  place-items: center;
  line-height: 1;
  transition: filter 0.15s, opacity 0.15s;
}
.mic {
  background: var(--yb-btn-neutral);
  font-size: 16px;
}
.stop {
  background: var(--yb-danger-soft);
  color: var(--yb-danger);
  font-size: 14px;
}
.stop:hover {
  filter: brightness(1.04);
}
.send {
  background: var(--yb-accent);
  color: #fff;
  font-size: 16px;
  font-weight: 700;
}
.mic:hover {
  filter: brightness(0.96);
}
.send:hover:not(:disabled) {
  filter: brightness(1.06);
}
.send:disabled {
  opacity: 0.4;
  cursor: default;
}
</style>
