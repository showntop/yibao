<script setup lang="ts">
import { computed, onMounted, onUnmounted, provide, ref } from "vue";
import YbIcon from "../components/common/YbIcon.vue";
import { HOME_ASSEMBLY_KEY, useHomeChrome } from "../lib/home/home-chrome.ts";
import { useHomeWidgets } from "../lib/home/home-widgets.ts";
import { useLivePluginIds } from "../composables/useAssembly";
import {
  DEFAULT_STAGE,
  SNAP_PITCH,
  collapsibleOf,
  collapsibleSidesOf,
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
} from "../lib/home/home-assembly.ts";
import type { AvatarState } from "../protocol/brain-types";

defineProps<{ thinking?: boolean; state?: AvatarState }>();
const left = defineModel<boolean>("left", { default: true });
const peek = defineModel<boolean>("peek", { default: true });
const compact = ref(false);
// 降级多断点（design §8）：<1280 器物收、<1100 今日收条、≤960 compact；取最紧命中档
const narrow = ref(false);
const slim = ref(false);
const stageEl = ref<HTMLElement | null>(null);
const stageSize = ref({ ...DEFAULT_STAGE });

const { id: presetId } = useHomeChrome();
const widgets = useHomeWidgets();
const { ids: pluginIds } = useLivePluginIds();

onMounted(() => {
  const mqs = [
    { q: window.matchMedia("(max-width: 960px)"), apply: (v: boolean) => (compact.value = v) },
    { q: window.matchMedia("(max-width: 1100px)"), apply: (v: boolean) => (slim.value = v) },
    { q: window.matchMedia("(max-width: 1280px)"), apply: (v: boolean) => (narrow.value = v) },
  ];
  for (const { q, apply } of mqs) {
    apply(q.matches);
    q.addEventListener("change", (e) => apply(e.matches));
  }
  if (collapsibleOf(presetId.value).includes("left") && window.innerWidth <= 1180) left.value = false;
  const ro = new ResizeObserver((entries) => {
    const box = entries[0]?.contentRect;
    if (!box) return;
    stageSize.value = { width: Math.max(1, box.width), height: Math.max(1, box.height) };
  });
  if (stageEl.value) ro.observe(stageEl.value);
  onUnmounted(() => {
    for (const { q, apply } of mqs) q.removeEventListener("change", (e) => apply(e.matches));
    ro.disconnect();
  });
});

const foldSides = computed(() => collapsibleSidesOf(presetId.value));
const collapsed = computed(() => {
  const areas: string[] = [];
  for (const [area, side] of Object.entries(foldSides.value)) {
    const open = side === "start" ? left.value : peek.value;
    if (!open) areas.push(area);
  }
  return areas;
});

