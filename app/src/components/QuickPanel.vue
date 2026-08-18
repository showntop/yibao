<script setup lang="ts">
// 快捷面板：团子脚下一条垂直栈（输入条 → 插件），高度随输入条变，不再绝对定位叠上去。
import { computed, onMounted, ref } from "vue";
import { invoke } from "@tauri-apps/api/core";
import InputBar from "./InputBar.vue";
import { getDockListOnce } from "../lib/brain";
import type { InputContext } from "../lib/at-mention";
import {
  DOCK_SIZE,
  quickStackLeft,
  quickStackTop,
  STACK_W,
} from "../lib/quick-dock";

const props = withDefaults(
  defineProps<{
    busy?: boolean;
    listening?: boolean;
    petY?: number;
  }>(),
  { petY: 100 },
);
const emit = defineEmits<{
  (e: "submit", text: string, contexts?: InputContext[]): void;
  (e: "launch", p: { id: string; name: string }): void;
  (e: "mic"): void;
  (e: "interrupt"): void;
}>();

interface PluginInfo { id: string; name: string }
const topPlugins = ref<PluginInfo[]>([]);
type DockSlot = { kind: "plugin"; p: PluginInfo } | { kind: "more" };
const dockSlots = computed<DockSlot[]>(() => {
  const arr: DockSlot[] = topPlugins.value.slice(0, 3).map((p) => ({ kind: "plugin", p }));
  while (arr.length < 3) arr.push({ kind: "more" });
  return arr;
});
async function reloadDock() {
  try {
    const [all, dock] = await Promise.all([
      invoke<PluginInfo[]>("list_plugins"),
      getDockListOnce(2000),
    ]);
    const pinned = new Set(dock.dock.filter((d) => d.pinned).map((d) => d.id));
    const pinnedFirst = all.filter((p) => pinned.has(p.id));
    const rest = all.filter((p) => !pinned.has(p.id));
    topPlugins.value = [...pinnedFirst, ...rest].slice(0, 3);
  } catch {
    topPlugins.value = [];
  }
}
function onDock(slot: DockSlot) {
  if (slot.kind === "plugin") emit("launch", slot.p);
  else emit("launch", { id: "", name: "全部" });
}

const ICON_PALETTE = [
  { bg: "var(--yb-icon-bg-0)", fg: "var(--yb-icon-fg-0)" },
  { bg: "var(--yb-icon-bg-1)", fg: "var(--yb-icon-fg-1)" },
  { bg: "var(--yb-icon-bg-2)", fg: "var(--yb-icon-fg-2)" },
  { bg: "var(--yb-icon-bg-3)", fg: "var(--yb-icon-fg-3)" },
  { bg: "var(--yb-icon-bg-4)", fg: "var(--yb-icon-fg-4)" },
];
function iconStyle(id: string) {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  const c = ICON_PALETTE[h % ICON_PALETTE.length];
  return { background: c.bg, color: c.fg };
}

onMounted(() => void reloadDock());

const stackStyle = computed(() => ({
  top: quickStackTop(props.petY) + "px",
  left: quickStackLeft() + "px",
  width: STACK_W + "px",
}));
</script>

<template>
  <div class="wb">
    <div class="wb-stack" :style="stackStyle">
      <div class="wb-zone wb-input">
        <InputBar
          :busy="props.busy"
          :listening="props.listening"
          placeholder="说点什么…"
          @submit="(t, ctx) => emit('submit', t, ctx)"
          @mic="() => emit('mic')"
          @interrupt="() => emit('interrupt')"
        />
      </div>
      <div class="wb-docks">
        <button
          v-for="(slot, i) in dockSlots"
          :key="i"
          class="wb-zone wb-dock"
          :class="slot.kind === 'more' && 'wb-dock-more'"
          :style="{ width: DOCK_SIZE + 'px', height: DOCK_SIZE + 'px' }"
          :title="slot.kind === 'plugin' ? slot.p.name : '全部插件'"
          @click="onDock(slot)"
        >
          <span class="wb-dock-ic" :style="slot.kind === 'plugin' ? iconStyle(slot.p.id) : undefined">
            <span v-if="slot.kind === 'plugin'" class="wb-dock-letter">{{ slot.p.name.slice(0, 1) }}</span>
            <span v-else class="wb-dock-plus">+</span>
          </span>
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.wb {
  position: absolute;
  inset: 0;
  pointer-events: none;
}
.wb-stack {
  position: absolute;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
}
.wb-input {
  width: 100%;
  pointer-events: auto;
}
.wb-docks {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 14px;
}
.wb-dock {
  padding: 0;
  border: 1px solid var(--yb-surface-border);
  border-radius: 50%;
  background: var(--yb-glass);
  -webkit-backdrop-filter: var(--yb-blur);
  backdrop-filter: var(--yb-blur);
  box-shadow:
    0 1px 2px rgba(var(--yb-c-slate-rgb), 0.06),
    0 6px 14px rgba(var(--yb-c-slate-rgb), 0.10);
  cursor: pointer;
  pointer-events: auto;
  display: grid;
  place-items: center;
  flex: none;
  transition: transform var(--yb-dur) var(--yb-ease-spring),
              box-shadow var(--yb-dur-fast) var(--yb-ease-out);
}
.wb-dock:hover {
  transform: scale(1.06);
  box-shadow:
    0 1px 2px rgba(var(--yb-c-slate-rgb), 0.08),
    0 8px 18px rgba(var(--yb-c-slate-rgb), 0.14);
}
.wb-dock:active {
  transform: scale(0.96);
}
.wb-dock-ic {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  font-size: 13px;
  font-weight: var(--yb-fw-bold);
}
.wb-dock-letter {
  display: block;
  line-height: 1;
}
.wb-dock-plus {
  font-size: 18px;
  font-weight: 300;
  color: var(--yb-text-dim);
  line-height: 1;
}
.wb-dock-more .wb-dock-ic {
  background: transparent;
  border: 1px dashed var(--yb-surface-border);
}
</style>
