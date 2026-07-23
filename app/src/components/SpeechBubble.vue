<script setup lang="ts">
// 说话态气泡：流式 chunk 由父组件拼到 text（天然打字机效果），streaming 时尾部闪光标。
// 仅展示；显隐/窗口撑开/计时由父组件 App.vue 拥有。点整体 = 展开完整聊天窗。
defineProps<{ text: string; streaming?: boolean }>();
defineEmits<{ (e: "expand"): void }>();
</script>

<template>
  <div class="sb" @click="$emit('expand')">
    <div class="who">译宝</div>
    <div class="body">
      <span class="txt">{{ text }}<span v-if="streaming" class="cur">▍</span></span>
    </div>
    <i class="tail" aria-hidden="true" />
  </div>
</template>

<style scoped>
.sb {
  position: relative;
  background: var(--yb-surface-solid);
  border: 1px solid var(--yb-surface-border);
  border-radius: var(--yb-radius-md);
  padding: 8px 11px;
  box-shadow: var(--yb-shadow);
  font-size: var(--yb-fs-md);
  line-height: 1.55;
  color: var(--yb-text);
  cursor: pointer;
  animation: rise 0.2s var(--yb-ease) both;
}
.who {
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-dim);
  font-weight: 600;
  margin-bottom: 2px;
}
/* 最多 3 行；超长由父组件决定是否 expand 看全文 */
.body {
  max-height: 72px;
  overflow: hidden;
  word-break: break-word;
  white-space: pre-wrap;
}
.cur {
  display: inline-block;
  width: 0.55em;
  margin-left: 1px;
  color: var(--yb-accent);
  animation: blink 0.9s steps(2, start) infinite;
}
/* tail 指向右侧团子 */
.tail {
  position: absolute;
  right: -5px;
  top: 16px;
  width: 10px;
  height: 10px;
  background: var(--yb-surface-solid);
  border-right: 1px solid var(--yb-surface-border);
  border-bottom: 1px solid var(--yb-surface-border);
  transform: rotate(-45deg);
}
@keyframes rise {
  from { opacity: 0; transform: translateY(5px); }
  to { opacity: 1; transform: none; }
}
@keyframes blink {
  0%, 50% { opacity: 1; }
  50.01%, 100% { opacity: 0; }
}
@media (prefers-reduced-motion: reduce) {
  .cur { animation: none; }
}
</style>
