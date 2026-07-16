<script setup lang="ts">
import { startDrag } from "../lib/window";

defineProps<{ state: "idle" | "listen" | "think" | "work" }>();
const emit = defineEmits<{ (e: "click"): void }>();

const faces: Record<string, string> = {
  idle: "😌",
  listen: "👂",
  think: "🤔",
  work: "⚙️",
};

// 拖动 vs 点击：pointerdown 记坐标，移动 >4px 触发 startDragging，否则 pointerup 算 click。
// 不用 data-tauri-drag-region（它会吞掉 click）。
const THRESHOLD = 4;
let down: { x: number; y: number } | null = null;
let dragging = false;

function onPointerDown(e: PointerEvent) {
  if (e.button !== 0) return;
  down = { x: e.clientX, y: e.clientY };
  dragging = false;
  (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
}

async function onPointerMove(e: PointerEvent) {
  if (!down || dragging) return;
  if (Math.hypot(e.clientX - down.x, e.clientY - down.y) > THRESHOLD) {
    dragging = true;
    await startDrag(); // 必须在用户手势链内调用
  }
}

function onPointerUp() {
  if (down && !dragging) emit("click");
  down = null;
  dragging = false;
}
</script>

<template>
  <div
    class="av"
    :class="state"
    @pointerdown.prevent="onPointerDown"
    @pointermove="onPointerMove"
    @pointerup="onPointerUp"
    @pointercancel="onPointerUp"
  >
    <span>{{ faces[state] }}</span>
  </div>
</template>

<style scoped>
.av {
  width: 64px;
  height: 64px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font-size: 34px;
  background: linear-gradient(145deg, rgba(255, 255, 255, 0.95), rgba(238, 241, 255, 0.85));
  border: 2px solid var(--yb-accent-soft);
  box-shadow: 0 4px 14px rgba(91, 108, 255, 0.2), inset 0 1px 0 rgba(255, 255, 255, 0.8);
  cursor: grab;
  user-select: none;
  touch-action: none;
  transition: transform 0.25s ease, border-color 0.25s ease;
}
.av:active {
  cursor: grabbing;
}
.av.think {
  border-color: rgba(91, 108, 255, 0.45);
}
.av.work {
  border-color: rgba(91, 108, 255, 0.7);
}
.av.think,
.av.work {
  animation: pulse 1.1s infinite alternate ease-in-out;
}
@keyframes pulse {
  from {
    opacity: 0.7;
    transform: scale(0.94);
  }
  to {
    opacity: 1;
    transform: scale(1.05);
  }
}
</style>
