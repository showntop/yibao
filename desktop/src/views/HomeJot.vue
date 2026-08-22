<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import HomeWidget from "./HomeWidget.vue";
import { getWidgetsOnce, onWidgets, panelAction, type WidgetPayload } from "../lib/brain";
import { jotFace } from "../lib/home/home-jot-face.ts";
import { setDeskOrigin } from "../lib/home/home-desk-presence.ts";

const widgets = ref<WidgetPayload[]>([]);
let unWidgets: (() => void) | null = null;
const face = computed(() => jotFace(widgets.value));

function openNotes(event: MouseEvent) {
  if (!face.value) return;
  setDeskOrigin(event.currentTarget as Element);
  void panelAction(face.value.open, {}, undefined, "panel:notes").catch(() => {});
}

onMounted(async () => {
  const result = await getWidgetsOnce().catch(() => ({ widgets: [] as WidgetPayload[] }));
  widgets.value = result.widgets ?? [];
  try {
    unWidgets = await onWidgets((payload) => { widgets.value = payload?.widgets ?? []; });
  } catch { /* 闪念盘不在线就不上桌 */ }
});

onUnmounted(() => {
  unWidgets?.();
});
</script>

<template>
  <aside class="jot">
    <HomeWidget v-if="face" id="jot" aria-label="闪念">
      <button class="slip" type="button" @click="openNotes">
        <span class="kicker">闪念</span>
        <span>{{ face.text }}</span>
        <time v-if="face.when">{{ face.when }}</time>
      </button>
    </HomeWidget>
  </aside>
</template>

<style scoped>
.jot { display: contents; }

.slip {
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
.slip time {
  color: var(--yb-paper-ink-dim);
  font-size: 10px;
  letter-spacing: 0.04em;
}

.slip > span:nth-child(2) {
  font-size: 12px;
  font-weight: var(--yb-fw-medium);
  line-height: 1.4;
}
</style>
