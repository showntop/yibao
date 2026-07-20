<script setup lang="ts">
import { computed } from "vue";
import { renderMarkdownLite } from "../lib/markdown";

const props = defineProps<{ role: "user" | "ai"; text: string }>();
// 用户消息原样纯文本；AI 消息走 markdown-lite（转义在前，安全）
const html = computed(() => (props.role === "ai" ? renderMarkdownLite(props.text) : null));
</script>

<template>
  <div v-if="html !== null" :class="['bubble', role]" v-html="html"></div>
  <div v-else :class="['bubble', role]">{{ text }}</div>
</template>

<style scoped>
.bubble {
  padding: var(--yb-space-2) var(--yb-space-3);
  border-radius: var(--yb-radius-lg);
  max-width: 88%;
  font-size: var(--yb-fs-lg);
  line-height: 1.45;
  word-break: break-word;
  box-shadow: var(--yb-shadow-soft);
  animation: pop var(--yb-dur) var(--yb-ease);
}
.ai {
  background: var(--yb-bubble-ai);
  color: var(--yb-text);
  align-self: flex-start;
  border-bottom-left-radius: 4px;
}
.user {
  background: var(--yb-bubble-user);
  color: var(--yb-text);
  align-self: flex-end;
  border-bottom-right-radius: 4px;
}
/* markdown-lite 块样式（v-html 内容，需 :deep） */
.ai :deep(.md-h) {
  font-weight: 700;
  margin: 2px 0;
}
.ai :deep(.md-li) {
  padding-left: 14px;
  position: relative;
}
.ai :deep(.md-li)::before {
  content: "·";
  position: absolute;
  left: 4px;
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
  border-top: 1px solid var(--yb-surface-border);
  margin: 6px 0;
}
.ai :deep(code) {
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
  font-size: 0.92em;
  background: var(--yb-well);
  border-radius: 4px;
  padding: 0 4px;
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
</style>
