<script setup lang="ts">
/* HomeContextBar — 主屏「上下文带」：对话头顶的一体化信息流。
 *
 * 不是独立面板/卡片（左右列会与对话割裂），而是与对话同容器、同背景、同字号的
 * 一条「流」：AI 此刻 + 动态简讯 + 回顾入口 + 插件快捷。点击任一信息即带上下文进对话。
 * 与下方对话区以一根 hairline 分隔——视觉上「对话头顶的上下文」，一体感。
 */
import { computed, onMounted, onUnmounted, ref } from "vue";
import YbIcon from "./YbIcon.vue";
import {
  getFeedOnce,
  getWidgetsOnce,
  onFeed,
  onWidgets,
  onPendingConfirms,
  panelAction,
  markFeedRead,
  type FeedItem,
  type FeedStats,
  type RunningTask,
  type PendingConfirm,
  type WidgetPayload,
} from "../lib/brain";

const emit = defineEmits<{ chat: [draft: string] }>();

// ---- 此刻 ----
const stats = ref<FeedStats>({ pending_reminders: 0, running_tasks: 0, done_24h: 0, unread: 0, ignored: 0 });
const runningTasks = ref<RunningTask[]>([]);
const loaded = ref(false);

const nowText = computed(() => {
  if (runningTasks.value.length) {
    const names = runningTasks.value.map((t) => t.label).join("、");
    return `正在做 ${names}`;
  }
  return null;
});
const nowChips = computed(() => {
  const o: { key: string; n: number; label: string }[] = [];
  if (stats.value.done_24h > 0) o.push({ key: "done", n: stats.value.done_24h, label: "今日完成" });
  if (stats.value.pending_reminders > 0) o.push({ key: "rem", n: stats.value.pending_reminders, label: "待提醒" });
  return o;
});

// ---- 动态简讯：前 4 条横排 ----
const items = ref<FeedItem[]>([]);
const feedPreview = computed(() => [...items.value].sort((a, b) => b.ts - a.ts).slice(0, 4));
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

/** 点动态：乐观置已读 + 带上下文进对话。 */
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

// ---- 插件快捷（chips，点开全面板）----
const widgets = ref<WidgetPayload[]>([]);
function openWidget(w: WidgetPayload) {
  if (!w.open) return;
  const pid = w.panel.split(":")[0];
  void panelAction(w.open, {}, undefined, `panel:${pid}`).catch(() => {});
}

// ---- 待批准 ----
const approvals = ref<PendingConfirm[]>([]);

// ---- 订阅 ----
let unFeed: (() => void) | null = null;
let unWidgets: (() => void) | null = null;
let unApprovals: (() => void) | null = null;

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
});
onUnmounted(() => {
  unFeed?.();
  unWidgets?.();
  unApprovals?.();
});
</script>

<template>
  <aside class="ctx-bar">
    <!-- 行 1：AI 此刻 + 待批 + 插件快捷 -->
    <div class="ctx-row">
      <span class="ctx-dot" />
      <span v-if="nowText" class="ctx-now" :title="runningTasks.map((t) => t.prompt).join('\n')">
        {{ nowText }}
      </span>
      <span v-else class="ctx-now muted">{{ loaded ? "此刻很清净" : "…" }}</span>
      <span v-for="o in nowChips" :key="o.key" class="ctx-chip">
        <strong class="yb-num">{{ o.n }}</strong>{{ o.label }}
      </span>
      <span v-if="approvals.length" class="ctx-chip warn" :title="`${approvals.length} 项待你批准`">
        <YbIcon name="lock" :size="11" />{{ approvals.length }} 待批
      </span>

      <span class="ctx-spacer" />

      <button v-for="w in widgets" :key="w.panel" class="ctx-plug" :disabled="!w.open" @click="openWidget(w)">
        {{ w.title }}
      </button>
    </div>

    <!-- 行 2：动态简讯（横排，点击带上下文进对话）+ 回顾入口 -->
    <div class="ctx-row feed">
      <template v-if="feedPreview.length">
        <button
          v-for="it in feedPreview"
          :key="it.id"
          class="ctx-feed"
          @click="openInChat(it)"
        >
          <YbIcon class="ctx-feed-ic" :class="`ic-${kindIcon(it)}`" :name="kindIcon(it)" :size="11" />
          <span class="ctx-feed-text" :class="{ unread: it.read === 0 }">{{ it.text }}</span>
          <span class="ctx-feed-time">{{ itemTime(it.ts) }}</span>
        </button>
        <span v-if="unreadCount > 0" class="ctx-unread">{{ unreadCount }} 未读</span>
      </template>
      <span v-else-if="loaded" class="ctx-now muted">还没有动态</span>
      <span class="ctx-spacer" />
      <button class="ctx-recap" @click="emit('chat', '看看最近的回顾')">
        <YbIcon name="sparkle" :size="11" />回顾
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M9 6l6 6-6 6" />
        </svg>
      </button>
    </div>
  </aside>
