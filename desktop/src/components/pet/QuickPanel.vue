<script setup lang="ts">
// 快捷面板：团子脚下一条垂直栈（输入条 → 插件），高度随输入条变，不再绝对定位叠上去。
import { computed, onMounted, ref } from "vue";
import { invoke } from "@tauri-apps/api/core";
import InputBar from "../common/InputBar.vue";
import { getDockListOnce } from "../../lib/brain";
import { inputMenuOpen } from "../../lib/input-menu";
import type { InputContext } from "../../lib/at-mention";
import {
  DOCK_SIZE,
  quickStackLeft,
  quickStackTop,
  STACK_W,
} from "../../lib/quick-dock";
import { iconStyle } from "../../lib/icons";

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

onMounted(() => void reloadDock());

const stackStyle = computed(() => ({
  top: quickStackTop(props.petY) + "px",
  left: quickStackLeft() + "px",
  width: STACK_W + "px",
  // 窗口高 = 300（idle/quick 恒 320×300）：给输入条+docks 一个确定高度并禁止超出，
  // 多行输入时 textarea 由 growMax 限高、插件钮在底部被裁，输入框始终完整可见。
  // overflow 用 visible：窗口视口（320×300）天然裁掉窗口外的 docks；若用 hidden 会把
  // 输入框聚焦时的外圈 focus ring（outline）四周裁掉，看起来像"周边一圈被切掉"。
  height: `calc(100% - ${quickStackTop(props.petY) + 10}px)`,
  overflow: "visible",
}));
</script>

<template>
  <div class="wb" :class="{ 'menu-open': inputMenuOpen }">
    <div class="wb-stack" :style="stackStyle">
      <div class="wb-zone wb-input">
        <InputBar
          :busy="props.busy"
          :listening="props.listening"
          placeholder="说点什么…"
          :grow-max="55"
          compact
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
  /* none：.wb 覆盖全窗且渲染在团子之上，必须 none 让点击穿透到下面的 .pet（团子的
   * 单击/双击/长按）。子元素 .wb-input/.wb-dock 已显式 auto，不受影响。 */
  pointer-events: none;
}
/* 菜单打开时整体可交互（配合 Rust 热区放行菜单区域）：此期间 .wb 盖住团子没关系——
 * 用户正在选命令，不需要点团子；菜单关闭后 class 移除，团子恢复可点。 */
.wb.menu-open {
  pointer-events: auto;
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
  flex-shrink: 0; /* 多行时输入条保持完整，插件钮让路被裁 */
  /* 让含菜单的输入区层级高于下方的 .wb-docks，避免菜单向下展开时被 3 个 dock 按钮盖住 */
  position: relative;
  z-index: 3;
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
/* 桌宠下菜单交互保障：菜单会溢出 .wb-input 几何（WKWebView 对 pointer-events:none 父元素的
 * 溢出子树在事件派发路径仍可能跳过），这里显式恢复交互。菜单打开期间 dock 按钮让路
 * （pointer-events:none），杜绝 dock 抢走 mousedown/wheel。大窗不在 .wb-stack 下，:deep 不匹配。 */
.wb-stack :deep(.at-menu) {
  pointer-events: auto !important;
  cursor: pointer;
  overflow-y: auto !important;
}
.wb-stack :deep(.at-item) {
  pointer-events: auto !important;
}
/* 菜单打开时 dock 完全让路：hover/click/wheel 全归菜单，关闭后 dock 恢复可点 */
.wb.menu-open .wb-docks {
  pointer-events: none;
}
</style>
