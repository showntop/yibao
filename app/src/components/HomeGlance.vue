<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import HomeWidget from "./HomeWidget.vue";
import {
  canRememberSkill,
  getFeedOnce,
  getWidgetsOnce,
  onFeed,
  onPendingConfirms,
  onWidgets,
  rememberLabelForSkill,
  sendConfirmBatch,
  type FeedItem,
  type PendingConfirm,
  type RunningTask,
  type WidgetPayload,
} from "../lib/brain";
import { remindFaces, taskFaces } from "../lib/home-glance-faces";

const emit = defineEmits<{ chat: [draft: string] }>();
defineProps<{ only?: "need" | "tasks" | "remind" }>();

const approvals = ref<PendingConfirm[]>([]);
const tasks = ref<RunningTask[]>([]);
const feedReminders = ref<FeedItem[]>([]);
const widgets = ref<WidgetPayload[]>([]);
const deciding = ref<string | null>(null);
const remember = ref(false);
const decideError = ref("");
let unFeed: (() => void) | null = null;
let unApprovals: (() => void) | null = null;
let unWidgets: (() => void) | null = null;

const reminderRows = computed(() => {
  const hit = widgets.value.find((widget) => widget.panel.startsWith("reminders:"));
  const data = hit?.data;
  if (!data || typeof data !== "object" || !("rows" in data) || !Array.isArray(data.rows)) return [];
  return data.rows as Array<{ text?: unknown; when?: unknown }>;
});
const reminds = computed(() => remindFaces(reminderRows.value, feedReminders.value));
const running = computed(() => taskFaces(tasks.value, approvals.value));
const firstNeed = computed(() => approvals.value[0] ?? null);

async function decide(approved: boolean) {
  const card = firstNeed.value;
  if (!card || deciding.value) return;
  deciding.value = card.id;
  decideError.value = "";
  try {
    await sendConfirmBatch([{
      id: card.id,
      approved,
      remember: remember.value && canRememberSkill(card.skill),
    }]);
    remember.value = false;
  } catch {
    decideError.value = approved ? "没能同意" : "没能驳回";
  } finally {
    deciding.value = null;
  }
}

onMounted(async () => {
  unApprovals = onPendingConfirms((list) => { approvals.value = list; });
  try {
    const feed = await getFeedOnce();
    tasks.value = feed.running_tasks ?? [];
    feedReminders.value = (feed.items ?? []).filter((item) => item.kind === "reminder" && item.status !== "ignore").slice(0, 3);
  } catch { /* 大脑不在线时瓷片保持空态 */ }
  try {
    unFeed = await onFeed((feed) => {
      tasks.value = feed.running_tasks ?? [];
      feedReminders.value = (feed.items ?? []).filter((item) => item.kind === "reminder" && item.status !== "ignore").slice(0, 3);
    });
  } catch { /* 无事件通道时保留一次拉取 */ }
  try {
    const result = await getWidgetsOnce();
    widgets.value = result.widgets ?? [];
    unWidgets = await onWidgets((payload) => { widgets.value = payload?.widgets ?? []; });
  } catch { /* 提醒钟点来自插件，没有时退回 Feed */ }
});

onUnmounted(() => {
  unFeed?.();
  unApprovals?.();
  unWidgets?.();
});
</script>

