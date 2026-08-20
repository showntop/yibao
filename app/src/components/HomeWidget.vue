<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import YbIcon from "./YbIcon.vue";
import {
  useHomeWidgets,
  WIDGET_MATERIALS,
  WIDGET_SIZES,
  isWidgetId,
  type WidgetId,
} from "../lib/home-widgets";
import { useLiveAssembly } from "../lib/home-chrome";
import { isPlaced } from "../lib/home-assembly";

const props = defineProps<{
  id: WidgetId;
  fill?: boolean;
}>();

const widgets = useHomeWidgets();
const assembly = useLiveAssembly();
const spec = computed(() => widgets.spec(props.id));
const placed = computed(() => isPlaced(assembly.value, props.id));
const menuOpen = ref(false);
const dragging = ref(false);

function onDragStart(e: DragEvent) {
  dragging.value = true;
  e.dataTransfer?.setData("text/plain", props.id);
  if (e.dataTransfer) e.dataTransfer.effectAllowed = "move";
}
function onDragEnd() {
  dragging.value = false;
}
function onDragOver(e: DragEvent) {
  e.preventDefault();
  if (e.dataTransfer) e.dataTransfer.dropEffect = "move";
}
function onDrop(e: DragEvent) {
  const from = e.dataTransfer?.getData("text/plain");
  e.preventDefault();
  if (!isWidgetId(from) || from === props.id) return;
  widgets.move(from, props.id);
}

function onDoc(e: MouseEvent) {
  const t = e.target as Node | null;
  if (t && (root.value?.contains(t))) return;
  menuOpen.value = false;
}
const root = ref<HTMLElement | null>(null);
onMounted(() => document.addEventListener("mousedown", onDoc));
onUnmounted(() => document.removeEventListener("mousedown", onDoc));
</script>

<template>
  <section
    v-if="spec.visible && placed"
    ref="root"
    class="yb-widget"
    :class="[
      `yb-widget--${spec.size}`,
      `yb-widget--${spec.material}`,
      { 'yb-widget-fill': fill && spec.size === 'l', 'is-dragging': dragging },
    ]"
    :data-widget="id"
    :style="{ '--yb-widget-order': spec.order }"
    @dragover="onDragOver"
    @drop="onDrop"
  >
    <div class="yb-widget-tools">
      <button
        class="yb-widget-tool"
        type="button"
        title="拖动排序"
        aria-label="拖动排序"
        draggable="true"
        @dragstart="onDragStart"
        @dragend="onDragEnd"
      >
        <YbIcon name="grip" :size="11" />
      </button>
      <button
        class="yb-widget-tool"
        type="button"
        title="零件选项"
        aria-label="零件选项"
        :aria-expanded="menuOpen"
        @click.stop="menuOpen = !menuOpen"
      >
        <YbIcon name="more" :size="11" />
      </button>
    </div>
    <div v-if="menuOpen" class="yb-widget-menu" role="menu">
      <div class="row">
        <button
          v-for="s in WIDGET_SIZES"
          :key="s.id"
          type="button"
          :class="{ on: spec.size === s.id }"
          @click="widgets.setSize(id, s.id)"
        >{{ s.label }}</button>
      </div>
      <div class="row">
        <button
          v-for="m in WIDGET_MATERIALS"
          :key="m.id"
          type="button"
          :class="{ on: spec.material === m.id }"
          @click="widgets.setMaterial(id, m.id)"
        >{{ m.label }}</button>
      </div>
      <button class="hide" type="button" @click="widgets.hide(id); menuOpen = false">隐藏</button>
    </div>
    <slot />
  </section>
</template>

<style scoped>
.yb-widget-tools {
  position: absolute;
  top: 6px;
  right: 6px;
  z-index: 5;
  display: flex;
  gap: 2px;
  opacity: 0;
  transition: opacity 140ms var(--yb-ease-out);
}
.yb-widget:hover > .yb-widget-tools,
.yb-widget:focus-within > .yb-widget-tools,
.yb-widget-tools:hover { opacity: 1; }

.yb-widget-tool {
  width: 20px;
  height: 20px;
  padding: 0;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: 7px;
  background: color-mix(in srgb, var(--yb-widget-bg) 72%, transparent);
  color: var(--yb-paper-ink-dim);
  cursor: grab;
}
.yb-widget-tool:last-child { cursor: pointer; }
.yb-widget-tool:hover { color: var(--yb-paper-ink); background: var(--yb-note-mute); }

.yb-widget-menu {
  position: absolute;
  top: 28px;
  right: 6px;
  z-index: 6;
  width: 132px;
  padding: 6px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  border: 1px solid var(--yb-widget-border);
  border-radius: 12px;
  background: var(--yb-widget-bg);
  box-shadow: var(--yb-widget-shadow);
}
.row {
  display: flex;
  gap: 2px;
  padding: 2px;
  border-radius: 8px;
  background: var(--yb-note-mute);
  box-shadow: var(--yb-press);
}
.row button,
.hide {
  flex: 1;
  height: 22px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--yb-paper-ink-dim);
  font: inherit;
  font-size: 10px;
  cursor: pointer;
}
.row button.on {
  background: var(--yb-widget-bg);
  color: var(--yb-paper-ink);
  box-shadow: var(--yb-glaze-hi);
}
.hide {
  width: 100%;
  color: var(--yb-text-faint);
}
.hide:hover { color: var(--yb-danger); }

.is-dragging { opacity: 0.55; }

@media (prefers-reduced-motion: reduce) {
  .yb-widget-tools { opacity: 1; }
}
</style>
