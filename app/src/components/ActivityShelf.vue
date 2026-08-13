<script setup lang="ts">
// 活动轨（Phase 1）：quiet 结果只记入这里，不展开任何表面；用户想回看时点开升到 stage。
// 横向轨道，不重排主屏；超出宽度可横滚。
interface ActivityItem {
  panel: string;
  title: string;
  plugin: string;
  objectTitle?: string;
}
defineProps<{ items: ActivityItem[] }>();
const emit = defineEmits<{ open: [item: ActivityItem] }>();
</script>

<template>
  <div class="activity-shelf" aria-label="活动轨">
    <button
      v-for="(it, i) in items"
      :key="`${it.panel}-${i}`"
      type="button"
      class="shelf-item"
      :title="it.objectTitle ? `${it.title} · ${it.objectTitle}` : it.title"
      @click="emit('open', it)"
    >
      <span class="shelf-ic">{{ (it.plugin || "?").charAt(0).toUpperCase() }}</span>
      <span class="shelf-copy">
        <small>{{ it.plugin }} · {{ it.title }}</small>
        <strong>{{ it.objectTitle || "已就绪" }}</strong>
      </span>
    </button>
  </div>
</template>

<style scoped>
.activity-shelf {
  position: fixed;
  left: 50%;
  bottom: var(--yb-space-3);
  z-index: var(--yb-z-inline);
  transform: translateX(-50%);
  display: flex;
  align-items: stretch;
  gap: 6px;
  max-width: min(560px, calc(100vw - 32px));
  padding: 5px 6px;
  overflow-x: auto;
  border: 1px solid var(--yb-card-border);
  border-radius: var(--yb-radius-sm);
  background: var(--yb-card-bg);
  box-shadow: var(--yb-shadow-2);
}
.shelf-item {
  flex-shrink: 0;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 150px;
  max-width: 220px;
  padding: 5px 9px;
  border: 1px solid transparent;
  border-radius: var(--yb-radius-xs);
  background: transparent;
  text-align: left;
  cursor: pointer;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.shelf-item:hover {
  border-color: rgba(var(--yb-c-sky-rgb), 0.3);
  background: var(--yb-accent-soft);
}
.shelf-ic {
  width: 22px;
  height: 22px;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  border-radius: 7px;
  background: var(--yb-accent-soft);
  color: var(--yb-accent);
  font-size: 12px;
  font-weight: var(--yb-fw-bold);
}
.shelf-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 0;
}
.shelf-copy small {
  overflow: hidden;
  color: var(--yb-text-faint);
  font-size: var(--yb-fs-xs);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.shelf-copy strong {
  overflow: hidden;
  color: var(--yb-text);
  font-size: var(--yb-fs-sm);
  font-weight: var(--yb-fw-medium);
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
