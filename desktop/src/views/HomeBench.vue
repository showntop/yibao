<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import HomeWidget from "./HomeWidget.vue";
import {
  getFeedOnce,
  getWidgetsOnce,
  onFeed,
  onWidgets,
  panelAction,
  type RunningTask,
  type WidgetPayload,
} from "../lib/brain";
import { benchFace } from "../lib/home/home-bench-face.ts";
import { runPanelAction } from "../lib/home/home-panel-run.ts";
import { setDeskOrigin } from "../lib/home/home-desk-presence.ts";

const coding = ref<unknown>(null);
const widgets = ref<WidgetPayload[]>([]);
const feed = ref<RunningTask[]>([]);
const face = computed(() => benchFace({
  coding: coding.value,
  widgets: widgets.value,
  feed: feed.value,
}));

let timer: ReturnType<typeof setInterval> | null = null;
let unWidgets: (() => void) | null = null;
let unFeed: (() => void) | null = null;

async function refreshCoding() {
  coding.value = await runPanelAction("coding.sessions", {}, "panel:coding");
}

function openBench(event: MouseEvent) {
  if (!face.value) return;
  setDeskOrigin(event.currentTarget as Element);
  void panelAction(face.value.method, face.value.params, undefined, face.value.surface).catch(() => {});
}

onMounted(async () => {
  const [cards, live] = await Promise.all([
    getWidgetsOnce().catch(() => ({ widgets: [] as WidgetPayload[] })),
    getFeedOnce().catch(() => ({ running_tasks: [] as RunningTask[] })),
  ]);
  widgets.value = cards.widgets ?? [];
  feed.value = live.running_tasks ?? [];
  await refreshCoding();
  timer = setInterval(() => { void refreshCoding(); }, 12_000);
  try {
    unWidgets = await onWidgets((payload) => { widgets.value = payload?.widgets ?? []; });
  } catch { /* 智能体不在线就只看编码 */ }
  try {
    unFeed = await onFeed((next) => { feed.value = next.running_tasks ?? []; });
  } catch { /* 动态通道没有时保留一次拉取 */ }
});

onUnmounted(() => {
  if (timer) clearInterval(timer);
  unWidgets?.();
  unFeed?.();
});
</script>

<template>
  <aside class="bench">
    <HomeWidget v-if="face" id="bench" aria-label="工位">
      <button class="job" type="button" @click="openBench">
        <span class="kicker">工位 · {{ face.who }}</span>
        <span>{{ face.label }}</span>
        <small>{{ face.state }}</small>
      </button>
    </HomeWidget>
  </aside>
</template>

<style scoped>
.bench { display: contents; }

.job {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 3px;
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
  cursor: pointer;
}

.kicker,
.job small {
  color: var(--yb-paper-ink-dim);
  font-size: 10px;
  letter-spacing: 0.04em;
}

.job > span:nth-child(2) {
  font-size: 12px;
  font-weight: var(--yb-fw-medium);
  line-height: 1.4;
}
</style>
