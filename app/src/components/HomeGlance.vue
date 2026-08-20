<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import HomeWidget from "./HomeWidget.vue";
import {
  getFeedOnce,
  onFeed,
  onPendingConfirms,
  type FeedItem,
  type PendingConfirm,
  type RunningTask,
} from "../lib/brain";

const emit = defineEmits<{ chat: [draft: string] }>();
defineProps<{ only?: "need" | "tasks" | "remind" }>();

const approvals = ref<PendingConfirm[]>([]);
const tasks = ref<RunningTask[]>([]);
const reminders = ref<FeedItem[]>([]);
let unFeed: (() => void) | null = null;
let unApprovals: (() => void) | null = null;

const needLine = computed(() => {
  const first = approvals.value[0];
  if (!first) return "没有等你的事";
  if (approvals.value.length === 1) return first.label || "待批准";
  return `${first.label} · 另 ${approvals.value.length - 1} 件`;
});

const taskLine = computed(() => {
  const first = tasks.value[0];
  if (!first) return "没有在跑的事";
  if (tasks.value.length === 1) return first.label;
  return `${first.label} · 另 ${tasks.value.length - 1} 件`;
});

const remindLine = computed(() => {
  const first = reminders.value[0];
  if (!first) return "没有待办的点";
  return first.text;
});

onMounted(async () => {
  unApprovals = onPendingConfirms((list) => { approvals.value = list; });
  try {
    const feed = await getFeedOnce();
    tasks.value = feed.running_tasks ?? [];
    reminders.value = (feed.items ?? []).filter((item) => item.kind === "reminder" && item.status !== "ignore").slice(0, 3);
  } catch { /* 大脑不在线时瓷片保持空态 */ }
  try {
    unFeed = await onFeed((feed) => {
      tasks.value = feed.running_tasks ?? [];
      reminders.value = (feed.items ?? []).filter((item) => item.kind === "reminder" && item.status !== "ignore").slice(0, 3);
    });
  } catch { /* 无事件通道时保留一次拉取 */ }
});

onUnmounted(() => {
  unFeed?.();
  unApprovals?.();
});
</script>

<template>
  <aside class="glance">
    <HomeWidget v-if="!only || only === 'need'" id="need" aria-label="需要你处理的确认">
      <h2 class="yb-widget-head">需要你 <span v-if="approvals.length" class="yb-widget-meta">{{ approvals.length }}</span></h2>
      <button
        v-if="approvals.length"
        class="glance-line"
        type="button"
        :title="approvals[0].desc || approvals[0].label"
        @click="emit('chat', `处理「${approvals[0].label}」`)"
      >{{ needLine }}</button>
      <p v-else class="glance-empty">{{ needLine }}</p>
    </HomeWidget>

    <HomeWidget v-if="!only || only === 'tasks'" id="tasks" aria-label="正在进行的任务">
      <h2 class="yb-widget-head">进行中 <span v-if="tasks.length" class="yb-widget-meta">{{ tasks.length }}</span></h2>
      <button
        v-if="tasks.length"
        class="glance-line"
        type="button"
        :title="tasks[0].label"
        @click="emit('chat', `查看正在进行的「${tasks[0].label}」`)"
      >{{ taskLine }}</button>
      <p v-else class="glance-empty">{{ taskLine }}</p>
    </HomeWidget>

    <HomeWidget v-if="!only || only === 'remind'" id="remind" aria-label="待办提醒">
      <h2 class="yb-widget-head">提醒 <span v-if="reminders.length" class="yb-widget-meta">{{ reminders.length }}</span></h2>
      <button
        v-if="reminders.length"
        class="glance-line"
        type="button"
        :title="reminders[0].text"
        @click="emit('chat', `查看提醒「${reminders[0].text}」`)"
      >{{ remindLine }}</button>
      <p v-else class="glance-empty">{{ remindLine }}</p>
    </HomeWidget>
  </aside>
</template>

<style scoped>
.glance { display: contents; }

.glance-line,
.glance-empty {
  display: block;
  width: calc(100% - 16px);
  margin: 0 8px 10px;
  padding: 7px 8px;
  border: 0;
  border-radius: calc(var(--yb-widget-radius) - 8px);
  background: var(--yb-note-mute);
  box-shadow: var(--yb-press);
  color: var(--yb-paper-ink);
  font: inherit;
  font-size: 11px;
  line-height: 1.35;
  text-align: left;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.glance-line { cursor: pointer; }
.glance-line:hover { color: var(--yb-paper-ink); filter: brightness(0.98); }
.glance-line:focus-visible {
  outline: 2px solid var(--yb-accent);
  outline-offset: 1px;
}
.glance-line:active { transform: translateY(1px); }
.glance-empty { color: var(--yb-paper-ink-dim); }

@media (prefers-reduced-motion: reduce) {
  .glance-line:active { transform: none; }
}
</style>
