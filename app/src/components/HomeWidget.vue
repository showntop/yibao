<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import YbIcon from "./YbIcon.vue";
import {
  useHomeWidgets,
  WIDGET_MATERIALS,
  type WidgetId,
} from "../lib/home-widgets";
import { useHomeChrome, useLiveAssembly } from "../lib/home-chrome";
import { isPlaced } from "../lib/home-assembly";

const props = defineProps<{
  id: WidgetId;
  fill?: boolean;
}>();

const widgets = useHomeWidgets();
const spec = computed(() => widgets.spec(props.id));
const assembly = useLiveAssembly();
const placed = computed(() => isPlaced(assembly.value, props.id));
const { id: presetId } = useHomeChrome();
const menuOpen = ref(false);
const canDrag = computed(() => assembly.value.place === "canvas");
const moreBtn = ref<HTMLElement | null>(null);
const menuEl = ref<HTMLElement | null>(null);
const menuStyle = ref<Record<string, string>>({});

/** 菜单宽度 = .yb-widget-menu 的 width，与 CSS 保持一致（避免错位） */
const MENU_WIDTH = 132;
function placeMenu() {
  const btn = moreBtn.value;
  if (!btn) return;
  const r = btn.getBoundingClientRect();
  menuStyle.value = {
    position: "fixed",
    top: `${Math.round(r.bottom + 6)}px`,
    left: `${Math.round(r.right - MENU_WIDTH)}px`,
  };
}
watch(menuOpen, (open) => { if (open) nextTick(placeMenu); });

function onDoc(e: MouseEvent) {
  const t = e.target as Node | null;
  if (!t) return;
  if (root.value?.contains(t)) return;
  // menuEl 已被 Teleport 到 body，不再被 root 包含；单独豁免避免点菜单内按钮触发关闭
  if (menuEl.value?.contains(t)) return;
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
      { 'yb-widget-fill': fill && spec.size === 'l' },
    ]"
    :data-widget="id"
    :style="{ '--yb-widget-order': String(spec.order) }"
  >
    <div class="yb-widget-tools">
      <button
        v-if="canDrag"
        class="yb-widget-tool"
        type="button"
        data-drag-handle
        title="拖动"
        aria-label="拖动"
      >
        <YbIcon name="grip" :size="11" />
      </button>
      <button
        ref="moreBtn"
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
    <!-- Teleport 到 body：菜单不再受 .yb-widget(overflow:hidden) 裁切，按按钮视口坐标定位 -->
    <Teleport to="body">
      <div
        v-if="menuOpen"
        ref="menuEl"
        class="yb-widget-menu"
        role="menu"
        :style="menuStyle"
        @click.stop
      >
        <div class="row">
          <button
            v-for="m in WIDGET_MATERIALS"
            :key="m.id"
            type="button"
            :class="{ on: spec.material === m.id }"
            @click="widgets.setMaterial(id, m.id)"
          >{{ m.label }}</button>
        </div>
        <button v-if="canDrag" class="act" type="button" @click="widgets.resetFrame(presetId, id); menuOpen = false">恢复位置</button>
        <button class="hide" type="button" @click="widgets.hide(id); menuOpen = false">隐藏</button>
      </div>
    </Teleport>
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
  cursor: pointer;
}
.yb-widget-tool[data-drag-handle] { cursor: grab; }
.yb-widget-tool:hover { color: var(--yb-paper-ink); background: var(--yb-note-mute); }

.yb-widget-menu {
  /* 位置由 inline style（fixed + 视口坐标）控制；只能叠在浮层上，靠 :style 提升层级 */
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
.act,
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
.hide,
.act {
  width: 100%;
  color: var(--yb-text-faint);
}
.act:hover { color: var(--yb-paper-ink); }
.hide:hover { color: var(--yb-danger); }

@media (prefers-reduced-motion: reduce) {
  .yb-widget-tools { opacity: 1; }
}
</style>
