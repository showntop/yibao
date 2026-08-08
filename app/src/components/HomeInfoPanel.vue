<script setup lang="ts">
/* HomeInfoPanel — 主屏右侧信息面板（AI 原生 OS 的「一瞥」侧栏）。
 *
 * 主屏 = 对话（左）+ 本面板（右）：AI 此刻在干嘛 / 插件入口 / 动态 / 回顾，
 * 用户对话的同时能扫到 AI 的状态与产出，需要时点一下带上下文进对话。
 * 数据订阅与 HomeFeed 同源（feed / widgets / 待批 / 回顾），展示精简版。
 */
import { computed, onMounted, onUnmounted, ref } from "vue";
import YbIcon from "./YbIcon.vue";
import {
  getFeedOnce,
  getWidgetsOnce,
  getDistillTimelineOnce,
  onFeed,
  onWidgets,
  onPendingConfirms,
  onRecapOpen,
  recapCheck,
  panelAction,
  markFeedRead,
  type FeedItem,
  type FeedStats,
  type RunningTask,
  type PendingConfirm,
  type WidgetPayload,
  type DistillDay,
} from "../lib/brain";

const emit = defineEmits<{
  chat: [draft: string]; // 点动态 → 带上下文进对话
}>();

// ---- 此刻：状态数字 + 进行中 + 今日完成 ----
const stats = ref<FeedStats>({ pending_reminders: 0, running_tasks: 0, done_24h: 0, unread: 0, ignored: 0 });
const runningTasks = ref<RunningTask[]>([]);
const loaded = ref(false);

const overview = computed(() => {
  const o: { key: string; n: number; label: string }[] = [];
  if (stats.value.done_24h > 0) o.push({ key: "done", n: stats.value.done_24h, label: "今日完成" });
  if (stats.value.running_tasks > 0) o.push({ key: "run", n: stats.value.running_tasks, label: "正在跑" });
  if (stats.value.pending_reminders > 0) o.push({ key: "rem", n: stats.value.pending_reminders, label: "待提醒" });
  return o;
});
const quiet = computed(
  () => loaded.value && !overview.value.length && !runningTasks.value.length && !widgets.value.length,
);

function elapsedSince(ts: number): string {
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (seconds < 60) return "刚开始";
  if (seconds < 3600) return `已运行 ${Math.floor(seconds / 60)} 分钟`;
  return `已运行 ${Math.floor(seconds / 3600)} 小时`;
}

function openTasks() {
  void panelAction("agents.task_list", {}, undefined, "panel:agents").catch(() => {});
}

// ---- 插件入口（一瞥行：标题 + chevron）----
const widgets = ref<WidgetPayload[]>([]);
function openWidget(w: WidgetPayload) {
  if (!w.open) return;
  const pid = w.panel.split(":")[0];
  void panelAction(w.open, {}, undefined, `panel:${pid}`).catch(() => {});
}

// ---- 待批准：琥珀小卡（有才显）----
const approvals = ref<PendingConfirm[]>([]);

// ---- 动态：feed 前几条摘要，点击带上下文进对话 ----
const items = ref<FeedItem[]>([]);
const feedPreview = computed(() => [...items.value].sort((a, b) => b.ts - a.ts).slice(0, 6));

const unreadCount = computed(() => feedPreview.value.filter((it) => it.read === 0).length);

