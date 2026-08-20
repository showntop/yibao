<script setup lang="ts">
import { computed, onMounted, onUnmounted, provide, ref } from "vue";
import Avatar from "./Avatar.vue";
import YbIcon from "./YbIcon.vue";
import { HOME_ASSEMBLY_KEY, useHomeChrome } from "../lib/home-chrome";
import { useHomeWidgets } from "../lib/home-widgets";
import {
  collapseGridColumns,
  collapsibleOf,
  docksOf,
  gridStyle,
  isPluginPart,
  itemsInRegion,
  regionSet,
  resolveAssembly,
  stackOrder,
  type Assembly,
  type ResolvedItem,
} from "../lib/home-assembly";

type AvatarState = "idle" | "listen" | "think" | "work" | "say" | "success" | "error";
const SLOTTED = new Set(["work", "input", "nav", "context"]);

defineProps<{ thinking?: boolean; state?: AvatarState }>();
const left = defineModel<boolean>("left", { default: true });
const peek = defineModel<boolean>("peek", { default: true });
const compact = ref(false);

const { id: presetId } = useHomeChrome();
const widgets = useHomeWidgets();

onMounted(() => {
  const mq = window.matchMedia("(max-width: 960px)");
  const apply = () => { compact.value = mq.matches; };
  apply();
  mq.addEventListener("change", apply);
  const preset = collapsibleOf(presetId.value);
  if (preset.includes("left") && window.innerWidth <= 1180) left.value = false;
  onUnmounted(() => mq.removeEventListener("change", apply));
});

const assembly = computed(() =>
  resolveAssembly(presetId.value, widgets.state, { compact: compact.value }),
);
provide(HOME_ASSEMBLY_KEY, assembly);

function defaultSlotRegion(a: Assembly): string | null {
  const regions = new Set(
    a.items.filter((i) => i.kind === "glance" && i.region).map((i) => i.region!),
  );
  for (const r of regions) {
    if (a.items.some((i) => i.region === r && SLOTTED.has(i.kind))) return r;
  }
  return null;
}

const stackRegion = computed(() => defaultSlotRegion(assembly.value));

const structuralRegions = computed(() => {
  const names: string[] = [];
  for (const item of assembly.value.items) {
    if (!item.region || !SLOTTED.has(item.kind)) continue;
    if (!names.includes(item.region)) names.push(item.region);
  }
  return names;
});

function slottedIn(region: string): ResolvedItem[] {
  return itemsInRegion(assembly.value, region).filter((i) => SLOTTED.has(i.kind));
}

function hostOrder(id: string): number {
  return stackOrder(assembly.value, widgets.state, id);
}

const scatterCss = computed(() => {
  if (stackRegion.value) return "";
  const byRegion = new Map<string, ResolvedItem[]>();
  for (const item of assembly.value.items) {
    if (item.kind !== "glance" || !item.region) continue;
    const list = byRegion.get(item.region) ?? [];
    list.push(item);
    byRegion.set(item.region, list);
  }
  const rules: string[] = [];
  for (const [region, items] of byRegion) {
    const align = region === "me" ? "end" : "start";
    const shared = items.length > 1 || items.some((item) => isPluginPart(item.id));
    if (shared) {
      rules.push(
        `[data-home-frame] .stage [data-glance-stack="${region}"]{grid-area:${region};align-self:${align};min-width:0;max-width:100%;overflow:visible;}`,
      );
      continue;
    }
    const item = items[0];
    rules.push(
      `[data-home-frame] .stage [data-widget="${item.id}"]{grid-area:${region};align-self:${align};min-width:0;max-width:100%;overflow:visible;}`,
    );
  }
  return rules.join("");
});

const stageStyle = computed(() => {
  const a = assembly.value;
  const style = gridStyle(a.grid);
  const regions = regionSet(a.grid.areas);
  const names = collapsibleOf(a.preset);
  const collapsed = new Set<string>();
  if (!left.value && names.includes("left") && regions.has("left")) collapsed.add("left");
  if (!peek.value && names.includes("right") && regions.has("right")) collapsed.add("right");
  if (collapsed.size) style.gridTemplateColumns = collapseGridColumns(a.grid, collapsed);
  return style;
});

