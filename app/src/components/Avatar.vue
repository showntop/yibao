<script setup lang="ts">
import { startDrag } from "../lib/window";

// size：常态球 64 / chat 头部 44；状态用外圈色环表达（本体不缩放）
const props = withDefaults(
  defineProps<{ state: "idle" | "listen" | "think" | "work" | "say"; size?: number }>(),
  { size: 64 },
);
const emit = defineEmits<{ (e: "click"): void }>();

const faces: Record<string, string> = {
  idle: "😌",
  listen: "👂",
  think: "🤔",
  work: "⚙️",
  say: "💬",
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
    :style="{ width: props.size + 'px', height: props.size + 'px', fontSize: Math.round(props.size * 0.53) + 'px' }"
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
  position: relative;
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
}
.av:active {
  cursor: grabbing;
}
/* 状态环：外圈 2px 呼吸色环表达状态，pulse 动画只在环上（本体不缩放） */
.av::before {
  content: "";
  position: absolute;
  inset: -5px;
  border-radius: 50%;
  border: 2px solid var(--ring, var(--yb-idle));
  opacity: 0.55;
  pointer-events: none;
}
.av.idle {
  --ring: var(--yb-idle);
}
.av.listen {
  --ring: var(--yb-listen);
}
.av.think {
  --ring: var(--yb-think);
}
.av.work {
  --ring: var(--yb-work);
}
.av.say {
  --ring: var(--yb-say);
}
.av.listen::before,
.av.think::before,
.av.work::before,
.av.say::before {
  animation: ring-pulse 1.2s infinite alternate ease-in-out;
}
@keyframes ring-pulse {
  from {
    opacity: 0.35;
    transform: scale(0.97);
  }
  to {
    opacity: 0.9;
    transform: scale(1.04);
  }
}
</style>
