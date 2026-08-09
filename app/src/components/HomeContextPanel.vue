<script setup lang="ts">
/* HomeContextPanel — 主屏右栏：「AI 进程」——此刻 / 需要你决定 / 动态 / 回顾 / 插件入口。
 * 数据与 HomeContextBar 同源（feed/widgets/待批/回顾），竖排面板形式（三栏工作台右栏）。
 * 点击动态/回顾 → 带上下文进对话。
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

const emit = defineEmits<{ chat: [draft: string] }>();

// ---- 此刻 ----
const stats = ref<FeedStats>({ pending_reminders: 0, running_tasks: 0, done_24h: 0, unread: 0, ignored: 0 });
const runningTasks = ref<RunningTask[]>([]);
const loaded = ref(false);

const nowText = computed(() => {
  if (runningTasks.value.length) return runningTasks.value.map((t) => t.label).join("、");
  return null;
});
const nowChips = computed(() => {
  const o: { key: string; n: number; label: string }[] = [];
  if (stats.value.done_24h > 0) o.push({ key: "done", n: stats.value.done_24h, label: "今日完成" });
  if (stats.value.pending_reminders > 0) o.push({ key: "rem", n: stats.value.pending_reminders, label: "待提醒" });
  return o;
});
function elapsedSince(ts: number): string {
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (seconds < 60) return "刚开始";
  if (seconds < 3600) return `已运行 ${Math.floor(seconds / 60)} 分钟`;
  return `已运行 ${Math.floor(seconds / 3600)} 小时`;
}
function openTasks() {
  void panelAction("agents.task_list", {}, undefined, "panel:agents").catch(() => {});
}

// ---- 插件入口 ----
const widgets = ref<WidgetPayload[]>([]);
function openWidget(w: WidgetPayload) {
  if (!w.open) return;
  const pid = w.panel.split(":")[0];
  void panelAction(w.open, {}, undefined, `panel:${pid}`).catch(() => {});
}

// ---- 待批准 ----
const approvals = ref<PendingConfirm[]>([]);

// ---- 动态：按天分组折叠（今天/昨天/更早，macOS 通知中心语言）----
const items = ref<FeedItem[]>([]);
const collapsed = ref<Set<string>>(new Set(["yesterday", "earlier"])); // 默认折叠非今天
function toggleGroup(key: string) {
  const s = new Set(collapsed.value);
  if (s.has(key)) s.delete(key);
  else s.add(key);
  collapsed.value = s;
}
const feedGroups = computed(() => {
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000;
  const startOfYesterday = startOfToday - 86400;
  const buckets: { key: string; label: string; items: FeedItem[] }[] = [
    { key: "today", label: "今天", items: [] },
    { key: "yesterday", label: "昨天", items: [] },
    { key: "earlier", label: "更早", items: [] },
  ];
  for (const it of [...items.value].sort((a, b) => b.ts - a.ts).slice(0, 14)) {
    if (it.ts >= startOfToday) buckets[0].items.push(it);
    else if (it.ts >= startOfYesterday) buckets[1].items.push(it);
    else buckets[2].items.push(it);
  }
  return buckets.filter((b) => b.items.length > 0);
});
const unreadCount = computed(() => items.value.filter((it) => it.read === 0).length);

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

// ---- 回顾 ----
const recapDays = ref<DistillDay[]>([]);
const recapLoaded = ref(false);
async function loadRecap() {
  recapDays.value = await getDistillTimelineOnce(14);
  recapLoaded.value = true;
}
const recapPreview = computed(() =>
  recapDays.value.filter((d) => d.status === "ok").slice(0, 3).map((d) => {
    const n = d.items.filter((i) => i.kind === "insight" || i.kind === "event").length;
    return { day: d.day, n };
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
  <aside class="ctx-panel">
    <!-- AI 此刻 -->
    <section class="cp-block">
      <div class="cp-title"><span class="cp-dot" />此刻</div>
      <div v-if="nowText" class="cp-now">
        <b>{{ nowText }}</b>
      </div>
      <div v-if="nowChips.length" class="cp-chips">
        <span v-for="o in nowChips" :key="o.key" class="cp-chip">
          <strong class="yb-num">{{ o.n }}</strong>{{ o.label }}
        </span>
      </div>
      <div v-if="runningTasks.length" class="cp-runs">
        <button v-for="t in runningTasks" :key="t.id" class="cp-run" @click="openTasks">
          <span class="cp-run-dot" />
          <span class="cp-run-main">
            <strong>{{ t.label }}</strong>
            <span>{{ elapsedSince(t.created_at) }}</span>
          </span>
        </button>
      </div>
      <div v-if="loaded && !nowText && !nowChips.length && !runningTasks.length" class="cp-quiet">
        此刻很清净，随时叫我
      </div>
    </section>

    <!-- 需要你决定 -->
    <section v-if="approvals.length" class="cp-block cp-decide">
      <div class="cp-title"><YbIcon name="lock" :size="12" />需要你决定 <span class="cp-count">{{ approvals.length }}</span></div>
      <button v-for="p in approvals" :key="p.id" class="cp-ap" @click="emit('chat', `批准还是拒绝：${p.label || p.skill}？`)">
        <span class="cp-ap-main">
          <strong>{{ p.label || p.skill }}</strong>
          <span>{{ p.desc || p.skill }}</span>
        </span>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6" /></svg>
      </button>
    </section>

    <!-- 动态：按天分组，组头可折叠 -->
    <section class="cp-block cp-feed">
      <div class="cp-title"><YbIcon name="inbox" :size="12" />动态 <span v-if="unreadCount" class="cp-count">{{ unreadCount }}</span></div>
      <div v-if="feedGroups.length" class="cp-feed-list">
        <div v-for="g in feedGroups" :key="g.key" class="cp-feed-group">
          <button class="cp-feed-head" @click="toggleGroup(g.key)">
            <svg class="cp-chev" :class="{ on: !collapsed.has(g.key) }" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6" /></svg>
            {{ g.label }}
            <span class="cp-gcount yb-num">{{ g.items.length }}</span>
          </button>
          <template v-if="!collapsed.has(g.key)">
            <button v-for="it in g.items" :key="it.id" class="cp-feed-row" @click="openInChat(it)">
              <YbIcon class="cp-feed-ic" :class="`ic-${kindIcon(it)}`" :name="kindIcon(it)" :size="12" />
              <span class="cp-feed-text" :class="{ unread: it.read === 0 }">{{ it.text }}</span>
              <span class="cp-feed-time">{{ itemTime(it.ts) }}</span>
            </button>
          </template>
        </div>
      </div>
      <div v-else-if="loaded" class="cp-quiet">还没有动态</div>
    </section>

    <!-- 回顾 -->
    <section class="cp-block">
      <div class="cp-title"><YbIcon name="sparkle" :size="12" />回顾</div>
      <div v-if="recapPreview.length" class="cp-recap-list">
        <button v-for="d in recapPreview" :key="d.day" class="cp-recap" @click="emit('chat', `看看 ${d.day} 那天的回顾`)">
          <span class="cp-recap-day">{{ d.day }}</span>
          <span class="cp-recap-text">{{ d.n }} 条洞察</span>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6" /></svg>
        </button>
      </div>
      <div v-else class="cp-quiet">{{ recapLoaded ? "还没有回顾" : "回顾整理中…" }}</div>
    </section>

    <!-- 插件入口 -->
    <section v-if="widgets.length" class="cp-block">
      <div class="cp-title"><YbIcon name="plug" :size="12" />插件</div>
      <button v-for="w in widgets" :key="w.panel" class="cp-widget" :disabled="!w.open" @click="openWidget(w)">
        <span>{{ w.title }}</span>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6" /></svg>
      </button>
    </section>
  </aside>
</template>

<style scoped>
.ctx-panel {
  width: 260px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 12px;
  overflow-y: auto;
  scrollbar-width: thin;
  border-left: 1px solid var(--yb-border-base);
  background:
    radial-gradient(80% 40% at 100% 0%, rgba(var(--yb-c-sky-rgb), 0.05), transparent 70%),
    var(--yb-content-bg);
}

/* 块：与对话一体——无卡片，仅分组标题 + 内容 */
.cp-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--yb-border-base);
}
.cp-block:last-child {
  border-bottom: none;
}
.cp-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--yb-fs-xs);
  font-weight: var(--yb-fw-bold);
  color: var(--yb-text-faint);
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.cp-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--yb-accent);
  box-shadow: 0 0 0 3px var(--yb-accent-soft);
}
.cp-count {
  margin-left: auto;
  padding: 0 7px;
  border-radius: var(--yb-radius-pill);
  background: var(--yb-btn-neutral);
  font-size: var(--yb-fs-sm);
  text-transform: none;
  letter-spacing: 0;
}
.cp-quiet {
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-faint);
  line-height: 1.4;
}

