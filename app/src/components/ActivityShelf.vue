<script setup lang="ts">
// 活动轨（Phase 1）：三态胶囊——运行中 / 待批准 / 已完成（quiet 结果）。
// 用户正在操作时只改变状态：不抢键盘焦点、不自动展开、不做补偿点击。
// 数据源本阶段接内存态（panelState + 待批准队列 + quiet 结果），权威持久化留 Phase 2 TaskTimeline。
import YbIcon from "./YbIcon.vue";

interface ActivityItem {
  panel: string;
  title: string;
  plugin: string;
  objectTitle?: string;
}
withDefaults(
  defineProps<{
    items: ActivityItem[];
    /** 运行中任务（当前 capability 正在处理） */
    busy?: { title: string; plugin: string } | null;
    /** 待批准队列长度 */
    pendingCount?: number;
  }>(),
  { busy: null, pendingCount: 0 },
);
const emit = defineEmits<{ open: [item: ActivityItem]; "open-pending": [] }>();
</script>

<template>
  <div class="activity-shelf" aria-label="活动轨">
    <button v-if="busy" type="button" class="shelf-item busy" title="任务运行中">
      <span class="shelf-ic spin"><YbIcon name="spinner" :size="12" /></span>
      <span class="shelf-copy">
        <small>{{ busy.plugin }}</small>
        <strong>正在处理…</strong>
      </span>
    </button>
    <button v-if="pendingCount > 0" type="button" class="shelf-item warn" title="待批准" @click="emit('open-pending')">
      <span class="shelf-ic">{{ pendingCount }}</span>
      <span class="shelf-copy">
        <small>收件箱</small>
        <strong>等你确认 {{ pendingCount }} 项</strong>
      </span>
    </button>
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
.shelf-item.busy {
  cursor: default;
}
.shelf-item.warn .shelf-ic {
  background: var(--yb-intent-pending-soft);
  color: var(--yb-intent-pending-ink);
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
.shelf-ic.spin :deep(svg) {
  animation: shelf-spin 1s linear infinite;
}
@keyframes shelf-spin {
  to { transform: rotate(360deg); }
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
