<script setup lang="ts">
import { computed } from "vue";
import { renderMarkdownLite } from "../lib/markdown";

// typing = 「正在输入」占位（三点呼吸，无文本）；streaming = 流式进行中（尾部闪光标）
const props = defineProps<{ role: "user" | "ai" | "sys"; text: string; typing?: boolean; streaming?: boolean }>();
// 用户消息原样纯文本；AI 消息走 markdown-lite（转义在前，安全）；sys 是轻提示（插件展开等）
const html = computed(() => (props.role === "ai" && !props.typing ? renderMarkdownLite(props.text) : null));
</script>

<template>
  <div v-if="typing" class="bubble ai typing" aria-label="正在输入"><i /><i /><i /></div>
  <div v-else-if="html !== null" :class="['bubble', role]">
    <span v-html="html"></span><span v-if="streaming" class="cur">▍</span>
  </div>
  <div v-else :class="['bubble', role]">{{ text }}</div>
</template>

<style scoped>
.bubble {
  padding: var(--yb-space-2) var(--yb-space-3);
  border-radius: var(--yb-radius-md);
  max-width: 88%;
  font-size: 13px;
  line-height: 1.6;
  word-break: break-word;
  animation: pop 0.15s ease;
}
.ai {
  background: var(--yb-bubble-ai);
  border: 1px solid var(--yb-surface-border);
  color: var(--yb-text);
  align-self: flex-start;
  box-shadow: var(--yb-shadow);
  /* 尾巴角：靠左下的角收窄，拟小尾巴 */
  border-radius: var(--yb-radius-md) var(--yb-radius-md) var(--yb-radius-md) 4px;
}
.user {
  background: linear-gradient(135deg, var(--yb-accent), var(--yb-accent-deep));
  color: #fff;
  align-self: flex-end;
  box-shadow: var(--yb-shadow);
  /* 尾巴角：靠右下的角收窄 */
  border-radius: var(--yb-radius-md) var(--yb-radius-md) 4px var(--yb-radius-md);
}
/* 轻提示（插件展开等 notice）：居中淡色小字，不拟气泡、不打断阅读 */
.sys {
  background: transparent;
  color: var(--yb-text-dim);
  font-size: 11.5px;
  align-self: center;
  padding: 0 var(--yb-space-3);
  box-shadow: none;
}
/* 「正在输入」占位：三点呼吸，accent 色 */
.typing {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 11px var(--yb-space-3);
}
.typing i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--yb-accent);
  animation: typing-breathe 1.2s ease-in-out infinite;
}
.typing i:nth-child(2) {
  animation-delay: 0.15s;
}
.typing i:nth-child(3) {
  animation-delay: 0.3s;
}
/* 流式光标：与 SpeechBubble 一致的闪烁 ▍ */
.cur {
  display: inline-block;
  width: 0.55em;
  margin-left: 1px;
  color: var(--yb-accent);
  animation: blink 0.9s steps(2, start) infinite;
}
/* markdown-lite 块样式（v-html 内容，需 :deep） */
.ai :deep(.md-h) {
  font-weight: 700;
  margin: 2px 0;
}
.ai :deep(.md-li) {
  padding-left: 2px;
}
.ai :deep(.md-kv) {
  padding-left: 2px;
}
.ai :deep(.md-kv-h) {
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-md);
}
.ai :deep(.md-gap) {
  height: 6px;
}
.ai :deep(.md-hr) {
  border-top: 1px solid var(--yb-line);
  margin: 6px 0;
}
.ai :deep(code) {
  font-family: var(--yb-mono);
  font-size: 0.92em;
  background: var(--yb-code-inline-bg);
  border-radius: 4px;
  padding: 0 4px;
}
/* 围栏代码块：等宽 + 浅底 + 横向滚动；块内 code 去掉行内底色 */
.ai :deep(pre) {
  margin: 4px 0;
  padding: 8px 10px;
  background: var(--yb-code-bg);
  border-radius: var(--yb-radius-sm);
  overflow-x: auto;
}
.ai :deep(pre code) {
  background: transparent;
  padding: 0;
  border-radius: 0;
}
@keyframes pop {
  from {
    opacity: 0;
    transform: translateY(4px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}
@keyframes typing-breathe {
  0%,
  100% {
    opacity: 0.3;
    transform: translateY(0);
  }
  50% {
    opacity: 1;
    transform: translateY(-2px);
  }
}
@keyframes blink {
  0%,
  50% {
    opacity: 1;
  }
  50.01%,
  100% {
    opacity: 0;
  }
}
@media (prefers-reduced-motion: reduce) {
  .cur,
  .typing i {
    animation: none;
  }
}
</style>
