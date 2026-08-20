<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import {
  getWidgetsOnce,
  onWidgets,
  panelAction,
  type WidgetPayload,
} from "../lib/brain";
import { useLiveAssembly } from "../lib/home-chrome";
import { useHomeWidgets } from "../lib/home-widgets";
import {
  isPlaced,
  isPluginPart,
  itemsInRegion,
  pluginPartId,
  stackOrder,
  syncPluginParts,
} from "../lib/home-assembly";

const widgets = ref<WidgetPayload[]>([]);
const layout = useHomeWidgets();
const assembly = useLiveAssembly();
let unWidgets: (() => void) | null = null;

const placed = computed(() =>
  widgets.value.filter((widget) => isPlaced(assembly.value, pluginPartId(widget.panel))),
);

const region = computed(() =>
  assembly.value.items.find((item) => isPluginPart(item.id))?.region,
);

const hosted = computed(() => {
  if (!region.value) return false;
  return !itemsInRegion(assembly.value, region.value).some((item) =>
    item.kind === "work" || item.kind === "input" || item.kind === "nav" || item.kind === "context",
  );
});

const pluginOrder = computed(() => {
  const first = placed.value[0];
  if (!first) return 0;
  return stackOrder(assembly.value, layout.state, pluginPartId(first.panel));
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
  <aside
    v-if="placed.length"
    class="plugin-glance"
    :class="{ hosted }"
    :data-glance-stack="hosted ? region : undefined"
  >
    <button
      v-for="widget in placed"
      :key="widget.panel"
      class="yb-widget yb-widget--porcelain yb-widget--m plugin-card"
      type="button"
      :data-widget="pluginPartId(widget.panel)"
      :style="{ '--yb-widget-order': pluginOrder }"
      :disabled="!widget.open"
      :title="widget.open ? `打开${widget.title}` : widget.title"
      @click="openWidget(widget)"
    >
      <span class="yb-widget-head">{{ widget.title }}</span>
    </button>
  </aside>
</template>

<style scoped>
.plugin-glance { display: contents; }
.plugin-glance.hosted {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
}
.plugin-card {
  width: 100%;
  margin: 0;
  padding: 0 0 8px;
  border: 0;
  text-align: left;
  cursor: pointer;
  font: inherit;
  color: inherit;
}
.plugin-card:disabled {
  cursor: default;
  opacity: 0.72;
}
.plugin-card:not(:disabled):hover {
  filter: brightness(0.98);
}
.plugin-card:focus-visible {
  outline: 2px solid var(--yb-accent);
  outline-offset: 1px;
}
</style>
