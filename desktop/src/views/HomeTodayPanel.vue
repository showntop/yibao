<script setup lang="ts">
// 今日瓷片（wb-prototype home.png）：月历 + 今日安排。数据在 lib/home/today-axis.ts。
import { computed, onMounted, onUnmounted, ref } from "vue";
import { getFeedOnce, onFeed, type FeedItem, type FeedResponse } from "../lib/brain";
import { agendaOf, monthGrid, monthNav, type AgendaItem, type AgendaStatus, type MonthView } from "../lib/home/today-axis.ts";

const items = ref<FeedItem[]>([]);
const now = ref(new Date());
const view = ref<MonthView>({ year: now.value.getFullYear(), month: now.value.getMonth() });

const WEEK_HEAD = ["日", "一", "二", "三", "四", "五", "六"] as const;

let unFeed: (() => void) | null = null;
let clock: ReturnType<typeof setInterval> | null = null;

onMounted(async () => {
  try {
    const feed: FeedResponse = await getFeedOnce();
    items.value = feed.items ?? [];
  } catch {
    /* feed 不可得：月历照常，安排空着 */
  }
  unFeed = await onFeed((r: FeedResponse) => {
    items.value = r.items ?? [];
  });
  clock = setInterval(() => (now.value = new Date()), 30_000);
});

onUnmounted(() => {
  unFeed?.();
  if (clock) clearInterval(clock);
});

const grid = computed(() => monthGrid(now.value, view.value));
const viewLabel = computed(() => `${view.value.year}年${view.value.month + 1}月`);
const agenda = computed<AgendaItem[]>(() => agendaOf(items.value, now.value));

function nav(dir: 1 | -1) {
  view.value = monthNav(view.value, dir);
}

function backToToday() {
  view.value = { year: now.value.getFullYear(), month: now.value.getMonth() };
}

const STATUS_LABEL: Record<AgendaStatus, string> = { done: "已完成", active: "进行中", upcoming: "待开始" };
</script>

<template>
  <section class="today-panel">
    <header class="head">
      <h3 class="title">今日</h3>
      <div class="nav">
        <button class="nav-btn" type="button" aria-label="上一月" @click="nav(-1)">‹</button>
        <span class="view-label">{{ viewLabel }}</span>
        <button class="nav-btn" type="button" aria-label="下一月" @click="nav(1)">›</button>
        <button class="now-btn" type="button" title="回到本月" @click="backToToday">今</button>
      </div>
    </header>
    <div class="cal">
      <div class="week-head">
        <span v-for="w in WEEK_HEAD" :key="w" class="wh">{{ w }}</span>
      </div>
      <div v-for="(row, ri) in grid" :key="ri" class="week-row">
        <span
          v-for="(c, ci) in row"
          :key="ci"
          class="cell"
          :class="{ dim: !c.inMonth, today: c.today }"
        >{{ c.date }}</span>
      </div>
    </div>
    <div class="agenda">
      <p class="agenda-title">今日安排</p>
      <p v-if="!agenda.length" class="agenda-empty">今天没有安排，享受空白</p>
      <div v-for="a in agenda" :key="`${a.time}-${a.title}`" class="ag-row" :data-status="a.status">
        <span class="ag-dot" />
        <time class="ag-time">{{ a.time }}</time>
        <span class="ag-text">{{ a.title }}</span>
        <span class="ag-badge">{{ STATUS_LABEL[a.status] }}</span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.today-panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
}
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.title {
  margin: 0;
  font-size: var(--yb-fs-lg);
  font-weight: var(--yb-fw-medium);
  color: var(--yb-text-strong);
}
.nav {
  display: flex;
  align-items: center;
  gap: 2px;
}
.nav-btn,
.now-btn {
  border: 0;
  background: none;
  padding: 2px 6px;
  border-radius: 6px;
  color: var(--yb-text-dim);
  font: inherit;
  font-size: var(--yb-fs-sm);
  cursor: pointer;
}
.nav-btn:hover,
.now-btn:hover {
  background: var(--yb-btn-neutral);
  color: var(--yb-text-strong);
}
.view-label {
  min-width: 74px;
  text-align: center;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-sm);
}
.cal {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.week-head,
.week-row {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 2px;
}
.wh {
  text-align: center;
  color: var(--yb-text-faint);
  font-size: 10px;
}
.cell {
  text-align: center;
  padding: 3px 0;
  border-radius: 8px;
  color: var(--yb-text);
  font-size: var(--yb-fs-sm);
  font-variant-numeric: tabular-nums;
}
.cell.dim {
  color: var(--yb-text-faint);
  opacity: 0.55;
}
.cell.today {
  background: var(--yb-accent-soft);
  box-shadow: inset 0 0 0 1.5px var(--yb-accent);
  color: var(--yb-accent-deep, var(--yb-accent));
  font-weight: var(--yb-fw-medium);
}
.agenda {
  border-top: 1px solid var(--yb-line);
  padding-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.agenda-title {
  margin: 0 0 2px;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-xs);
}
.agenda-empty {
  margin: 0;
  color: var(--yb-text-faint);
  font-size: var(--yb-fs-sm);
}
.ag-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}
.ag-dot {
  flex: none;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--yb-accent);
  align-self: center;
}
.ag-row[data-status="done"] .ag-dot {
  background: var(--yb-c-slate-300);
}
.ag-row[data-status="done"] .ag-text {
  color: var(--yb-text-faint);
  text-decoration: line-through;
  text-decoration-color: var(--yb-border-strong);
}
.ag-row[data-status="active"] .ag-badge {
  background: rgba(var(--yb-c-sky-rgb), 0.14);
  color: var(--yb-accent-deep, var(--yb-accent));
}
.ag-time {
  flex: none;
  color: var(--yb-text-dim);
  font-family: var(--yb-mono);
  font-size: var(--yb-fs-xs);
  font-variant-numeric: tabular-nums;
}
.ag-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  color: var(--yb-text);
  font-size: var(--yb-fs-sm);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ag-badge {
  flex: none;
  padding: 1px 8px;
  border-radius: var(--yb-radius-pill);
  background: var(--yb-btn-neutral);
  color: var(--yb-text-dim);
  font-size: 10px;
}
</style>
