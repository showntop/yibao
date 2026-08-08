<script setup lang="ts">
import { ref, computed, watch } from "vue";

// busy = 生成/播报中（可打断）；listening = 录音中（麦克风切声波态，点击=取消录音）
// draft = 外部预填草稿（主屏 Feed 点击带上下文来）；变化即填入并聚焦
const props = defineProps<{ busy?: boolean; listening?: boolean; draft?: string }>();
const emit = defineEmits<{ (e: "submit", text: string): void; (e: "mic"): void; (e: "interrupt"): void }>();
const text = ref("");
const inputRef = ref<HTMLInputElement | null>(null);
const canSend = computed(() => text.value.trim().length > 0);

watch(
  () => props.draft,
  (v) => {
    if (v) {
      text.value = v;
      inputRef.value?.focus();
    }
  },
);

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

// 全局唤起等外部焦点请求（反射键唤起后输入就绪）
defineExpose({ focus: () => inputRef.value?.focus() });
</script>

<template>
  <form class="bar" @submit.prevent="send">
    <input ref="inputRef" v-model="text" placeholder="对译宝说点什么…" />
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
  gap: 4px;
  align-items: center;
  padding: 4px 4px 4px 12px;
  border-radius: 22px;                        /* 更胶囊（视觉稿） */
  background: var(--yb-glass);                /* 毛玻璃（统一浮层质感） */
  -webkit-backdrop-filter: var(--yb-blur);
  backdrop-filter: var(--yb-blur);
  border: 1px solid var(--yb-surface-border);
  /* 双层阴影：下层弥散浮起，上层锐边描立体（视觉稿感） */
  box-shadow:
    0 1px 2px rgba(0, 0, 0, 0.04),
    0 6px 18px rgba(var(--yb-c-slate-rgb), 0.10);
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.bar:focus-within {
  border-color: var(--yb-accent);
  box-shadow:
    0 1px 2px rgba(0, 0, 0, 0.04),
    0 6px 18px rgba(var(--yb-c-slate-rgb), 0.10),
    0 0 0 3px var(--yb-accent-soft);
}
input {
  flex: 1;
  min-width: 0;
  border: none;
  background: transparent;
  font-size: 14px;                            /* 13.5 → 14 更清晰 */
  outline: none;
  color: var(--yb-text);
}
input::placeholder {
  color: var(--yb-text-dim);
}
.mic,
.main {
  width: 30px;                                /* 收回 30（之前 34 在 260 宽容器里挤到边） */
  height: 30px;
  flex-shrink: 0;
  border-radius: 50%;
  cursor: pointer;
  display: grid;
  place-items: center;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.icon {
  width: 14px;                                /* 与 30 按钮协调 */
  height: 14px;
}
.mic {
  background: transparent;
  border: none;
  color: var(--yb-text-dim);
}
.mic:hover {
  background: var(--yb-well);
  color: var(--yb-text);
}
/* 聆听中：红底 + 脉动光环 + 声波动画（明确的「正在听」状态） */
.mic.listening {
  background: var(--yb-danger);
  border-color: transparent;
  color: var(--yb-text-on-accent);
  animation: mic-pulse 1.6s ease-out infinite;
}
@keyframes mic-pulse {
  0% {
    box-shadow: 0 0 0 0 rgba(var(--yb-c-red-rgb), 0.35);
  }
  70% {
    box-shadow: 0 0 0 8px rgba(var(--yb-c-red-rgb), 0);
  }
  100% {
    box-shadow: 0 0 0 0 rgba(var(--yb-c-red-rgb), 0);
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
  border-radius: var(--yb-radius-pill);
  background: var(--yb-surface-1);
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
/* 主按钮：常态=发送（主色实底），打断态=失败色浅底；图标交叉淡入淡出切换。
 * 阴影收到最小（之前晕 6px/35% 让按钮视觉上"大且溢出"容器） */
.main {
  border: none;
  background: var(--yb-accent);
  color: var(--yb-text-on-accent);
  box-shadow: 0 1px 2px rgba(var(--yb-c-sky-rgb), 0.25);
}
.main:hover:not(:disabled) {
  background: var(--yb-accent-deep);
}
.main:active:not(:disabled) {
  transform: scale(0.97);
}
.main:disabled {
  opacity: 0.4;
  cursor: default;
  box-shadow: none;
}
.main.stopping {
  background: var(--yb-danger-soft);
  color: var(--yb-danger);
  box-shadow: none;
  opacity: 1;
}
.swap-enter-active,
.swap-leave-active {
  transition: opacity var(--yb-dur-fast) var(--yb-ease-out), transform var(--yb-dur-fast) var(--yb-ease-out);
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
