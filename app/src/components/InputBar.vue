<script setup lang="ts">
import { ref, computed } from "vue";

// busy = 生成/播报中（可打断）；listening = 录音中（麦克风切声波态，点击=取消录音）
const props = defineProps<{ busy?: boolean; listening?: boolean }>();
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

function onMic() {
  // 聆听中再点麦克风 = 取消录音；否则发起语音输入
  if (props.listening) emit("interrupt");
  else emit("mic");
}

/** 右端主按钮：生成/播报中=打断、其余=发送（无内容置灰）；聆听时取消录音归麦克风，主按钮仍是发送。 */
const stopping = computed(() => props.busy && !props.listening);

function onMain() {
  if (stopping.value) emit("interrupt");
  else send();
}
</script>

<template>
  <form class="bar" @submit.prevent="send">
    <input v-model="text" placeholder="对译宝说点什么…" />
    <button
      type="button"
      class="mic"
      :class="{ listening }"
      :aria-label="listening ? '聆听中，点击取消' : '语音输入'"
      :title="listening ? '聆听中，点击取消' : '语音输入'"
      @click="onMic"
    >
      <span v-if="listening" class="wave"><i /><i /><i /></span>
      <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round" class="icon">
        <rect x="9" y="2" width="6" height="12" rx="3" />
        <path d="M5 10a7 7 0 0 0 14 0" />
        <line x1="12" y1="19" x2="12" y2="22" />
      </svg>
    </button>
    <button
      type="button"
      class="main"
      :class="{ stopping }"
      :disabled="!stopping && !canSend"
      :aria-label="stopping ? '打断（停止生成与播报）' : '发送'"
      :title="stopping ? '打断' : '发送'"
      @click="onMain"
    >
      <Transition name="swap" mode="out-in">
        <svg v-if="stopping" key="stop" viewBox="0 0 24 24" fill="currentColor" class="icon">
          <rect x="6" y="6" width="12" height="12" rx="2.5" />
        </svg>
        <svg v-else key="send" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"
          stroke-linecap="round" stroke-linejoin="round" class="icon">
          <line x1="12" y1="19" x2="12" y2="5" />
          <polyline points="5 12 12 5 19 12" />
        </svg>
      </Transition>
    </button>
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
.main {
  width: 30px;
  height: 30px;
  flex-shrink: 0;
  border-radius: 50%;
  border: none;
  cursor: pointer;
  display: grid;
  place-items: center;
  transition: filter 0.15s, opacity 0.15s, background 0.15s;
}
.icon {
  width: 15px;
  height: 15px;
}
.mic {
  background: var(--yb-btn-neutral);
  color: var(--yb-text-dim);
}
.mic:hover {
  filter: brightness(0.96);
  color: var(--yb-text);
}
/* 聆听中：红底 + 脉动光环 + 声波动画（明确的「正在听」状态） */
.mic.listening {
  background: var(--yb-danger);
  color: #fff;
  animation: mic-pulse 1.6s ease-out infinite;
}
@keyframes mic-pulse {
  0% {
    box-shadow: 0 0 0 0 rgba(229, 72, 77, 0.35);
  }
  70% {
    box-shadow: 0 0 0 8px rgba(229, 72, 77, 0);
  }
  100% {
    box-shadow: 0 0 0 0 rgba(229, 72, 77, 0);
  }
}
.wave {
  display: flex;
  align-items: center;
  gap: 2.5px;
  height: 14px;
}
.wave i {
  width: 2.5px;
  height: 5px;
  border-radius: 2px;
  background: #fff;
  animation: wave 1s ease-in-out infinite;
}
.wave i:nth-child(2) {
  animation-delay: 0.15s;
}
.wave i:nth-child(3) {
  animation-delay: 0.3s;
}
@keyframes wave {
  0%,
  100% {
    height: 5px;
  }
  50% {
    height: 13px;
  }
}
/* 主按钮：常态=发送（杏橙），打断态=红色方块；图标交叉淡入淡出切换 */
.main {
  background: var(--yb-accent);
  color: #fff;
}
.main:hover:not(:disabled) {
  filter: brightness(1.06);
}
.main:disabled {
  opacity: 0.4;
  cursor: default;
}
.main.stopping {
  background: var(--yb-danger-soft);
  color: var(--yb-danger);
  opacity: 1;
}
.main.stopping:hover {
  filter: brightness(1.04);
}
.swap-enter-active,
.swap-leave-active {
  transition: opacity 0.12s ease, transform 0.12s ease;
}
.swap-enter-from,
.swap-leave-to {
  opacity: 0;
  transform: scale(0.7);
}
.swap-enter-active,
.swap-leave-active {
  display: grid;
  place-items: center;
}
</style>