<template>
  <aside class="glance">
    <HomeWidget v-if="(!only || only === 'need') && firstNeed" id="need" aria-label="需要你处理的确认">
      <article class="slip">
        <p class="kicker">需要你</p>
        <strong>{{ firstNeed.label || "待批准" }}</strong>
        <small v-if="firstNeed.desc">{{ firstNeed.desc }}</small>
        <p v-if="decideError" class="err" role="alert">{{ decideError }}</p>
        <label v-if="canRememberSkill(firstNeed.skill)" class="remember">
          <input v-model="remember" type="checkbox" />
          <span>{{ rememberLabelForSkill(firstNeed.skill) }}</span>
        </label>
        <div class="acts">
          <button type="button" class="reject" :disabled="Boolean(deciding)" @click="decide(false)">驳回</button>
          <button type="button" class="allow" :disabled="Boolean(deciding)" @click="decide(true)">
            {{ deciding ? "在办…" : "同意" }}
          </button>
        </div>
      </article>
    </HomeWidget>

    <HomeWidget v-if="(!only || only === 'tasks') && running.length" id="tasks" aria-label="正在进行的任务">
      <button
        v-for="task in running"
        :key="task.id"
        class="pulse"
        type="button"
        :data-stuck="task.stuck"
        @click="emit('chat', `查看正在进行的「${task.label}」`)"
      >
        <i aria-hidden="true" />
        <span>
          <strong>{{ task.label }}</strong>
          <small>{{ task.stuck }}</small>
        </span>
      </button>
    </HomeWidget>

    <HomeWidget
      v-if="(!only || only === 'remind') && reminds.length"
      id="remind"
      class="remind-card"
      :class="{ tight: reminds[0]?.tight }"
      aria-label="待办提醒"
    >
      <button
        v-for="row in reminds"
        :key="`${row.text}-${row.when}`"
        class="bell"
        type="button"
        :data-tight="row.tight ? '1' : '0'"
        @click="emit('chat', `查看提醒「${row.text}」`)"
      >
        <span>{{ row.text }}</span>
        <time v-if="row.when">{{ row.when }}</time>
      </button>
    </HomeWidget>
  </aside>
</template>

<style scoped>
.glance { display: contents; }

.slip,
.pulse,
.bell {
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: calc(100% - 16px);
  margin: 8px;
  padding: 8px 10px 10px;
  border: 0;
  border-radius: calc(var(--yb-widget-radius) - 8px);
  background: var(--yb-note-mute);
  box-shadow: var(--yb-press);
  color: var(--yb-paper-ink);
  font: inherit;
  text-align: left;
}

.kicker,
.slip small,
.pulse small,
.bell time {
  color: var(--yb-paper-ink-dim);
  font-size: 10px;
  letter-spacing: 0.04em;
}

.slip strong,
.pulse strong,
.bell span {
  font-size: 12px;
  font-weight: var(--yb-fw-medium);
  line-height: 1.35;
}

.slip small,
.pulse small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.err { margin: 0; color: var(--yb-danger); font-size: 10px; }

.remember {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 2px;
  color: var(--yb-paper-ink-dim);
  font-size: 10px;
}

.acts {
  display: flex;
  gap: 6px;
  margin-top: 6px;
}

.acts button {
  flex: 1;
  height: 28px;
  border: 0;
  border-radius: 8px;
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}

.reject {
  background: transparent;
  color: var(--yb-paper-ink-dim);
}

.allow {
  background: var(--yb-accent);
  color: #fff;
}

.acts button:disabled { opacity: 0.55; cursor: default; }

.pulse {
  flex-direction: row;
  align-items: center;
  gap: 8px;
  cursor: pointer;
}

.pulse i {
  flex: none;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--yb-state-work);
  box-shadow: 0 0 0 4px color-mix(in srgb, var(--yb-state-work) 22%, transparent);
}

.pulse[data-stuck="等你"] i {
  background: var(--yb-intent-pending-ink, var(--yb-accent));
  box-shadow: 0 0 0 4px color-mix(in srgb, var(--yb-accent) 22%, transparent);
}

.bell { cursor: pointer; gap: 2px; }
.bell time { font-variant-numeric: tabular-nums; }
.remind-card.tight .bell[data-tight="1"] {
  background: color-mix(in srgb, var(--yb-accent) 12%, var(--yb-note-mute));
}

.pulse:hover,
.bell:hover { filter: brightness(0.98); }
.pulse:focus-visible,
.bell:focus-visible,
.acts button:focus-visible {
  outline: 2px solid var(--yb-accent);
  outline-offset: 1px;
}

@media (prefers-reduced-motion: reduce) {
  .pulse i { box-shadow: none; }
}
</style>
