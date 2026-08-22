<script setup lang="ts">
import { computed, onMounted, onUnmounted, provide, ref } from "vue";
import YbIcon from "./YbIcon.vue";
import { HOME_ASSEMBLY_KEY, useHomeChrome } from "../lib/home-chrome";
import { useHomeWidgets } from "../lib/home-widgets";
import { useLivePluginIds } from "../composables/useAssembly";
import {
  DEFAULT_STAGE,
  SNAP_PITCH,
  collapsibleOf,
  foldHandleStyle,
  frameStyle,
  gridStageStyle,
  resolveAssembly,
  settleSnap,
  snapBox,
  type AssemblyFold,
  type HomeDrag,
  type ResolvedFrame,
  type ResolvedItem,
  type SnapHit,
} from "../lib/home-assembly";

type AvatarState = "idle" | "listen" | "think" | "work" | "say" | "success" | "error";

defineProps<{ thinking?: boolean; state?: AvatarState }>();
const left = defineModel<boolean>("left", { default: true });
const peek = defineModel<boolean>("peek", { default: true });
const compact = ref(false);
const stageEl = ref<HTMLElement | null>(null);
const stageSize = ref({ ...DEFAULT_STAGE });

const { id: presetId } = useHomeChrome();
const widgets = useHomeWidgets();
const { ids: pluginIds } = useLivePluginIds();

onMounted(() => {
  const mq = window.matchMedia("(max-width: 960px)");
  const apply = () => { compact.value = mq.matches; };
  apply();
  mq.addEventListener("change", apply);
  if (collapsibleOf(presetId.value).includes("left") && window.innerWidth <= 1180) left.value = false;
  const ro = new ResizeObserver((entries) => {
    const box = entries[0]?.contentRect;
    if (!box) return;
    stageSize.value = { width: Math.max(1, box.width), height: Math.max(1, box.height) };
  });
  if (stageEl.value) ro.observe(stageEl.value);
  onUnmounted(() => {
    mq.removeEventListener("change", apply);
    ro.disconnect();
  });
});

const collapsed = computed(() => [
  ...(left.value ? [] : ["left"]),
  ...(peek.value ? [] : ["right"]),
]);

const assembly = computed(() =>
  resolveAssembly(presetId.value, widgets.state, {
    compact: compact.value,
    stage: stageSize.value,
    collapsed: collapsed.value,
    pluginIds: pluginIds.value,
  }),
);
provide(HOME_ASSEMBLY_KEY, assembly);

const isCanvas = computed(() => assembly.value.place === "canvas");
const grid = computed(() => assembly.value.grid);

const drag = ref<HomeDrag | null>(null);
const snapping = computed(() => isCanvas.value && drag.value !== null);
provide("home-snapping", snapping);
provide("home-stage", stageSize);
provide("home-drag", drag);
provide("home-commit-drag", commitFrame);

const areas = computed(() => Object.keys(grid.value?.stacks ?? {}));

function stackOf(area: string): ResolvedItem[] {
  const ids = grid.value?.stacks[area] ?? [];
  return ids
    .map((id) => assembly.value.items.find((item) => item.id === id))
    .filter((item): item is ResolvedItem => Boolean(item));
}

function foldLabel(fold: AssemblyFold): string {
  const side = fold.side === "start" ? "左栏" : "右栏";
  return fold.folded ? `展开${side}` : `收起${side}`;
}

function toggleFold(fold: AssemblyFold) {
  if (fold.side === "end") peek.value = !peek.value;
  else left.value = !left.value;
}

function hostStyle(item: ResolvedItem) {
  if (!item.frame) return {};
  if (drag.value?.id === item.id) return frameStyle(drag.value.frame);
  return frameStyle(item.frame);
}

function commitFrame(id: string, hit: SnapHit, origin: ResolvedFrame) {
  const settled = settleSnap(hit);
  widgets.setFrame(presetId.value, id, {
    left: settled.left,
    top: settled.top,
    width: origin.width,
    height: origin.height,
    z: origin.z + 20,
  });
}

