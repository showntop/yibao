<script setup lang="ts">
// 截图框选层：⌘⇧I 后铺满显示器，拖拽画矩形 → finish_snip；Esc/单击/过小选区 → cancel_snip。
import { computed, onMounted, onUnmounted, ref } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

const start = ref<{ x: number; y: number } | null>(null);
const cur = ref<{ x: number; y: number } | null>(null);

const rect = computed(() => {
  if (!start.value || !cur.value) return null;
  const left = Math.min(start.value.x, cur.value.x);
  const top = Math.min(start.value.y, cur.value.y);
  const width = Math.abs(cur.value.x - start.value.x);
  const height = Math.abs(cur.value.y - start.value.y);
  return { left, top, width, height };
});

function down(e: MouseEvent) {
  start.value = { x: e.clientX, y: e.clientY };
  cur.value = { x: e.clientX, y: e.clientY };
}
function move(e: MouseEvent) {
  if (start.value) cur.value = { x: e.clientX, y: e.clientY };
}
async function up() {
  const r = rect.value;
  start.value = null;
  cur.value = null;
  if (r && r.width > 8 && r.height > 8) {
    await invoke("finish_snip", { rect: r });
  } else {
    await invoke("cancel_snip"); // 单击/抖动选区 = 取消
  }
}
function onKey(e: KeyboardEvent) {
  if (e.key === "Escape") void invoke("cancel_snip");
}

let unlisten: UnlistenFn | null = null;
onMounted(async () => {
  window.addEventListener("keydown", onKey);
  unlisten = await listen("snip-start", () => {
    start.value = null;
    cur.value = null;
  });
});
onUnmounted(() => {
  window.removeEventListener("keydown", onKey);
  unlisten?.();
});
</script>

<template>
  <div class="cover" @mousedown="down" @mousemove="move" @mouseup="up">
    <div
      v-if="rect"
      class="sel"
      :style="{ left: rect.left + 'px', top: rect.top + 'px', width: rect.width + 'px', height: rect.height + 'px' }"
    >
      <span class="size">{{ Math.round(rect.width) }} × {{ Math.round(rect.height) }}</span>
    </div>
    <div v-if="!rect" class="hint">拖拽框选要问的区域，Esc 取消</div>
  </div>
</template>

<style scoped>
.cover {
  position: fixed;
  inset: 0;
  cursor: crosshair;
  background: rgba(15, 23, 42, 0.28);
  font-family: var(--yb-font);
}
.sel {
  position: absolute;
  border: 1.5px solid #38bdf8;
  border-radius: 2px;
  box-shadow: 0 0 0 9999px rgba(15, 23, 42, 0.28);
  background: transparent;
}
.size {
  position: absolute;
  right: 0;
  bottom: -24px;
  font-size: 11px;
  color: #e2e8f0;
  background: rgba(15, 23, 42, 0.75);
  padding: 2px 6px;
  border-radius: 4px;
  font-variant-numeric: tabular-nums;
}
.hint {
  position: absolute;
  top: 12%;
  left: 50%;
  transform: translateX(-50%);
  font-size: 13px;
  color: #e2e8f0;
  background: rgba(15, 23, 42, 0.75);
  padding: 6px 12px;
  border-radius: var(--yb-radius-pill);
  pointer-events: none;
}
</style>
