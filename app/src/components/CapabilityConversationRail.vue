<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import YbIcon from "./YbIcon.vue";
import {
  getConversationHistory,
  onBrainEvent,
  type BrainEvent,
  type ConversationHistoryMessage,
} from "../lib/brain";
import { procLabel, procResultSuffix } from "../lib/proc";

export interface CapabilityRailSurface {
  panel: string;
  title: string;
  plugin: string;
  objectTitle?: string;
}

type RailKind = "user" | "assistant" | "activity";
type RailStatus = "running" | "done" | "failed";
type RailFilter = "all" | "conversation" | "activity";
interface RailEntry {
  id: number;
  kind: RailKind;
  text: string;
  detail?: string;
  label?: string;
  status?: RailStatus;
  actionId?: string;
  expanded?: boolean;
  restored?: boolean;
}

const props = defineProps<{
  surface: CapabilityRailSurface | null;
  active: boolean;
}>();
const emit = defineEmits<{ close: []; focus: [] }>();

const entries = ref<RailEntry[]>([]);
const filter = ref<RailFilter>("all");
const timelineEl = ref<HTMLElement | null>(null);
const nextId = ref(0);
const hydrated = ref(false);
const streamingId = ref<number | null>(null);
const activityByAction = new Map<string, number>();
let unlisten: (() => void) | null = null;

const visibleEntries = computed(() => {
  if (filter.value === "conversation") return entries.value.filter((entry) => entry.kind !== "activity");
  if (filter.value === "activity") return entries.value.filter((entry) => entry.kind === "activity");
  return entries.value;
});
const conversationCount = computed(() => entries.value.filter((entry) => entry.kind !== "activity").length);
const activityCount = computed(() => entries.value.filter((entry) => entry.kind === "activity").length);

function push(entry: Omit<RailEntry, "id">): RailEntry {
  const item: RailEntry = { ...entry, id: ++nextId.value };
  entries.value.push(item);
  if (entries.value.length > 120) entries.value.splice(0, entries.value.length - 120);
  return item;
}

function historyLabel(message: ConversationHistoryMessage): string {
  const call = message.tool_calls?.[0]?.function?.name;
  if (call) return call.replace(/_/g, " · ");
  return message.surface && message.surface !== "pet"
    ? `${message.surface.split(":", 2)[1] || message.surface} · 协作`
    : "译宝能力";
}

function hydrate(messages: ConversationHistoryMessage[]) {
  const restored: RailEntry[] = [];
  const toolRows = new Map<string, RailEntry>();
  for (const message of messages) {
    const text = message.content?.trim();
    if (message.role === "user" && text) {
      restored.push({ id: ++nextId.value, kind: "user", text, restored: true });
      continue;
    }
    if (message.role === "assistant") {
      if (message.tool_calls?.length) {
        for (const call of message.tool_calls) {
          const item: RailEntry = {
            id: ++nextId.value,
            kind: "activity",
            text: "已调用能力",
            label: call.function?.name?.replace(/_/g, " · ") || historyLabel(message),
            status: "done",
            actionId: call.id,
            restored: true,
          };
          restored.push(item);
          if (call.id) toolRows.set(call.id, item);
        }
      }
      if (text) restored.push({ id: ++nextId.value, kind: "assistant", text, restored: true });
      continue;
    }
    if (message.role === "tool") {
      const row = message.tool_call_id ? toolRows.get(message.tool_call_id) : undefined;
      if (row) {
        row.text = "能力调用已完成";
        if (text) row.detail = `结果：${text.slice(0, 300)}`;
      } else {
        restored.push({
          id: ++nextId.value,
          kind: "activity",
          text: "能力调用已完成",
          label: historyLabel(message),
          detail: text ? `结果：${text.slice(0, 300)}` : undefined,
          status: "done",
          restored: true,
        });
      }
    }
  }
  entries.value = restored.slice(-80);
  hydrated.value = true;
}