function onMoveStart(item: ResolvedItem, e: PointerEvent) {
  if (!isCanvas.value || !item.frame) return;
  const t = e.target as HTMLElement;
  if (t.closest("button, input, textarea, a, [contenteditable]") && !t.closest("[data-drag-handle]")) return;
  if (item.kind === "work" || item.kind === "input") {
    const host = e.currentTarget as HTMLElement;
    if (e.clientY - host.getBoundingClientRect().top > 14) return;
  }
  e.preventDefault();
  const origin = { ...item.frame };
  const x0 = e.clientX;
  const y0 = e.clientY;
  let hold: SnapHit | undefined;
  drag.value = { id: item.id, frame: { ...origin, z: origin.z + 20 }, xs: [], ys: [] };
  const onMove = (ev: PointerEvent) => {
    const others = assembly.value.items
      .filter((row) => row.id !== item.id && row.frame)
      .map((row) => row.frame!);
    const hit = snapBox(
      {
        left: origin.left + ev.clientX - x0,
        top: origin.top + ev.clientY - y0,
        width: origin.width,
        height: origin.height,
      },
      others,
      stageSize.value,
      hold,
    );
    hold = hit;
    drag.value = {
      id: item.id,
      frame: { ...hit, width: origin.width, height: origin.height, z: origin.z + 20 },
      xs: hit.xs,
      ys: hit.ys,
    };
  };
  const onUp = () => {
    if (drag.value) {
      commitFrame(item.id, {
        left: drag.value.frame.left,
        top: drag.value.frame.top,
        xs: drag.value.xs,
        ys: drag.value.ys,
      }, origin);
    }
    drag.value = null;
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerup", onUp);
  };
  window.addEventListener("pointermove", onMove);
  window.addEventListener("pointerup", onUp);
}
</script>

