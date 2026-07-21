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
  border-radius: 14px;
  max-width: 88%;
  font-size: 13px;
  line-height: 1.6;
  word-break: break-word;
  animation: pop 0.15s ease;
}
.ai {
  background: #ffffff;
  border: 1px solid #eee4d6;
  color: #3f372e;
  align-self: flex-start;
  box-shadow: 0 1px 2px rgba(90, 70, 50, 0.04), 0 6px 16px rgba(90, 70, 50, 0.05);
}
.user {
  background: #fff0e8;
  border: 1px solid #eee4d6;
  color: #3f372e;
  align-self: flex-end;
  box-shadow: 0 1px 2px rgba(90, 70, 50, 0.04), 0 6px 16px rgba(90, 70, 50, 0.05);
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
  color: #a89a86;
  font-size: var(--yb-fs-md);
}
.ai :deep(.md-gap) {
  height: 6px;
}
.ai :deep(.md-hr) {
  border-top: 1px solid #f3ecdf;
  margin: 6px 0;
}
.ai :deep(code) {
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
  font-size: 0.92em;
  background: #f3ecdf;
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
