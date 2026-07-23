<script setup lang="ts">
// 收起态回复气泡：短回复直接显示；长内容给摘要 + 预览 +「点开看」。
// 仅展示；显隐与自动收起计时由父组件 App.vue 拥有。点整体 = 展开。
defineProps<{ text: string; preview?: string; long?: boolean }>();
defineEmits<{ (e: "expand"): void; (e: "hover"): void; (e: "leave"): void }>();
</script>

<template>
  <div class="peek" @click="$emit('expand')" @mouseenter="$emit('hover')" @mouseleave="$emit('leave')">
    <div class="who">译宝</div>
    <div v-if="long" class="digest">{{ text }}</div>
    <div v-else class="short">{{ text }}</div>
    <div v-if="long && preview" class="preview">{{ preview }}</div>
    <span v-if="long" class="chip">点开看 →</span>
    <i class="tail" aria-hidden="true" />
  </div>
</template>

<style scoped>
.peek {
  position: relative;
  max-width: 230px;
  background: var(--yb-surface-solid);
  border: 1px solid var(--yb-surface-border);
  border-radius: var(--yb-radius-md);
  padding: 10px 12px;
  box-shadow: var(--yb-shadow);
  font-size: var(--yb-fs-md);
  line-height: 1.6;
  color: var(--yb-text);
  cursor: pointer;
  animation: rise 0.3s var(--yb-ease) both;
}
.who { font-size: var(--yb-fs-sm); color: var(--yb-text-dim); font-weight: 600; margin-bottom: 3px; }
.short { white-space: pre-wrap; word-break: break-word; }
.digest { font-weight: 600; color: var(--yb-accent-deep); }
.preview { color: var(--yb-text-dim); font-size: var(--yb-fs-sm); margin-top: 4px; line-height: 1.5; }
.chip {
  display: inline-flex; align-items: center; gap: 4px; margin-top: 8px;
  background: var(--yb-accent); color: #fff; font-size: var(--yb-fs-sm); font-weight: 600;
  padding: 4px 10px; border-radius: 999px;
}
/* tail 指向右侧团子（团子默认 dock 右上 → 气泡在左） */
.tail {
  position: absolute; right: -5px; top: 18px; width: 10px; height: 10px;
  background: var(--yb-surface-solid);
  border-right: 1px solid var(--yb-surface-border);
  border-bottom: 1px solid var(--yb-surface-border);
  transform: rotate(-45deg);
}
@keyframes rise {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: none; }
}
</style>