const assembly = computed(() =>
  resolveAssembly(presetId.value, widgets.state, {
    compact: compact.value,
    slim: slim.value,
    narrow: narrow.value,
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

function railOverviewOf(area: string): ResolvedItem[] {
  return stackOf(area).filter((item) => item.id !== "sessions");
}

function railSessionsOf(area: string): ResolvedItem[] {
  return stackOf(area).filter((item) => item.id === "sessions");
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
          <template v-if="presetId === 'rails' && area === 'left'">
            <div class="rail-overview" aria-label="译宝概览">
              <div
                v-for="item in railOverviewOf(area)"
                :key="item.id"
                class="host"
                :class="[`kind-${item.kind}`, `part-${item.id}`, `face-${item.presentation}`, { grow: item.grow, 'pin-end': item.pinEnd }]"
              >
                <slot :name="item.id" />
              </div>
            </div>
            <div
              v-for="item in railSessionsOf(area)"
              :key="item.id"
              class="host rail-sessions-host"
              :class="[`kind-${item.kind}`, `part-${item.id}`, `face-${item.presentation}`, { grow: item.grow, 'pin-end': item.pinEnd }]"
            >
              <slot :name="item.id" />
            </div>
          </template>
          <template v-else>
            <div
              v-for="item in stackOf(area)"
              :key="item.id"
              class="host"
              :class="[`kind-${item.kind}`, `part-${item.id}`, `face-${item.presentation}`, { grow: item.grow, 'pin-end': item.pinEnd }]"
            >
              <slot :name="item.id" />
            </div>
          </template>
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
.area-start,
.area-end {
  gap: 8px;
  min-width: 0;
}
.frame[data-preset="rails"] .area-left {
  gap: 0;
  padding: 10px;
  overflow: hidden;
  border: 1px solid var(--yb-widget-border);
  border-radius: calc(var(--yb-widget-radius) + 4px);
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--yb-accent-soft) 18%, transparent), transparent 28%),
    color-mix(in srgb, var(--yb-widget-bg) 90%, var(--yb-content-bg));
  box-shadow: var(--yb-widget-shadow);
}
.frame[data-preset="rails"] .rail-overview {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
  scrollbar-width: thin;
  overscroll-behavior: contain;
}
.frame[data-preset="rails"] .area-left > .host,
.frame[data-preset="rails"] .rail-overview > .host {
  flex: none;
  overflow: visible;
  border: 0;
  border-radius: 0;
  background: transparent;
  box-shadow: none;
}
.frame[data-preset="rails"] .area-left > .host + .host,
.frame[data-preset="rails"] .rail-overview > .host + .host {
  border-top: 1px solid color-mix(in srgb, var(--yb-widget-border) 72%, transparent);
}
.frame[data-preset="rails"] .area-left > .host.grow,
.frame[data-preset="rails"] .rail-overview > .host.grow {
  flex: 1;
  min-height: 160px;
  overflow: hidden;
}
.frame[data-preset="rails"] .area-left > .host > :deep(.yb-widget),
.frame[data-preset="rails"] .rail-overview > .host > :deep(.yb-widget) {
  width: 100%;
  border: 0;
  border-radius: 0;
  background: transparent;
  box-shadow: none;
}
.frame[data-preset="rails"] .area-left > .host > :deep(.yb-widget::after),
.frame[data-preset="rails"] .rail-overview > .host > :deep(.yb-widget::after) {
  display: none;
}
.frame[data-preset="rails"] .area-left > .part-identity,
.frame[data-preset="rails"] .rail-overview > .part-identity {
  margin-bottom: 4px;
  border: 1px solid color-mix(in srgb, var(--yb-widget-border) 86%, transparent);
  border-radius: var(--yb-widget-radius);
  background: color-mix(in srgb, var(--yb-widget-bg) 88%, transparent);
  box-shadow: var(--yb-glaze-hi), var(--yb-shadow-1);
}
.frame[data-preset="rails"] .area-left > .part-identity + .host,
.frame[data-preset="rails"] .rail-overview > .part-identity + .host {
  border-top: 0;
}
.frame[data-preset="rails"] .area-left > .host.rail-sessions-host.grow {
  flex: 0 0 clamp(208px, 30dvh, 280px);
  min-height: 208px;
  overflow: hidden;
  border-top: 1px solid color-mix(in srgb, var(--yb-widget-border) 82%, transparent);
  background: color-mix(in srgb, var(--yb-widget-bg) 94%, var(--yb-content-bg));
  box-shadow: 0 -10px 20px color-mix(in srgb, var(--yb-content-bg) 52%, transparent);
}
.frame[data-preset="rails"] .area-main,
.frame[data-preset="rails"] .area-right {
  padding-block: 2px;
}
@media (max-height: 780px) {
  .frame[data-preset="rails"] .rail-overview > .part-mind :deep(.brain-stage) {
    height: 132px;
    min-height: 132px;
    aspect-ratio: auto;
  }
  .frame[data-preset="rails"] .rail-overview > .part-today :deep(.stain-wrap) {
    display: none;
  }
  .frame[data-preset="rails"] .rail-overview > .part-today :deep(.today-summary) {
    margin: 2px 10px 10px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}
.area-ident {
  justify-content: center;
  min-width: 0;
}
.area-ident .host,
.area-ident :deep(.yb-widget) {
  flex: 0 0 auto;
  width: 100%;
}
.area-note {
  min-height: 0;
  min-width: 0;
}
.area-compose {
  justify-content: center;
  min-height: min-content;
  overflow: visible;
}
.area-compose .host.kind-input {
  justify-content: center;
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
.host.part-scratch.grow {
  overflow: visible;
}
.host.part-scratch.grow :deep(.yb-widget) {
  flex: 1;
  width: 100%;
  min-width: 0;
  min-height: 0;
}
.host.pin-end {
  margin-top: auto;
}
.host.kind-context.grow :deep(.session-inspector) {
  flex: 1;
  height: 100%;
  min-width: 0;
  min-height: 0;
}
.host.kind-nav,
.host.kind-context {
  overflow: hidden;
  min-width: 0;
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
.host.kind-nav.face-spine.grow {
  overflow: visible;
  min-height: 0;
}
.host.kind-nav.face-spine :deep(.yb-widget) {
  overflow: visible;
  border-radius: 0;
  display: flex;
  flex-direction: column;
}
.host.grow.face-paper {
  overflow: visible;
  border-radius: 0 var(--yb-widget-radius) var(--yb-widget-radius) 0;
}
.host.grow.face-paper :deep(.paper-wrap) {
  border-radius: inherit;
  overflow: hidden;
  box-shadow: var(--yb-widget-shadow);
}
.host.grow.face-paper :deep(.sheet) {
  box-shadow: none;
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
  width: 24px;
  height: 24px;
  padding: 0;
  border: 0;
  border-radius: 999px;
  background: transparent;
  color: var(--yb-text-faint);
  cursor: pointer;
  opacity: 0;
  box-shadow: none;
  transition: opacity 140ms var(--yb-ease-out), color 140ms var(--yb-ease-out), background 140ms var(--yb-ease-out);
}
.stage:hover .fold-handle,
.fold-handle:focus-visible {
  opacity: 0.55;
}
.fold-handle:hover,
.fold-handle:focus-visible {
  opacity: 1;
  color: var(--yb-paper-ink);
  background: color-mix(in srgb, var(--yb-widget-bg) 92%, transparent);
}
.fold-handle.folded {
  opacity: 1;
  background: color-mix(in srgb, var(--yb-accent-soft) 60%, transparent);
  color: var(--yb-accent-deep);
}
@media (prefers-reduced-motion: reduce) {
  .fold-handle { transition: none; }
}
</style>
