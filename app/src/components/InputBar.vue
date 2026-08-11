<script setup lang="ts">
import { ref, computed, watch, onMounted } from "vue";
import YbIcon from "./YbIcon.vue";
import { sessionStore } from "../state/store";

// busy = 生成/播报中（可打断）；listening = 录音中（麦克风切声波态，点击=取消录音）
// draft = 外部预填草稿（主屏 Feed 点击带上下文来）；变化即填入并聚焦
const props = defineProps<{ busy?: boolean; listening?: boolean; draft?: string }>();
type InputContext = { kind: "attachment" | "reference"; label: string };
const emit = defineEmits<{
  (e: "submit", text: string, contexts: InputContext[]): void;
  (e: "mic"): void;
  (e: "interrupt"): void;
}>();
const text = ref("");
const inputRef = ref<HTMLInputElement | null>(null);
const fileRef = ref<HTMLInputElement | null>(null);
const addOpen = ref(false);
const pendingContexts = ref<InputContext[]>([]);
const canSend = computed(() => text.value.trim().length > 0);

// 草稿暂存：写入 SessionStore.conversation（按活动会话），300ms trailing debounce 避免高频写
let draftTimer: ReturnType<typeof setTimeout> | null = null;
function persistDraft(v: string) {
  if (draftTimer) clearTimeout(draftTimer);
  draftTimer = setTimeout(() => {
    const id = sessionStore.conversation.getActiveConversationId();
    if (id) sessionStore.conversation.setDraft(id, v);
  }, 300);
}
onMounted(() => {
  const id = sessionStore.conversation.getActiveConversationId();
  if (id) {
    const saved = sessionStore.conversation.getUIState(id).draft;
    if (saved) text.value = saved;
  }
});
watch(text, (v) => persistDraft(v));

watch(
  () => props.draft,
  (v) => {
    if (v) {
      text.value = v;
      persistDraft(v);
      inputRef.value?.focus();
    }
  },
);

function send() {
  const t = text.value.trim();
  if (t) {
    // AI 正在生成/播报（stopping）时发送 = 先打断再发新消息（不必手动"停止"）
    if (stopping.value) emit("interrupt");
    emit("submit", t, pendingContexts.value.slice());
    text.value = "";
    pendingContexts.value = [];
    persistDraft("");
  }
}

function openAdd(kind: InputContext["kind"]) {
  addOpen.value = false;
  if (kind === "attachment") {
    fileRef.value?.click();
    return;
  }
  if (!pendingContexts.value.some((item) => item.kind === "reference")) {
    pendingContexts.value.push({ kind: "reference", label: "当前会话" });
  }
}

function onFileChange(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0];
  if (!file) return;
  pendingContexts.value.push({ kind: "attachment", label: file.name });
  (event.target as HTMLInputElement).value = "";
}

function removeContext(index: number) {
  pendingContexts.value.splice(index, 1);
}

function onMic() {
  // 聆听中再点麦克风 = 取消录音；否则发起语音输入
  if (props.listening) emit("interrupt");
  else emit("mic");
}

/** 右端主按钮：生成/播报中=打断、其余=发送（无内容置灰）；聆听时取消录音归麦克风，主按钮仍是发送。 */
const stopping = computed(() => props.busy && !props.listening);

function onMain() {
  // 生成/播报中：有输入文字 → 打断并发送新消息；无文字 → 仅打断
  if (stopping.value) {
    if (text.value.trim()) send();
    else emit("interrupt");
  } else {
    send();
  }
}

// 全局唤起等外部焦点请求（反射键唤起后输入就绪）
defineExpose({ focus: () => inputRef.value?.focus() });
</script>