function itemTime(ts: number): string {
  const d = new Date(ts * 1000);
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return "刚刚";
  if (diff < 172800) {
    return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  }
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

function kindIcon(it: FeedItem): "clock" | "check" | "x" | "chat" {
  if (it.kind === "reminder") return "clock";
  if (it.kind === "task") return taskStatus(it) === "done" ? "check" : "x";
  return "chat";
}
function taskStatus(it: FeedItem): string {
  return String(it.meta?.status ?? "done");
}

/** 点动态：乐观置已读 + 带自包含上下文草稿进对话。 */
async function openInChat(it: FeedItem) {
  if (it.read === 0) {
    it.read = 1;
    if (stats.value.unread > 0) stats.value = { ...stats.value, unread: stats.value.unread - 1 };
    try {
      await markFeedRead(it.id);
    } catch {
      it.read = 0;
    }
  }
  const oneLine = it.text.replace(/\s+/g, " ").trim();
  const truncated = oneLine.length > 60 ? oneLine.slice(0, 60) + "…" : oneLine;
  const prompt = typeof it.meta?.prompt === "string" && it.meta.prompt ? it.meta.prompt : "";
  const draft = it.kind === "task" && prompt
    ? `关于任务「${prompt.length > 40 ? prompt.slice(0, 40) + "…" : prompt}」：`
    : `关于「${truncated}」：`;
  emit("chat", draft);
}

// ---- 回顾：最近几天摘要 + 入口 ----
const recapDays = ref<DistillDay[]>([]);
const recapLoaded = ref(false);
async function loadRecap() {
  recapDays.value = await getDistillTimelineOnce(14);
  recapLoaded.value = true;
}
/** 秒 → "1.2h"；0 返回空串。 */
function fmtHours(sec: number): string {
  return sec > 0 ? `${(sec / 3600).toFixed(1)}h` : "";
}
const recapPreview = computed(() =>
  recapDays.value
    .filter((d) => d.status === "ok")
    .slice(0, 3)
    .map((d) => {
      const insights = d.items.filter((i) => i.kind === "insight").length;
      const events = d.items.filter((i) => i.kind === "event").length;
      const secs = Object.values(d.stats.app_seconds ?? {}).reduce((a, b) => a + b, 0);
      return { day: d.day, text: `${insights + events} 条洞察 · ${fmtHours(secs)} 专注` };
    }),
);

// ---- 订阅 ----
let unFeed: (() => void) | null = null;
let unWidgets: (() => void) | null = null;
let unApprovals: (() => void) | null = null;
let unRecapOpen: (() => void) | null = null;

onMounted(async () => {
  const r = await getFeedOnce();
  items.value = r.items;
  stats.value = r.stats;
  runningTasks.value = r.running_tasks ?? [];
  loaded.value = true;
  const w = await getWidgetsOnce();
  widgets.value = w.widgets;
  unApprovals = onPendingConfirms((l) => (approvals.value = l));
  unFeed = await onFeed((r2) => {
    items.value = r2.items;
    stats.value = r2.stats;
    runningTasks.value = r2.running_tasks ?? [];
  });
  unWidgets = await onWidgets((w2) => {
    widgets.value = w2.widgets;
  });
  unRecapOpen = await onRecapOpen(() => {
    void loadRecap();
  });
  void recapCheck().catch(() => {});
  void loadRecap();
});
onUnmounted(() => {
  unFeed?.();
  unWidgets?.();
  unApprovals?.();
  unRecapOpen?.();
});
</script>

<template>
  <aside class="info-panel">
    <!-- AI 此刻：状态数字 + 进行中任务 -->
    <section class="ip-card">
      <div class="ip-title"><span class="ip-dot" />此刻</div>
      <div v-if="overview.length" class="ip-chips">
        <span v-for="o in overview" :key="o.key" class="ip-chip">
          <strong class="yb-num">{{ o.n }}</strong>{{ o.label }}
        </span>
      </div>
      <div v-if="runningTasks.length" class="ip-runs">
        <button v-for="t in runningTasks" :key="t.id" class="ip-run" @click="openTasks">
          <span class="run-dot" />
          <span class="ip-run-main">
            <strong>{{ t.label }}</strong>
            <span>{{ t.prompt }}</span>
          </span>
          <span class="ip-run-time">{{ elapsedSince(t.created_at) }}</span>
        </button>
      </div>
      <div v-if="quiet" class="ip-quiet">此刻很清净，随时叫我</div>
    </section>

    <!-- 插件入口：一瞥行，点开全面板 -->
    <section v-if="widgets.length" class="ip-card">
      <div class="ip-title"><YbIcon name="plug" :size="12" />插件</div>
      <button v-for="w in widgets" :key="w.panel" class="ip-widget" :disabled="!w.open" @click="openWidget(w)">
        <span>{{ w.title }}</span>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M9 6l6 6-6 6" />
        </svg>
      </button>
    </section>

    <!-- 需要你决定：琥珀强调，有才显 -->
    <section v-if="approvals.length" class="ip-card ip-decide">
      <div class="ip-title">需要你决定 <span class="ip-count">{{ approvals.length }}</span></div>
      <button v-for="p in approvals" :key="p.id" class="ip-ap" @click="emit('chat', `批准还是拒绝：${p.label || p.skill}？`)">
        <span class="ip-ap-main">
          <strong>{{ p.label || p.skill }}</strong>
          <span>{{ p.desc || p.skill }}</span>
        </span>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M9 6l6 6-6 6" />
        </svg>
      </button>
    </section>

    <!-- 动态：最近几条，点击带上下文进对话 -->
    <section class="ip-card ip-feed">
      <div class="ip-title">
        <YbIcon name="inbox" :size="12" />动态
        <span v-if="unreadCount > 0" class="ip-count">{{ unreadCount }}</span>
      </div>
      <div v-if="feedPreview.length" class="ip-feed-list">
        <button v-for="it in feedPreview" :key="it.id" class="ip-feed-row" @click="openInChat(it)">
          <YbIcon class="ip-feed-ic" :class="`ic-${kindIcon(it)}`" :name="kindIcon(it)" :size="12" />
          <span class="ip-feed-text" :class="{ unread: it.read === 0 }">{{ it.text }}</span>
          <span class="ip-feed-time">{{ itemTime(it.ts) }}</span>
        </button>
      </div>
      <div v-else-if="loaded" class="ip-quiet">还没有动态</div>
    </section>

    <!-- 回顾：最近几天摘要 -->
    <section class="ip-card">
      <div class="ip-title"><YbIcon name="sparkle" :size="12" />回顾</div>
      <div v-if="recapPreview.length" class="ip-recap-list">
        <button v-for="d in recapPreview" :key="d.day" class="ip-recap" @click="emit('chat', `看看 ${d.day} 那天的回顾`)">
          <span class="ip-recap-day">{{ d.day }}</span>
          <span class="ip-recap-text">{{ d.text }}</span>
        </button>
      </div>
      <div v-else class="ip-quiet">{{ recapLoaded ? "还没有回顾" : "回顾整理中…" }}</div>
    </section>
  </aside>
</template>

<style scoped>
.info-panel {
  width: 300px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: var(--yb-space-3);
  padding: 0 var(--yb-space-4) var(--yb-space-4);
  overflow-y: auto;
  scrollbar-width: thin;
  border-left: 1px solid var(--yb-border-base);
  background: var(--yb-content-bg);
}

/* 卡：细边 + 圆角 + 微阴影（与主屏 widget 同语言） */
.ip-card {
  display: flex;
  flex-direction: column;
  gap: var(--yb-space-2);
  padding: 10px 12px;
  border: 1px solid var(--yb-card-border);
  border-radius: var(--yb-card-radius);
  background: var(--yb-card-bg);
  box-shadow: var(--yb-shadow-1);
}
.ip-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--yb-fs-sm);
  font-weight: var(--yb-fw-bold);
  color: var(--yb-text-dim);
  letter-spacing: 0.02em;
}
.ip-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--yb-accent);
  box-shadow: 0 0 0 3px var(--yb-accent-soft);
}
.ip-count {
  margin-left: auto;
  padding: 0 7px;
  border-radius: var(--yb-radius-pill);
  background: var(--yb-btn-neutral);
  font-size: var(--yb-fs-sm);
}
.ip-quiet {
  font-size: var(--yb-fs-md);
  color: var(--yb-text-faint);
  line-height: 1.4;
}