</template>

<style scoped>
/* 上下文带：无卡片、无独立背景——与对话同容器，hairline 与对话区分，视觉一体 */
.ctx-bar {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px var(--yb-space-5) 10px;
  border-bottom: 1px solid var(--yb-border-base);
  background: var(--yb-content-bg);
  user-select: none;
}
.ctx-row {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  flex-wrap: nowrap;
}
.ctx-row.feed {
  gap: 4px;
}
/* 此刻脉动点 */
.ctx-dot {
  flex-shrink: 0;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--yb-accent);
  box-shadow: 0 0 0 3px var(--yb-accent-soft);
}
.ctx-now {
  font-size: var(--yb-fs-md);
  font-weight: var(--yb-fw-medium);
  color: var(--yb-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}
.ctx-now.muted {
  color: var(--yb-text-faint);
  font-weight: 400;
}
.ctx-chip {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 1px 8px;
  border-radius: var(--yb-radius-pill);
  background: var(--yb-surface-2);
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-dim);
  white-space: nowrap;
}
.ctx-chip strong {
  font-size: var(--yb-fs-md);
  font-weight: var(--yb-fw-bold);
  color: var(--yb-accent-deep);
}
.ctx-chip.warn {
  background: var(--yb-intent-pending-soft);
  color: var(--yb-intent-pending-ink);
}
.ctx-chip.warn svg {
  color: var(--yb-intent-pending);
}
.ctx-spacer {
  flex: 1;
}
/* 插件快捷 chip：与此刻 chips 同语言 */
.ctx-plug {
  display: inline-flex;
  align-items: center;
  padding: 2px 10px;
  border: 1px solid var(--yb-border-strong);
  border-radius: var(--yb-radius-pill);
  background: transparent;
  color: var(--yb-accent-deep);
  font-size: var(--yb-fs-sm);
  font-family: inherit;
  white-space: nowrap;
  cursor: pointer;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.ctx-plug:hover:not(:disabled) {
  border-color: var(--yb-accent);
  background: var(--yb-accent-soft);
}
.ctx-plug:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
/* 动态简讯行 */
.ctx-feed {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 240px;
  padding: 2px 7px;
  border: none;
  border-radius: var(--yb-radius-xs);
  background: transparent;
  color: var(--yb-text-dim);
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  transition: background var(--yb-dur-fast) var(--yb-ease-out);
}
.ctx-feed:hover {
  background: var(--yb-row-hover);
  color: var(--yb-text);
}
.ctx-feed-ic {
  flex-shrink: 0;
  color: var(--yb-text-faint);
}
.ctx-feed-ic.ic-clock {
  color: var(--yb-accent);
}
.ctx-feed-ic.ic-check {
  color: var(--yb-intent-ok);
}
.ctx-feed-ic.ic-x {
  color: var(--yb-danger);
}
.ctx-feed-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--yb-fs-sm);
}
.ctx-feed-text.unread {
  font-weight: var(--yb-fw-medium);
  color: var(--yb-text);
}
.ctx-feed-time {
  flex-shrink: 0;
  font-size: var(--yb-fs-xs);
  color: var(--yb-text-faint);
}
.ctx-unread {
  flex-shrink: 0;
  font-size: var(--yb-fs-xs);
  color: var(--yb-accent-deep);
}
/* 回顾入口 */
.ctx-recap {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
  padding: 2px 7px;
  border: none;
  border-radius: var(--yb-radius-xs);
  background: transparent;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-sm);
  font-family: inherit;
  cursor: pointer;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.ctx-recap svg {
  width: 10px;
  height: 10px;
}
.ctx-recap:hover {
  color: var(--yb-accent-deep);
  background: var(--yb-accent-soft);
}
</style>
