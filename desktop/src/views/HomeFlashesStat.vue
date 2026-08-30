<script setup lang="ts">
// 闪念统计卡（wb-prototype）：未处理大数字 + 去处理（直达 notes 面板）。
import { computed, onMounted, onUnmounted, ref } from "vue";
import HomeWidget from "./HomeWidget.vue";
import { getWidgetsOnce, onWidgets, panelAction, type WidgetPayload } from "../lib/brain";
import { flashesStatFace } from "../lib/home/home-jot-face.ts";
import { setDeskOrigin } from "../lib/home/home-desk-presence.ts";

const widgets = ref<WidgetPayload[]>([]);
let unWidgets: (() => void) | null = null;

const face = computed(() => flashesStatFace(widgets.value));

function openNotes(event: MouseEvent) {
  setDeskOrigin(event.currentTarget as Element);
  void panelAction(face.value.open, {}, undefined, "panel:notes").catch(() => {});
}

onMounted(async () => {
  const result = await getWidgetsOnce().catch(() => ({ widgets: [] as WidgetPayload[] }));
  widgets.value = result.widgets ?? [];
  try {
    unWidgets = await onWidgets((payload) => { widgets.value = payload?.widgets ?? []; });
  } catch { /* 闪念盘不在线就显示 0 */ }
});

onUnmounted(() => {
  unWidgets?.();
});
</script>

<template>
  <aside class="flash-stat">
    <HomeWidget id="flashes" aria-label="闪念">
      <button class="card" type="button" title="打开闪念盘" @click="openNotes">
        <header class="head">
          <span class="kicker">闪念</span>
        </header>
        <p class="big">{{ face.count }}</p>
        <p class="unit">条未处理</p>
        <span class="go">去处理 →</span>
      </button>
    </HomeWidget>
  </aside>
</template>

<style scoped>
.flash-stat { display: contents; }
.card {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  width: 100%;
  border: 0;
  background: none;
  padding: 0;
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.kicker {
  color: var(--yb-paper-ink-dim);
  font-size: var(--yb-fs-xs);
  font-weight: var(--yb-fw-medium);
  letter-spacing: 0.04em;
}
.big {
  margin: 2px 0 0;
  color: var(--yb-text-strong);
  font-family: var(--yb-mono);
  font-size: 30px;
  line-height: 1.1;
  font-variant-numeric: tabular-nums;
}
.unit {
  margin: 0;
  color: var(--yb-text-faint);
  font-size: var(--yb-fs-xs);
}
.go {
  margin-top: 4px;
  color: var(--yb-accent);
  font-size: var(--yb-fs-xs);
}
.card:hover .go {
  text-decoration: underline;
}
</style>