/* 此刻 */
.cp-now {
  font-size: var(--yb-fs-md);
  font-weight: var(--yb-fw-bold);
  color: var(--yb-accent-deep);
  line-height: 1.3;
}
.cp-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}
.cp-chip {
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
.cp-chip strong {
  font-size: var(--yb-fs-md);
  font-weight: var(--yb-fw-bold);
  color: var(--yb-accent-deep);
}
.cp-runs {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.cp-run {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 5px 7px;
  border: none;
  border-radius: var(--yb-radius-xs);
  background: transparent;
  color: var(--yb-text);
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  transition: background var(--yb-dur-fast) var(--yb-ease-out);
}
.cp-run:hover {
  background: var(--yb-row-hover);
}
.cp-run-dot {
  flex-shrink: 0;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--yb-accent);
}
.cp-run-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.cp-run-main strong {
  font-size: var(--yb-fs-md);
  font-weight: var(--yb-fw-medium);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cp-run-main span {
  font-size: var(--yb-fs-xs);
  color: var(--yb-text-faint);
}

/* 待批准（琥珀） */
.cp-decide .cp-title {
  color: var(--yb-intent-pending-ink);
}
.cp-ap {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  padding: 6px 9px;
  border: 1px solid var(--yb-intent-pending-soft);
  border-radius: var(--yb-radius-sm);
  background: var(--yb-intent-pending-soft);
  color: var(--yb-text);
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.cp-ap:hover {
  border-color: var(--yb-intent-pending);
}
.cp-ap svg {
  flex-shrink: 0;
  width: 11px;
  height: 11px;
  color: var(--yb-intent-pending);
}
.cp-ap-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.cp-ap-main strong {
  font-size: var(--yb-fs-md);
  font-weight: var(--yb-fw-medium);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cp-ap-main span {
  font-size: var(--yb-fs-xs);
  color: var(--yb-text-dim);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 动态 */
.cp-feed-list,
.cp-recap-list {
  display: flex;
  flex-direction: column;
  gap: 1px;
}
/* 动态分组 */
.cp-feed-group {
  display: flex;
  flex-direction: column;
}
.cp-feed-head {
  display: flex;
  align-items: center;
  gap: 5px;
  width: 100%;
  padding: 3px 7px;
  border: none;
  border-radius: var(--yb-radius-xs);
  background: transparent;
  color: var(--yb-text-faint);
  font-size: var(--yb-fs-xs);
  font-weight: var(--yb-fw-bold);
  letter-spacing: 0.03em;
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  transition: color var(--yb-dur-fast) var(--yb-ease-out);
}
.cp-feed-head:hover {
  color: var(--yb-text);
}
.cp-chev {
  flex-shrink: 0;
  width: 10px;
  height: 10px;
  transition: transform var(--yb-dur-fast) var(--yb-ease-out);
}
.cp-chev.on {
  transform: rotate(180deg);
}
.cp-gcount {
  margin-left: auto;
  font-size: var(--yb-fs-xs);
  color: var(--yb-text-faint);
}
.cp-feed-row {
  display: flex;
  align-items: center;
  gap: 7px;
  width: 100%;
  padding: 5px 7px;
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
.cp-feed-row:hover {
  background: var(--yb-row-hover);
}
.cp-feed-ic {
  flex-shrink: 0;
  color: var(--yb-text-faint);
}
.cp-feed-ic.ic-clock { color: var(--yb-accent); }
.cp-feed-ic.ic-check { color: var(--yb-intent-ok); }
.cp-feed-ic.ic-x { color: var(--yb-danger); }
.cp-feed-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--yb-fs-md);
}
.cp-feed-text.unread {
  font-weight: var(--yb-fw-medium);
}
.cp-feed-time {
  flex-shrink: 0;
  font-size: var(--yb-fs-xs);
  color: var(--yb-text-faint);
}

/* 回顾 */
.cp-recap {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 5px 7px;
  border: none;
  border-radius: var(--yb-radius-xs);
  background: transparent;
  color: var(--yb-text);
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.cp-recap:hover {
  background: var(--yb-accent-soft);
}
.cp-recap:hover svg {
  color: var(--yb-accent);
  transform: translateX(2px);
}
.cp-recap svg {
  flex-shrink: 0;
  width: 11px;
  height: 11px;
  color: var(--yb-text-faint);
  transition: transform var(--yb-dur-fast) var(--yb-ease-out), color var(--yb-dur-fast) var(--yb-ease-out);
}
.cp-recap-day {
  flex-shrink: 0;
  font-size: var(--yb-fs-sm);
  font-weight: var(--yb-fw-bold);
  color: var(--yb-accent-deep);
}
.cp-recap-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-dim);
}

/* 插件入口 */
.cp-widget {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  padding: 6px 9px;
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
.cp-widget:hover:not(:disabled) {
  border-color: var(--yb-accent);
  border-style: solid;
}
.cp-widget:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.cp-widget svg {
  flex-shrink: 0;
  width: 11px;
  height: 11px;
  color: var(--yb-text-faint);
}
</style>
