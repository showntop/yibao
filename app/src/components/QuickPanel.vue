<script setup lang="ts">
// 快捷面板（单窗三态的 quick 内容层）：3 圆（常用插件，上拱弧形）+ 底部输入条。
// 与团子同窗渲染（App.vue 内 v-show），热区由 App.vue 统一上报（.wb-zone），交互走普通事件。
import { computed, onMounted, ref } from "vue";
import { invoke } from "@tauri-apps/api/core";
import InputBar from "./InputBar.vue";
import { getDockListOnce } from "../lib/brain";

const props = withDefaults(
  defineProps<{
    busy?: boolean;
    listening?: boolean;
    /** 团子窗口内 top（CSS 像素）：布局随团子联动（输入条 = petY + 130，间距恒定） */
    petY?: number;
    /** 顶部贴顶时 3 圆与团子重叠（团子顶 < 3圆底），隐藏 3 圆只留输入条 */
    showDock?: boolean;
  }>(),
  { petY: 100, showDock: true },
);
const emit = defineEmits<{
  (e: "submit", text: string): void;
  (e: "launch", p: { id: string; name: string }): void;
  (e: "mic"): void;
  (e: "interrupt"): void;
}>();

// ---- 3 圆快捷插件：Dock pinned 优先 + 频率补齐，不足 3 用「全部」虚线占位 ----
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

// ---- 插件图标配色：按 id 哈希到 5 色调色板 ----
const ICON_PALETTE = [
  { bg: "rgba(77,144,196,0.16)",  fg: "#3d7aa8" },
  { bg: "rgba(238,95,143,0.16)",  fg: "#c4447a" },
  { bg: "rgba(242,160,60,0.16)",  fg: "#a86a15" },
  { bg: "rgba(62,142,90,0.16)",   fg: "#2d6e44" },
  { bg: "rgba(124,92,184,0.16)",  fg: "#5a4380" },
];
function iconStyle(id: string) {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  const c = ICON_PALETTE[h % ICON_PALETTE.length];
  return { background: c.bg, color: c.fg };
}

onMounted(() => void reloadDock());
</script>

<template>
  <div class="wb">
    <!-- 3 圆快捷插件：上拱弧形（中间圆高 20px 弧差），跟随团子（top = petY-52/72，
         间距恒 2px），贴顶时 3 圆自动出窗由 showDock 隐藏 -->
    <button
      v-for="(slot, i) in dockSlots"
      v-if="props.showDock"
      :key="i"
      :class="['wb-zone', 'wb-dock', `wb-dock-${i + 1}`, slot.kind === 'more' && 'wb-dock-more']"
      :style="{ top: props.petY - 52 - (i === 1 ? 20 : 0) + 'px' }"
      :title="slot.kind === 'plugin' ? slot.p.name : '全部插件'"
      @click="onDock(slot)"
    >
      <span class="wb-dock-ic" :style="slot.kind === 'plugin' ? iconStyle(slot.p.id) : undefined">
        <span v-if="slot.kind === 'plugin'" class="wb-dock-letter">{{ slot.p.name.slice(0, 1) }}</span>
        <span v-else class="wb-dock-plus">+</span>
      </span>
    </button>

    <!-- 底部输入条：跟随团子（top = petY + 98，与团子底间距恒定 2px） -->
    <div class="wb-zone wb-input" :style="{ top: props.petY + 98 + 'px' }">
      <InputBar
        :busy="props.busy"
        :listening="props.listening"
        @submit="(t) => emit('submit', t)"
        @mic="() => emit('mic')"
        @interrupt="() => emit('interrupt')"
      />
    </div>
  </div>
</template>

<style scoped>
.wb {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

/* 3 圆：上拱弧形（窗口 320×300，团子 y:100-196） */
.wb-dock {
  position: absolute;
  width: 50px;
  height: 50px;
  padding: 0;
  border: 1px solid var(--yb-surface-border);
  border-radius: 50%;
  background: var(--yb-glass);
  -webkit-backdrop-filter: var(--yb-blur);
  backdrop-filter: var(--yb-blur);
  box-shadow:
    0 1px 2px rgba(var(--yb-c-slate-rgb), 0.06),
    0 8px 20px rgba(var(--yb-c-slate-rgb), 0.10);
  cursor: pointer;
  pointer-events: auto;
  transition: transform var(--yb-dur) var(--yb-ease-spring),
              box-shadow var(--yb-dur-fast) var(--yb-ease-out);
}
.wb-dock:hover {
  transform: scale(1.04);
  box-shadow:
    0 1px 2px rgba(var(--yb-c-slate-rgb), 0.08),
    0 10px 24px rgba(var(--yb-c-slate-rgb), 0.14);
}
.wb-dock:active {
  transform: scale(0.96);
}
.wb-dock-1 { left: 87px; }
.wb-dock-2 { left: 167px; }
.wb-dock-3 { left: 247px; }

.wb-dock-ic {
  display: grid;
  place-items: center;
  width: 38px;
  height: 38px;
  margin: 0 auto;
  border-radius: 50%;
  font-size: 15.5px;
  font-weight: var(--yb-fw-bold);
}
.wb-dock-letter {
  display: block;
  line-height: 1;
}
.wb-dock-plus {
  font-size: 22px;
  font-weight: 300;
  color: var(--yb-text-dim);
  line-height: 1;
}
.wb-dock-more .wb-dock-ic {
  background: transparent;
  border: 1px dashed var(--yb-surface-border);
}

/* 输入条：下方居中（窗口 320×300，整体右移 32 与团子对齐）；top 由 petY 驱动（petY+98） */
.wb-input {
  position: absolute;
  left: 72px;
  top: 230px;
  width: 240px;
  pointer-events: auto;
}
</style>