function onEvent(event: BrainEvent) {
  switch (event.kind) {
    case "action_proposed": {
      if (!event.action || event.action.skill_id === "use_plugin") break;
      const row = push({
        kind: "activity",
        text: event.action.description || "正在调用能力",
        label: procLabel(event.action),
        status: "running",
        actionId: event.action.id,
      });
      if (event.action.id) activityByAction.set(event.action.id, row.id);
      break;
    }
    case "action_result": {
      const rowId = event.action?.id ? activityByAction.get(event.action.id) : undefined;
      const row = rowId ? entries.value.find((entry) => entry.id === rowId) : undefined;
      if (row) {
        const ok = event.result?.success !== false;
        row.status = ok ? "done" : "failed";
        row.text = `${procLabel(event.action)}${procResultSuffix(event.result)}`;
        row.detail = ok
          ? String(event.result?.data?.human ?? "已完成").slice(0, 300)
          : String(event.result?.error ?? "调用失败").slice(0, 300);
        if (event.action?.id) activityByAction.delete(event.action.id);
      }
      break;
    }
    case "final_reply_chunk": {
      let row = streamingId.value === null
        ? undefined
        : entries.value.find((entry) => entry.id === streamingId.value);
      if (!row) {
        row = push({ kind: "assistant", text: event.text ?? "" });
        streamingId.value = row.id;
      } else {
        row.text += event.text ?? "";
      }
      break;
    }
    case "final_reply": {
      const row = streamingId.value === null
        ? undefined
        : entries.value.find((entry) => entry.id === streamingId.value);
      if (row) row.text = event.text ?? row.text;
      else if (event.text) push({ kind: "assistant", text: event.text });
      streamingId.value = null;
      break;
    }
    case "listening_done":
      if (event.text) push({ kind: "user", text: event.text });
      break;
    case "panel":
      push({
        kind: "activity",
        text: "工作面已准备好，可随时展开或收起",
        label: event.payload?.title || event.payload?.panel || "插件工作面",
        status: "done",
      });
      break;
    case "error":
      push({ kind: "activity", text: event.text || "能力运行失败", label: "译宝", status: "failed" });
      break;
  }
}

watch(() => visibleEntries.value.length, () => {
  void nextTick(() => {
    if (timelineEl.value) timelineEl.value.scrollTop = timelineEl.value.scrollHeight;
  });
});
watch(() => props.active, (active) => {
  if (active) filter.value = "all";
});

onMounted(async () => {
  const qaMode = import.meta.env.DEV && new URLSearchParams(window.location.search).get("qa") === "capability";
  if (qaMode) {
    hydrate([
      { role: "user", content: "把最近关于 AI OS 的想法整理成一个选题", surface: "pet" },
      {
        role: "assistant",
        content: "",
        surface: "pet",
        tool_calls: [{ id: "qa-tool", function: { name: "zimeiti_list", arguments: "{}" } }],
      },
      { role: "tool", content: "已整理 5 个方向并按成熟度放入看板", tool_call_id: "qa-tool", surface: "pet" },
      { role: "assistant", content: "我整理了 5 个方向。工作面已经附着到当前任务，协作过程和结果都会留在这里。", surface: "pet" },
      { role: "user", content: "先把第三个推进到写作中", surface: "panel:zimeiti" },
      { role: "assistant", content: "已经移动好了。你可以继续说“基于这个找案例”，我会沿用当前对象。", surface: "panel:zimeiti" },
    ]);
    return;
  }
  try {
    hydrate(await getConversationHistory());
  } catch {
    hydrated.value = true;
  }
  unlisten = await onBrainEvent(onEvent);
});
onUnmounted(() => unlisten?.());
</script>

<template>
  <aside class="cap-rail" aria-label="当前任务协作记录">
    <header class="rail-head">
      <div>
        <span>当前任务</span>
        <strong>和译宝一起完成</strong>
      </div>
      <button type="button" title="回到完整对话" @click="emit('close')">
        <YbIcon name="chat" :size="14" />
      </button>
    </header>

    <div class="surface-context">
      <span class="context-icon"><YbIcon name="plug" :size="13" /></span>
      <div>
        <strong>{{ surface?.title || "能力工作面" }}</strong>
        <small>{{ surface?.objectTitle ? `在看：${surface.objectTitle}` : "对话、调用与结果在同一任务中" }}</small>
      </div>
      <button type="button" title="进入专注场" @click="emit('focus')"><YbIcon name="expand" :size="13" /></button>
    </div>

    <div class="rail-tabs" role="tablist" aria-label="协作记录筛选">
      <button :class="{ active: filter === 'all' }" type="button" @click="filter = 'all'">全部</button>
      <button :class="{ active: filter === 'conversation' }" type="button" @click="filter = 'conversation'">对话 <span>{{ conversationCount }}</span></button>
      <button :class="{ active: filter === 'activity' }" type="button" @click="filter = 'activity'">活动 <span>{{ activityCount }}</span></button>
    </div>

    <div ref="timelineEl" class="rail-timeline" aria-live="polite">
      <div v-if="!hydrated" class="rail-empty"><YbIcon name="spinner" :size="13" spin />正在恢复协作记录…</div>
      <div v-else-if="!visibleEntries.length" class="rail-empty">本次任务的对话与能力活动会留在这里</div>
      <template v-for="entry in visibleEntries" :key="entry.id">
        <div v-if="entry.kind === 'user'" class="rail-message user">{{ entry.text }}</div>
        <div v-else-if="entry.kind === 'assistant'" class="rail-message assistant">{{ entry.text }}</div>
        <button
          v-else
          class="rail-activity"
          :class="entry.status"
          type="button"
          :aria-expanded="entry.expanded"
          @click="entry.expanded = !entry.expanded"
        >
          <span class="activity-icon">
            <YbIcon :name="entry.status === 'running' ? 'spinner' : entry.status === 'failed' ? 'x' : 'check'" :size="11" :spin="entry.status === 'running'" />
          </span>
          <span class="activity-copy">
            <small>{{ entry.label || "译宝能力" }}</small>
            <strong>{{ entry.text }}</strong>
            <span v-if="entry.expanded && entry.detail">{{ entry.detail }}</span>
          </span>
        </button>
      </template>
    </div>
  </aside>