<template>
  <div
    data-home-frame
    class="frame"
    :class="{ thinking, compact }"
    :data-place="assembly.place"
    :data-ground="grid?.ground"
  >
    <div
      ref="stageEl"
      class="stage"
      :class="{ snapping, canvas: isCanvas }"
      :style="grid ? { ...gridStageStyle(grid), '--yb-snap': `${SNAP_PITCH}px`, '--yb-snap-major': `${SNAP_PITCH * 4}px` } : { '--yb-snap': `${SNAP_PITCH}px`, '--yb-snap-major': `${SNAP_PITCH * 4}px` }"
    >
      <template v-if="grid">
        <div
          v-for="area in areas"
          :key="area"
          class="area"
          :class="`area-${area}`"
          :style="{ gridArea: area }"
        >
          <div
            v-for="item in stackOf(area)"
            :key="item.id"
            class="host"
            :class="[`kind-${item.kind}`, `part-${item.id}`, `face-${item.presentation}`, { grow: item.grow, 'pin-end': item.pinEnd }]"
          >
            <slot :name="item.id" />
          </div>
        </div>
      </template>
      <template v-else>
        <div
          v-for="item in assembly.items"
          :key="item.id"
          class="host"
          :class="[`kind-${item.kind}`, `part-${item.id}`, `face-${item.presentation}`, { 'yb-desk': item.presentation === 'paper' }]"
          :style="hostStyle(item)"
          @pointerdown="onMoveStart(item, $event)"
        >
          <slot :name="item.id" />
        </div>
      </template>
      <button
        v-for="fold in grid?.fold ?? []"
        :key="fold.name"
        class="fold-handle"
        :class="[`side-${fold.side}`, { folded: fold.folded }]"
        type="button"
        :style="foldHandleStyle(fold, stageSize)"
        :aria-pressed="!fold.folded"
        :title="foldLabel(fold)"
        :aria-label="foldLabel(fold)"
        @click="toggleFold(fold)"
      >
        <YbIcon :name="fold.side === 'start' ? 'panel-left' : 'panel-right'" :size="14" />
      </button>
      <div v-if="drag" class="snap-guides" aria-hidden="true">
        <i v-for="x in drag.xs" :key="`x-${x}`" class="snap-x" :style="{ left: `${x}px` }" />
        <i v-for="y in drag.ys" :key="`y-${y}`" class="snap-y" :style="{ top: `${y}px` }" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.frame {
  box-sizing: border-box;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  display: flex;
  position: relative;
  overflow: hidden;
  background: var(--yb-content-bg);
}
.frame[data-place="canvas"],
.frame[data-ground="desk"] {
  background: var(--yb-desk);
}
.frame.thinking {
  background:
    radial-gradient(80% 50% at 40% 38%, var(--yb-think-mist), transparent 70%),
    var(--yb-content-bg);
}
.frame.thinking[data-place="canvas"],
.frame.thinking[data-ground="desk"] {
  background:
    radial-gradient(80% 50% at 40% 38%, var(--yb-think-mist), transparent 70%),
    var(--yb-desk);
}
.stage {
  box-sizing: border-box;
  position: relative;
  flex: 1;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
.frame[data-place="grid"] .stage {
  overflow: visible;
}
.stage.canvas {
  display: block;
}
.stage.canvas .host {
  overflow: hidden;
  border-radius: var(--yb-widget-radius);
}
.stage.snapping::before {
  content: "";
  position: absolute;
  inset: 0;
  z-index: 0;
  pointer-events: none;
  background-image:
    linear-gradient(to right, color-mix(in srgb, var(--yb-text-faint) 16%, transparent) 1px, transparent 1px),
    linear-gradient(to bottom, color-mix(in srgb, var(--yb-text-faint) 16%, transparent) 1px, transparent 1px),
    linear-gradient(to right, color-mix(in srgb, var(--yb-text-faint) 28%, transparent) 1px, transparent 1px),
    linear-gradient(to bottom, color-mix(in srgb, var(--yb-text-faint) 28%, transparent) 1px, transparent 1px);
  background-size: var(--yb-snap) var(--yb-snap), var(--yb-snap) var(--yb-snap), var(--yb-snap-major) var(--yb-snap-major), var(--yb-snap-major) var(--yb-snap-major);
}
.snap-guides {
  position: absolute;
  inset: 0;
  z-index: 40;
  pointer-events: none;
}
.snap-x,
.snap-y {
  position: absolute;
  background: var(--yb-accent);
  box-shadow: 0 0 0 1px color-mix(in srgb, var(--yb-accent) 35%, transparent);
}
.snap-x {
  top: 0;
  bottom: 0;
  width: 2px;
  margin-left: -1px;
}
.snap-y {
  left: 0;
  right: 0;
  height: 2px;
  margin-top: -1px;
}
.area {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
  min-height: 0;
  overflow: visible;
}
.area-compose {
  min-height: min-content;
  overflow: visible;
}
.host {
  box-sizing: border-box;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: visible;
  position: relative;
}
.host.grow {
  flex: 1;
  overflow: hidden;
}
.host.pin-end {
  margin-top: auto;
}
.host.kind-nav,
.host.kind-context {
  overflow: hidden;
  border: 1px solid var(--yb-widget-border);
  border-radius: var(--yb-widget-radius);
  background:
    var(--yb-widget-glaze),
    var(--yb-widget-bg);
  box-shadow: var(--yb-widget-shadow);
}
/* 让 host 内"透明组件"（AgentBrain / HomeGlance / HomeLife / HomePluginGlance 用 display:contents
   把内部 .yb-widget 透出到 host 直接子）的瓷片撑满 host。HomeContextPanel 不透明，aside 内的
   .yb-widget 隔了一层，不再被此规则影响，由 aside 自己负责内部排布。 */
.host.kind-nav > :deep(.yb-widget),
.host.kind-context > :deep(.yb-widget) {
  flex: 1;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
  box-shadow: none;
}
.host.kind-nav > :deep(.yb-widget::after),
.host.kind-context > :deep(.yb-widget::after) {
  display: none;
}
.host.kind-nav.face-spine {
  overflow: visible;
  border: 0;
  border-radius: 0;
  background: transparent;
  box-shadow: none;
}
.host.kind-nav.face-spine :deep(.yb-widget) {
  overflow: visible;
  border-radius: 0;
}
.host.grow.face-paper {
  overflow: hidden;
}
.stage.canvas .host :deep(.yb-widget),
.stage.canvas .host :deep(.plugin-card) {
  flex: 1;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
}
.stage.canvas .host.kind-work,
.stage.canvas .host.kind-nav,
.stage.canvas .host.kind-context {
  cursor: grab;
}
.stage.canvas .host.kind-work:active,
.stage.canvas .host.kind-nav:active,
.stage.canvas .host.kind-context:active,
.stage.canvas .host.kind-input:active,
.stage.canvas .host.kind-glance:active {
  cursor: grabbing;
}
.host.kind-glance:not(:has(.yb-widget, .plugin-card)) {
  display: none;
}
.host.kind-input {
  overflow: visible;
  justify-content: flex-end;
  flex: none;
  min-height: min-content;
}
.host :deep(.paper-wrap),
.host :deep(.sheet),
.host :deep(.desk-work) {
  flex: 1;
  min-height: 0;
  width: 100%;
  height: auto;
}
.fold-handle {
  box-sizing: border-box;
  display: grid;
  place-items: center;
  padding: 0;
  border: 1px solid var(--yb-widget-border);
  border-radius: 999px;
  background: color-mix(in srgb, var(--yb-widget-bg) 88%, transparent);
  color: var(--yb-text-faint);
  cursor: pointer;
  opacity: 0.42;
  box-shadow: var(--yb-shadow-1);
  transition: opacity 140ms var(--yb-ease-out), color 140ms var(--yb-ease-out), background 140ms var(--yb-ease-out);
}
.stage:hover .fold-handle,
.fold-handle:focus-visible,
.fold-handle.folded {
  opacity: 0.95;
}
.fold-handle:hover,
.fold-handle:focus-visible {
  color: var(--yb-paper-ink);
  background: var(--yb-widget-bg);
}
.fold-handle.folded {
  color: var(--yb-accent);
}
@media (prefers-reduced-motion: reduce) {
  .fold-handle { transition: none; }
}
</style>
