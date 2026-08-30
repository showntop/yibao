<script setup lang="ts">
// 提醒卡（wb-prototype home.png 右列）：圆圈 + 标题 + 状态徽标 + 时间。
// 状态只从时间推导（已响/待响/每天），数据在 lib/home/home-glance-faces.ts。
import { computed, onMounted, onUnmounted, ref } from "vue";
import HomeWidget from "./HomeWidget.vue";
import { getFeedOnce, getWidgetsOnce, onFeed, onWidgets, type FeedItem, type WidgetPayload } from "../lib/brain";
import { remindCardFaces, type RemindCardItem } from "../lib/home/home-glance-faces.ts";

const widgets = ref<WidgetPayload[]>([]);
const feed = ref<FeedItem[]>([]);
let unWidgets: (() => void) | null = null;
let unFeed: (() => void) | null = null;

onMounted(async () => {
  try {
    const w = await getWidgetsOnce().catch(() => ({ widgets: [] as WidgetPayload[] }));
    widgets.value = w.widgets ?? [];
    const f = await getFeedOnce();
    feed.value = f.items ?? [];
  } catch { /* 空态即可 */ }
  try {
    unWidgets = await onWidgets((payload) => { widgets.value = payload?.widgets ?? []; });
  } catch { /* ignore */ }
  try {
    unFeed = await onFeed((r) => { feed.value = r.items ?? []; });
  } catch { /* ignore */ }
});

onUnmounted(() => {
  unWidgets?.();
  unFeed?.();
});

const rows = computed<RemindCardItem[]>(() =>
  remindCardFaces(
    reminderRows.value,
    feed.value,
  ));

const reminderRows = computed(() => {
  const hit = widgets.value.find((widget) => widget.panel.startsWith("reminders:"));
  const data = hit?.data;
  if (!data || typeof data !== "object" || !("rows" in data) || !Array.isArray(data.rows)) return [];
  return data.rows as Array<{ text?: unknown; when?: unknown }>;
});

const BADGE: Partial<Record<RemindCardItem["state"], string>> = { done: "已响", due: "待响" };
</script>

<template>
  <aside class="remind-card">
    <HomeWidget id="remind" aria-label="提醒">
      <header class="head">
        <span class="kicker">提醒</span>
        <span class="total">全部 {{ rows.length }}</span>
      </header>
      <p v-if="!rows.length" class="empty">今天没有提醒</p>
      <div v-for="(r, i) in rows" :key="i" class="row" :data-state="r.state">
        <span class="ring" :class="{ filled: r.state === 'done' }">
          <svg v-if="r.state === 'done'" viewBox="0 0 10 10" class="tick"><path d="M2 5.2 4.2 7.4 8 3" /></svg>
        </span>
        <span class="col">
          <span class="text">{{ r.text }}</span>
          <span class="when">{{ r.when }}</span>
        </span>
        <span v-if="r.state !== 'daily'" class="badge" :class="r.state">{{ BADGE[r.state] }}</span>
      </div>
    </HomeWidget>
  </aside>
</template>

<style scoped>
.remind-card { display: contents; }
/* 宽度硬约束：任何内容都不把卡片撑出列宽（真机长文本溢出防护） */
.remind-card :deep(.yb-widget) {
  min-width: 0;
  max-width: 100%;
}
.head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 4px;
}
.kicker {
  color: var(--yb-paper-ink-dim);
  font-size: var(--yb-fs-xs);
  font-weight: var(--yb-fw-medium);
  letter-spacing: 0.04em;
}
.total {
  color: var(--yb-text-faint);
  font-size: var(--yb-fs-xs);
}
.empty {
  margin: 0;
  color: var(--yb-text-faint);
  font-size: var(--yb-fs-sm);
}
.row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 6px 0;
  min-width: 0;
}
.ring {
  flex: none;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  border: 1.5px solid var(--yb-border-strong);
  display: flex;
  align-items: center;
  justify-content: center;
  margin-top: 1px;
}
.row[data-state="due"] .ring {
  border-color: var(--yb-c-amber-500);
}
.ring.filled {
  border-color: var(--yb-intent-ok);
  background: var(--yb-intent-ok);
}
.tick {
  width: 9px;
  height: 9px;
  fill: none;
  stroke: #fff;
  stroke-width: 1.6;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.col {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.text {
  overflow: hidden;
  color: var(--yb-text);
  font-size: var(--yb-fs-sm);
  line-height: 1.35;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.row[data-state="done"] .text {
  color: var(--yb-text-faint);
}
.when {
  color: var(--yb-text-faint);
  font-family: var(--yb-mono);
  font-size: 10px;
  font-variant-numeric: tabular-nums;
}
.badge {
  flex: none;
  align-self: center;
  padding: 1px 8px;
  border-radius: var(--yb-radius-pill);
  font-size: 10px;
}
.badge.due {
  background: var(--yb-intent-pending-soft);
  color: var(--yb-intent-pending-ink);
}
.badge.done {
  background: rgba(62, 142, 90, 0.12);
  color: var(--yb-intent-ok);
}
</style>