</template>

<style scoped>
.cap-rail {
  height: 100%;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--yb-border-base);
  background: rgba(255, 255, 255, 0.64);
}
.rail-head {
  height: 62px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 14px;
  border-bottom: 1px solid var(--yb-border-base);
}
.rail-head > div { min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.rail-head span { color: var(--yb-text-faint); font-size: var(--yb-fs-xs); }
.rail-head strong { overflow: hidden; color: var(--yb-text-strong); font-size: var(--yb-fs-lg); text-overflow: ellipsis; white-space: nowrap; }
.rail-head button,
.surface-context button {
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: var(--yb-radius-sm);
  background: transparent;
  color: var(--yb-text-dim);
  cursor: pointer;
}
.rail-head button:hover,
.surface-context button:hover { background: var(--yb-row-hover); color: var(--yb-accent); }
.surface-context {
  margin: 12px 12px 8px;
  padding: 9px;
  display: flex;
  align-items: center;
  gap: 8px;
  border: 1px solid rgba(var(--yb-c-sky-rgb), 0.2);
  border-radius: var(--yb-radius-md);
  background: var(--yb-accent-soft);
}
.context-icon { width: 27px; height: 27px; flex-shrink: 0; display: grid; place-items: center; border-radius: 8px; background: var(--yb-card-bg); color: var(--yb-accent); box-shadow: var(--yb-shadow-1); }
.surface-context > div { min-width: 0; flex: 1; display: flex; flex-direction: column; gap: 2px; }
.surface-context strong,
.surface-context small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.surface-context strong { font-size: var(--yb-fs-md); }
.surface-context small { color: var(--yb-text-dim); font-size: var(--yb-fs-xs); }
.rail-tabs { margin: 0 12px 8px; padding: 2px; display: flex; gap: 2px; border-radius: var(--yb-radius-sm); background: var(--yb-segment-track); }
.rail-tabs button { flex: 1; padding: 4px 2px; border: 0; border-radius: 7px; background: transparent; color: var(--yb-text-dim); font: inherit; font-size: var(--yb-fs-xs); cursor: pointer; }
.rail-tabs button.active { background: var(--yb-segment-thumb); color: var(--yb-text); box-shadow: var(--yb-shadow-1); }
.rail-tabs span { color: var(--yb-text-faint); font-size: 10px; }
.rail-timeline { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; padding: 4px 12px 18px; scrollbar-width: thin; }
.rail-empty { margin: auto; padding: 20px; display: flex; align-items: center; justify-content: center; gap: 6px; color: var(--yb-text-faint); font-size: var(--yb-fs-xs); text-align: center; }
.rail-message { max-width: 92%; padding: 7px 9px; border-radius: var(--yb-radius-md); font-size: var(--yb-fs-md); line-height: var(--yb-lh-base); white-space: pre-wrap; }
.rail-message.user { align-self: flex-end; background: var(--yb-user-bg); color: var(--yb-text); border-bottom-right-radius: 5px; }
.rail-message.assistant { align-self: flex-start; padding-left: 2px; color: var(--yb-text); }
.rail-activity { width: 100%; flex-shrink: 0; display: flex; align-items: flex-start; gap: 8px; padding: 8px; border: 1px solid var(--yb-card-border); border-radius: var(--yb-radius-sm); background: var(--yb-card-bg); color: var(--yb-text); font: inherit; text-align: left; cursor: pointer; }
.rail-activity:hover { border-color: rgba(var(--yb-c-sky-rgb), 0.28); background: var(--yb-row-hover); }
.activity-icon { width: 21px; height: 21px; flex-shrink: 0; display: grid; place-items: center; border-radius: 50%; background: var(--yb-success-soft); color: var(--yb-success); }
.rail-activity.running .activity-icon { background: var(--yb-accent-soft); color: var(--yb-accent); }
.rail-activity.failed .activity-icon { background: var(--yb-danger-soft); color: var(--yb-danger); }
.activity-copy { min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.activity-copy small { color: var(--yb-text-faint); font-size: 10px; }
.activity-copy strong { font-size: var(--yb-fs-xs); font-weight: var(--yb-fw-medium); line-height: var(--yb-lh-base); }
.activity-copy > span { margin-top: 4px; color: var(--yb-text-dim); font-size: 10px; line-height: 1.45; overflow-wrap: anywhere; }
</style>
