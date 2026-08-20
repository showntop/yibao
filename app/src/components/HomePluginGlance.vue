<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import YbIcon from "./YbIcon.vue";
import {
  getWidgetsOnce,
  onWidgets,
  panelAction,
  type WidgetPayload,
} from "../lib/brain";
import { useLiveAssembly } from "../lib/home-chrome";
import {
  isPlaced,
  pluginPartId,
  syncPluginParts,
} from "../lib/home-assembly";

const props = defineProps<{ panel?: string }>();
const widgets = ref<WidgetPayload[]>([]);
const assembly = useLiveAssembly();
let unWidgets: (() => void) | null = null;

const canDrag = computed(() => assembly.value.place === "canvas");
const shown = computed(() => {
  const rows = props.panel
    ? widgets.value.filter((widget) => pluginPartId(widget.panel) === props.panel)
    : widgets.value;
  return rows.filter((widget) => isPlaced(assembly.value, pluginPartId(widget.panel)));
});

function applyWidgets(next: WidgetPayload[]) {
  widgets.value = next;
  syncPluginParts(next);
}

function openWidget(widget: WidgetPayload) {
  if (!widget.open) return;
  const pluginId = widget.panel.split(":")[0];
  void panelAction(widget.open, {}, undefined, `panel:${pluginId}`).catch(() => {});
}

onMounted(async () => {
  const result = await getWidgetsOnce().catch(() => ({ widgets: [] as WidgetPayload[] }));
  applyWidgets(result.widgets ?? []);
  try {
    unWidgets = await onWidgets((payload) => applyWidgets(payload?.widgets ?? []));
  } catch { /* sidecar unavailable */ }
});

onUnmounted(() => {
  unWidgets?.();
});
</script>

<template>
  <aside v-if="shown.length" class="plugin-glance">
    <section
      v-for="widget in shown"
      :key="widget.panel"
      class="yb-widget yb-widget--porcelain yb-widget--m plugin-card"
      :data-widget="pluginPartId(widget.panel)"
    >
      <button
        v-if="canDrag"
        class="plugin-grip"
        type="button"
        data-drag-handle
        title="拖动"
        aria-label="拖动"
      >
        <YbIcon name="grip" :size="11" />
      </button>
      <button
        class="plugin-open"
        type="button"
        :disabled="!widget.open"
        :title="widget.open ? `打开${widget.title}` : widget.title"
        @click="openWidget(widget)"
      >
        <span class="plugin-title">{{ widget.title }}</span>
      </button>
    </section>
  </aside>
</template>

<style scoped>
.plugin-glance { display: contents; }
.plugin-card {
  margin: 0;
  padding: 0;
  width: 100%;
  min-height: 40px;
  display: flex;
  align-items: center;
}
.plugin-grip {
  flex: none;
  width: 20px;
  height: 20px;
  margin-left: 8px;
  padding: 0;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: var(--yb-paper-ink-dim);
  cursor: grab;
}
.plugin-open {
  flex: 1;
  min-width: 0;
  height: 40px;
  margin: 0;
  padding: 0 14px;
  border: 0;
  display: flex;
  align-items: center;
  text-align: left;
  cursor: pointer;
  font: inherit;
  color: inherit;
  background: transparent;
}
.plugin-title {
  width: 100%;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--yb-paper-ink);
  font-size: 12px;
  font-weight: var(--yb-fw-medium);
  letter-spacing: 0;
  line-height: 1.3;
}
.plugin-open:disabled {
  cursor: default;
  opacity: 0.72;
}
.plugin-open:not(:disabled):hover {
  filter: brightness(0.98);
}
.plugin-open:focus-visible {
  outline: 2px solid var(--yb-accent);
  outline-offset: 1px;
}
</style>