const canCollapseLeft = computed(() => {
  const a = assembly.value;
  return collapsibleOf(a.preset).includes("left") && regionSet(a.grid.areas).has("left");
});
const canCollapseRight = computed(() => {
  const a = assembly.value;
  const named = collapsibleOf(a.preset).some((name) => regionSet(a.grid.areas).has(name) && name !== "left");
  return named || docksOf(a, "chat", "end").length > 0;
});
</script>

<template>
  <div
    data-home-frame
    class="frame"
    :class="{
      thinking,
      compact,
      'peek-on': peek,
      'left-collapsed': !left,
      'right-collapsed': !peek,
    }"
    :data-preset="assembly.preset"
  >
    <component :is="'style'">{{ scatterCss }}</component>
    <Transition name="rail-fab">
      <button
        v-if="canCollapseLeft && !left"
        class="rail-avatar-reopen"
        type="button"
        title="展开左栏"
        aria-label="展开左栏"
        @click="left = true"
      >
        <Avatar :state="state ?? 'idle'" :size="28" compact />
      </button>
    </Transition>
    <button
      v-if="canCollapseRight"
      class="rail-toggle rail-toggle-right"
      :class="{ collapsed: !peek }"
      type="button"
      :aria-pressed="peek"
      :title="peek ? '隐藏右栏' : '显示右栏'"
      :aria-label="peek ? '隐藏右栏' : '显示右栏'"
      @click="peek = !peek"
    >
      <YbIcon name="panel-right" :size="14" />
    </button>
    <div class="stage" :style="stageStyle">
      <div
        v-for="region in structuralRegions"
        :key="region"
        class="cell"
        :class="{ 'yb-desk': region === stackRegion }"
        :data-region="region"
        :style="{ gridArea: region }"
      >
        <slot v-if="stackRegion === region" />
        <template v-for="item in slottedIn(region)" :key="item.id">
          <div
            class="host"
            :class="[`kind-${item.kind}`, `part-${item.id}`]"
            :style="{ '--yb-widget-order': hostOrder(item.id) }"
          >
            <div v-if="docksOf(assembly, item.id, 'start').length" class="dock-start">
              <slot
                v-for="d in docksOf(assembly, item.id, 'start')"
                :key="d.id"
                :name="d.id"
              />
            </div>
            <div class="host-body">
              <slot :name="item.id" />
            </div>
            <aside
              v-if="docksOf(assembly, item.id, 'end').length"
              v-show="peek"
              class="dock-end"
            >
              <slot
                v-for="d in docksOf(assembly, item.id, 'end')"
                :key="d.id"
                :name="d.id"
              />
            </aside>
          </div>
        </template>
      </div>
      <slot v-if="!stackRegion" />
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
}
.frame[data-preset="desk"] {
  padding: 10px 12px 12px;
  background: var(--yb-desk);
}
.frame[data-preset="rails"] {
  background: var(--yb-content-bg);
}
.frame.thinking[data-preset="desk"] {
  background:
    radial-gradient(80% 50% at 40% 38%, var(--yb-think-mist), transparent 70%),
    var(--yb-desk);
}
.frame.thinking[data-preset="rails"] {
  background:
    radial-gradient(90% 60% at 50% 30%, var(--yb-think-mist), transparent 65%),
    var(--yb-content-bg);
}
.stage {
  box-sizing: border-box;
  position: relative;
  width: 100%;
  height: 100%;
  min-width: 0;
  display: grid;
  align-content: stretch;
  overflow: visible;
}
.frame[data-preset="desk"] .stage {
  column-gap: 12px;
  row-gap: 12px;
}
.cell {
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.frame[data-preset="rails"] .cell[data-region="left"],
.frame[data-preset="rails"] .cell[data-region="right"] {
  width: 280px;
  min-width: 0;
}
/* 跟瓷片同一内容宽。写死 280px 会盖过 .yb-desk 垫，会话宿主比身份/插件宽一圈。 */
.frame[data-preset="rails"] .cell[data-region="left"] > :deep(*),
.frame[data-preset="rails"] .cell[data-region="right"] > :deep(*) {
  box-sizing: border-box;
  width: 100%;
  max-width: 100%;
  min-width: 0;
}
.frame[data-preset="rails"] .cell[data-region="left"] {
  box-shadow: 1px 0 0 var(--yb-border-base);
}
.frame[data-preset="rails"] .cell[data-region="right"] {
  box-shadow: -1px 0 0 var(--yb-border-base);
}
.frame.left-collapsed .cell[data-region="left"],
.frame.right-collapsed .cell[data-region="right"] {
  width: 0;
  opacity: 0;
  pointer-events: none;
  box-shadow: none;
}
.host {
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  order: var(--yb-widget-order, 0);
}
.host.kind-work {
  flex: 1;
  flex-direction: row;
  align-items: stretch;
}
.host.kind-nav,
.host.kind-context {
  flex: 1;
  min-height: 0;
}
.host-body {
  flex: 1 1 0;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.host.kind-input {
  flex: none;
  min-height: auto;
}
.host.kind-input .host-body {
  flex: none;
  min-height: auto;
}
.dock-start {
  flex: none;
  width: 28px;
  min-width: 28px;
  min-height: 0;
  z-index: 2;
}
.dock-end {
  flex: none;
  width: 176px;
  min-width: 0;
  margin-left: 12px;
  align-self: start;
  overflow: visible;
  z-index: 2;
}
.frame[data-preset="rails"] .host.kind-input {
  padding: var(--yb-space-3) var(--yb-space-5) var(--yb-space-4);
}
.cell[data-region="compose"] {
  padding: 2px 8px 4px 36px;
  overflow: visible;
  min-height: auto;
}
.frame.peek-on .cell[data-region="compose"] {
  padding-right: 196px;
}
.frame[data-preset="desk"] :deep(.skill-row) {
  display: none;
}
.host-body :deep(.paper-wrap) {
  flex: 1;
  min-height: 0;
  width: 100%;
}
.host-body :deep(.sheet) {
  width: 100%;
  flex: 1;
  min-height: 0;
  height: auto;
}
.dock-start :deep([data-widget="sessions"]) {
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  padding: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
  box-shadow: none;
  overflow: visible;
}
.dock-start :deep([data-widget="sessions"])::after,
.dock-start :deep([data-widget="sessions"] .yb-widget-tools),
.dock-end :deep(.yb-widget-tools) {
  display: none;
}
.stage :deep([data-widget="identity"] .identity) {
  padding-right: 12px;
}
.stage :deep([data-widget="identity"] .identity-copy) {
  white-space: normal;
}
.stage :deep([data-widget="identity"] .identity-copy > span:last-of-type) {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.stage :deep([data-widget="today"] .today-cell) {
  padding: 6px 2px 7px;
}
.frame[data-preset="rails"] .cell[data-region="main"] .host-body {
  flex: 1;
}
.frame[data-preset="rails"] .cell[data-region="main"] {
  position: relative;
}
.rail-toggle {
  position: absolute;
  top: 8px;
  z-index: 10;
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  padding: 0;
  border: 1px solid transparent;
  border-radius: var(--yb-radius-sm);
  background: color-mix(in srgb, var(--yb-content-bg) 86%, transparent);
  color: var(--yb-text-faint);
  cursor: pointer;
  opacity: 0.74;
}
.rail-toggle-right { right: 8px; }
.rail-toggle.collapsed { color: var(--yb-accent); opacity: 0.9; }
.rail-avatar-reopen {
  position: absolute;
  left: 8px;
  top: 8px;
  z-index: 10;
  width: 36px;
  height: 36px;
  display: grid;
  place-items: center;
  padding: 0;
  border: 1px solid var(--yb-surface-border);
  border-radius: 50%;
  background: var(--yb-surface-2);
  cursor: pointer;
}
.rail-fab-enter-active,
.rail-fab-leave-active {
  transition: opacity var(--yb-dur) var(--yb-ease-out), transform var(--yb-dur) var(--yb-ease-out);
}
.rail-fab-enter-from,
.rail-fab-leave-to {
  opacity: 0;
  transform: scale(0.86);
}
@media (max-width: 900px) {
  .frame[data-preset="rails"] .cell[data-region="right"],
  .frame[data-preset="rails"] .rail-toggle-right {
    display: none;
  }
}
</style>