/* 此刻 */
.ip-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.ip-chip {
  display: inline-flex;
  align-items: baseline;
  gap: 3px;
  padding: 2px 8px;
  border: 1px solid var(--yb-border-base);
  border-radius: var(--yb-radius-pill);
  background: var(--yb-surface-2);
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-dim);
}
.ip-chip strong {
  font-size: var(--yb-fs-md);
  font-weight: var(--yb-fw-bold);
  color: var(--yb-accent-deep);
}
.ip-runs {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.ip-run {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 6px 8px;
  border: none;
  border-radius: var(--yb-radius-xs);
  background: transparent;
  color: var(--yb-text);
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  transition: background var(--yb-dur-fast) var(--yb-ease-out);
}
.ip-run:hover {
  background: var(--yb-row-hover);
}
.run-dot {
  flex-shrink: 0;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--yb-accent);
}
.ip-run-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.ip-run-main strong {
  font-size: var(--yb-fs-md);
  font-weight: var(--yb-fw-medium);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ip-run-main span {
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-dim);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ip-run-time {
  flex-shrink: 0;
  font-size: var(--yb-fs-xs);
  color: var(--yb-text-faint);
}

/* 插件入口行 */
.ip-widget,
.ip-ap {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  padding: 7px 10px;
  border: 1px dashed var(--yb-card-border);
  border-radius: var(--yb-radius-sm);
  background: var(--yb-surface-2);
  color: var(--yb-text);
  font-family: inherit;
  font-size: var(--yb-fs-md);
  text-align: left;
  cursor: pointer;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.ip-widget:hover:not(:disabled) {
  border-color: var(--yb-accent);
  border-style: solid;
}
.ip-widget:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}
.ip-widget svg,
.ip-ap svg {
  flex-shrink: 0;
  width: 12px;
  height: 12px;
  color: var(--yb-text-faint);
  transition: transform var(--yb-dur-fast) var(--yb-ease-out);
}
.ip-widget:hover:not(:disabled) svg,
.ip-ap:hover svg {
  color: var(--yb-accent);
  transform: translateX(2px);
}

/* 待批准（琥珀） */
.ip-decide {
  border-color: var(--yb-intent-pending);
}
.ip-decide .ip-title {
  color: var(--yb-intent-pending-ink);
}
.ip-ap {
  border-style: solid;
  border-color: var(--yb-intent-pending-soft);
  background: var(--yb-intent-pending-soft);
}
.ip-ap:hover {
  border-color: var(--yb-intent-pending);
}
.ip-ap-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.ip-ap-main strong {
  font-size: var(--yb-fs-md);
  font-weight: var(--yb-fw-medium);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ip-ap-main span {
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-dim);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 动态列表 */
.ip-feed-list,
.ip-recap-list {
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.ip-feed-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 5px 8px;
  border: none;
  border-radius: var(--yb-radius-xs);
  background: transparent;
  color: var(--yb-text);
  font-family: inherit;
  font-size: var(--yb-fs-md);
  text-align: left;
  cursor: pointer;
  transition: background var(--yb-dur-fast) var(--yb-ease-out);
}
.ip-feed-row:hover {
  background: var(--yb-row-hover);
}
.ip-feed-ic {
  flex-shrink: 0;
  color: var(--yb-text-faint);
}
.ip-feed-ic.ic-clock {
  color: var(--yb-accent);
}
.ip-feed-ic.ic-check {
  color: var(--yb-intent-ok);
}
.ip-feed-ic.ic-x {
  color: var(--yb-danger);
}
.ip-feed-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--yb-fs-md);
}
.ip-feed-text.unread {
  font-weight: var(--yb-fw-medium);
}
.ip-feed-time {
  flex-shrink: 0;
  font-size: var(--yb-fs-xs);
  color: var(--yb-text-faint);
}

/* 回顾 */
.ip-recap {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 6px 8px;
  border: none;
  border-radius: var(--yb-radius-xs);
  background: transparent;
  color: var(--yb-text);
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  transition: background var(--yb-dur-fast) var(--yb-ease-out);
}
.ip-recap:hover {
  background: var(--yb-row-hover);
}
.ip-recap-day {
  flex-shrink: 0;
  font-size: var(--yb-fs-sm);
  font-weight: var(--yb-fw-bold);
  color: var(--yb-accent-deep);
}
.ip-recap-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-dim);
}
</style>
