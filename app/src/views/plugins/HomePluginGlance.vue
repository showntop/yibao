<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import YbIcon from "../../components/common/YbIcon.vue";
import {
  getWidgetsOnce,
  onWidgets,
  panelAction,
  type WidgetPayload,
} from "../../lib/brain";
import { useLiveAssembly } from "../../lib/home/home-chrome.ts";
import {
  isPlaced,
  pluginPartId,
} from "../../lib/home/home-assembly.ts";
import { useLivePluginIds } from "../../composables/useAssembly";
import { pluginGlanceLine, pluginHasGlance } from "../../lib/home/home-glance-faces.ts";
import { isDeskLivePlugin, setDeskOrigin, type DeskKind } from "../../lib/home/home-desk-presence.ts";

const props = defineProps<{ panel?: string; livePanel?: string | null; liveKind?: DeskKind }>();
const emit = defineEmits<{ fold: [] }>();
const widgets = ref<WidgetPayload[]>([]);
const assembly = useLiveAssembly();
const { sync: pluginSync } = useLivePluginIds();
let unWidgets: (() => void) | null = null;

const canDrag = computed(() => assembly.value.place === "canvas");
const shown = computed(() => {
  const rows = props.panel
    ? widgets.value.filter((widget) => pluginPartId(widget.panel) === props.panel)
    : widgets.value;
  // 插件卡是否显示由预设决定：只有预设 stacks 里显式声明的 widget 才渲染
  return rows.filter(
    (widget) => isPlaced(assembly.value, pluginPartId(widget.panel)) && pluginHasGlance(widget),
  );
});

function applyWidgets(next: WidgetPayload[]) {
  widgets.value = next;
  pluginSync(next);
}

function live(widget: WidgetPayload) {
  return isDeskLivePlugin(widget.panel, props.livePanel);
}

function openWidget(widget: WidgetPayload, event?: MouseEvent) {
  if (live(widget)) {
    emit("fold");
    return;
  }
  if (!widget.open) return;
  const card = (event?.currentTarget as HTMLElement | null)?.closest?.(".plugin-card");
  setDeskOrigin(card ?? (event?.currentTarget as Element | null));
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
      :data-live="live(widget) || undefined"
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
        :disabled="!widget.open && !live(widget)"
        :title="live(widget) ? `收起${widget.title}` : widget.open ? `摊开${widget.title}` : widget.title"
        @click="openWidget(widget, $event)"
      >
        <span class="plugin-title">{{ widget.title }}</span>
        <span class="plugin-line">{{ live(widget) && props.liveKind !== "tool" ? "正在用" : pluginGlanceLine(widget.schema, widget.data) }}</span>
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
  min-height: 52px;
  display: flex;
  align-items: stretch;
}
.plugin-grip {
  flex: none;
  align-self: center;
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
  margin: 0;
  padding: 8px 14px;
  border: 0;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 2px;
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
  color: var(--yb-paper-ink-dim);
  font-size: 10px;
  font-weight: var(--yb-fw-medium);
  letter-spacing: 0.04em;
  line-height: 1.3;
}
.plugin-line {
  width: 100%;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--yb-paper-ink);
  font-size: 12px;
  line-height: 1.35;
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
.plugin-card[data-live] {
  box-shadow: inset 0 0 0 1px rgba(var(--yb-c-sky-rgb), 0.34);
}
.plugin-card[data-live] .plugin-line {
  color: var(--yb-accent);
}
</style>
