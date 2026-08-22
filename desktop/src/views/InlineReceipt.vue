<script setup lang="ts">
// Inline 回执（Phase 1）：简单结果在过程行原地收束为宿主原生卡，最多两个动作（忽略/展开）。
// 视觉骨架、间距、按钮、风险提示全部由宿主控制——插件只提供身份（provider/标题）。
import { computed } from "vue";

const props = withDefaults(
  defineProps<{
    provider: string; // 插件 id（命名空间前缀，图标配色/回执头部用）
    title: string; // 面板标题（插件 · 面板）
    summary?: string; // 一句话结果摘要（缺省用 objectTitle）
    object?: { id?: string; title?: string } | null; // 跨应用接力对象
  }>(),
  { summary: "", object: null },
);
const emit = defineEmits<{ dismiss: []; expand: [] }>();

const headline = computed(() => props.summary || props.object?.title || "已完成");
const sub = computed(() => (props.summary ? props.object?.title ?? "" : ""));
const initial = computed(() => (props.provider ? props.provider.charAt(0).toUpperCase() : "?"));
</script>

<template>
  <div class="inline-receipt" role="status" aria-live="polite">
    <span class="ir-icon"><span class="ir-initial">{{ initial }}</span></span>
    <div class="ir-copy">
      <small>{{ provider }} · {{ title }}</small>
      <strong>{{ headline }}</strong>
      <span v-if="sub" class="ir-sub">{{ sub }}</span>
    </div>
    <div class="ir-actions">
      <button type="button" class="ir-btn" @click="emit('dismiss')">忽略</button>
      <button type="button" class="ir-btn accent" @click="emit('expand')">展开</button>
    </div>
  </div>
</template>

<style scoped>
.inline-receipt {
  box-sizing: border-box;
  max-width: 380px;
  min-width: 260px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 10px;
  border: 1px solid var(--yb-card-border);
  border-radius: var(--yb-card-radius);
  background: var(--yb-card-bg);
  box-shadow: var(--yb-shadow-2);
}
.ir-icon {
  width: 30px;
  height: 30px;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  border-radius: 8px;
  background: var(--yb-accent-soft);
  color: var(--yb-accent);
}
.ir-initial {
  font-size: 13px;
  font-weight: var(--yb-fw-bold);
}
.ir-copy {
  min-width: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.ir-copy small {
  color: var(--yb-text-faint);
  font-size: var(--yb-fs-xs);
}
.ir-copy strong {
  overflow: hidden;
  color: var(--yb-text);
  font-size: var(--yb-fs-md);
  font-weight: var(--yb-fw-medium);
  line-height: var(--yb-lh-base);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ir-sub {
  overflow: hidden;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-xs);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ir-actions {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 4px;
}
.ir-btn {
  padding: 4px 10px;
  border: 1px solid var(--yb-border-strong);
  border-radius: var(--yb-radius-xs);
  background: var(--yb-card-bg);
  color: var(--yb-text-dim);
  font: inherit;
  font-size: var(--yb-fs-xs);
  cursor: pointer;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.ir-btn:hover {
  background: var(--yb-btn-neutral);
  color: var(--yb-text);
}
.ir-btn.accent {
  border-color: var(--yb-accent);
  background: var(--yb-accent);
  color: var(--yb-text-on-accent);
}
.ir-btn.accent:hover {
  background: var(--yb-accent-deep);
  color: var(--yb-text-on-accent);
}
</style>