<template>
  <form class="bar" @submit.prevent="send">
    <div class="add-wrap">
      <button
        type="button"
        class="add"
        :class="{ open: addOpen, active: pendingContexts.length }"
        aria-label="添加附件或引用"
        :aria-expanded="addOpen"
        title="添加附件或引用"
        @click="addOpen = !addOpen"
      >
        <YbIcon name="plus" :size="16" />
      </button>
      <div v-if="addOpen" class="add-menu" role="menu" aria-label="添加内容">
        <button type="button" role="menuitem" @click="openAdd('attachment')">
          <strong>附件</strong><small>文件或图片</small>
        </button>
        <button type="button" role="menuitem" @click="openAdd('reference')">
          <strong>引用</strong><small>当前会话上下文</small>
        </button>
      </div>
      <input ref="fileRef" class="file-input" type="file" @change="onFileChange" />
    </div>
    <div v-if="pendingContexts.length" class="context-list" aria-label="待发送的附件和引用">
      <span v-for="(context, index) in pendingContexts" :key="`${context.kind}-${context.label}-${index}`" class="context-chip">
        {{ context.kind === "attachment" ? "附件" : "引用" }} · {{ context.label }}
        <button type="button" aria-label="移除内容" @click="removeContext(index)">×</button>
      </span>
    </div>
    <input
      ref="inputRef"
      v-model="text"
      placeholder="对译宝说点什么…"
      @keydown.enter.prevent="send"
    />
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
  position: relative;
  display: flex;
  gap: 5px;
  align-items: center;
  min-height: 46px;
  padding: 5px 5px 5px 7px;
  border-radius: 24px;                        /* 更高的对话胶囊 */
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
    0 6px 18px rgba(var(--yb-c-slate-rgb), 0.10);
  /* focus ring：原用 box-shadow 0 0 0 3px spread，但 spread 在某些 WebView
   * 上从 padding-box 渲染，画在 border 内侧与 1px border 叠出"内圈"。
   * outline 明确从 border-box 外侧绘制且跟随 border-radius，无 inset 风险。 */
  outline: 2px solid var(--yb-accent-soft);
  outline-offset: 1px;
}
input {
  flex: 1;
  min-width: 0;
  border: none;
  background: transparent;
  /* 去 native control 描边：macOS WKWebView 即便 border:none 仍会留 -webkit-appearance
   * 默认的「凹槽」内边框，与外层 .bar 描边叠出双圈。appearance:none 一并清掉。
   * 显式 box-shadow:none 再防一层 UA inset 残留。 */
  -webkit-appearance: none;
  appearance: none;
  box-shadow: none;
  font-size: 14px;                            /* 13.5 → 14 更清晰 */
  outline: none;
  color: var(--yb-text);
}
input::placeholder {
  color: var(--yb-text-dim);
}
.add-wrap {
  position: relative;
  flex: none;
}
.add,
.mic,
.main {
  width: 34px;
  height: 34px;
  flex-shrink: 0;
  border-radius: 50%;
  cursor: pointer;
  display: grid;
  place-items: center;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.add {
  border: 1px solid transparent;
  background: transparent;
  color: var(--yb-accent);
}
.add:hover,
.add.open,
.add.active {
  background: var(--yb-surface-2);
  color: var(--yb-accent);
}
.add.active {
  box-shadow: inset 0 0 0 1px var(--yb-accent-soft);
}
.add-menu {
  position: absolute;
  left: -2px;
  bottom: calc(100% + 9px);
  width: 170px;
  padding: 5px;
  display: grid;
  gap: 2px;
  border: 1px solid var(--yb-surface-border);
  border-radius: var(--yb-radius-md);
  background: var(--yb-glass);
  -webkit-backdrop-filter: var(--yb-blur);
  backdrop-filter: var(--yb-blur);
  box-shadow: var(--yb-shadow-soft);
  z-index: 20;
}
.add-menu button {
  display: grid;
  gap: 1px;
  padding: 7px 9px;
  border: none;
  border-radius: var(--yb-radius-sm);
  background: transparent;
  color: var(--yb-text);
  text-align: left;
  cursor: pointer;
}
.add-menu button:hover {
  background: var(--yb-row-hover);
}
.add-menu strong {
  font-size: 12px;
  font-weight: var(--yb-fw-medium);
}
.add-menu small {
  color: var(--yb-text-faint);
  font-size: 10px;
}
.file-input {
  display: none;
}
.context-list {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
  max-width: 42%;
  overflow: hidden;
}
.context-chip {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  max-width: 170px;
  padding: 4px 7px;
  border-radius: var(--yb-radius-pill);
  background: var(--yb-accent-soft);
  color: var(--yb-accent-deep);
  font-size: 10px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.context-chip button {
  padding: 0;
  border: none;
  background: transparent;
  color: inherit;
  cursor: pointer;
  line-height: 1;
}
.mic,
.main {
  border: none;
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
