<script setup lang="ts">
import { ref } from "vue";
import { startDrag } from "../lib/window";

// 抽象团子：SVG 角色。身体渐变 + 呼吸 squash-stretch + 周期眨眼；表情按 state 切换。
// size：常态球 64 / chat 头部 44。状态色通过身体周围的光晕（halo）表达。
const props = withDefaults(
  defineProps<{ state: "idle" | "listen" | "think" | "work" | "say"; size?: number }>(),
  { size: 64 },
);
const emit = defineEmits<{ (e: "click"): void; (e: "longpress"): void }>();

// 拖动 vs 点击 vs 长按：pointerdown 记坐标并起 450ms 计时；
// 移动 >4px 触发 startDragging（取消计时）；到点未动未抬 = 长按（voice）；提前抬起且未拖 = click。
// 不用 data-tauri-drag-region（它会吞掉 click）。
const THRESHOLD = 4;
const LONGPRESS_MS = 450;
let down: { x: number; y: number } | null = null;
let dragging = false;
let longFired = false;
let timer: ReturnType<typeof setTimeout> | null = null;
const holding = ref(false);

function cancelTimer() {
  if (timer !== null) {
    clearTimeout(timer);
    timer = null;
  }
  holding.value = false;
}

function onPointerDown(e: PointerEvent) {
  if (e.button !== 0) return;
  down = { x: e.clientX, y: e.clientY };
  dragging = false;
  longFired = false;
  holding.value = true;
  (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
  timer = setTimeout(() => {
    if (down && !dragging) {
      longFired = true;
      emit("longpress");
    }
    cancelTimer();
  }, LONGPRESS_MS);
}

async function onPointerMove(e: PointerEvent) {
  if (!down || dragging) return;
  if (Math.hypot(e.clientX - down.x, e.clientY - down.y) > THRESHOLD) {
    dragging = true;
    cancelTimer();
    await startDrag(); // 必须在用户手势链内调用
  }
}

function onPointerUp() {
  cancelTimer();
  if (down && !dragging && !longFired) emit("click");
  down = null;
  dragging = false;
  longFired = false;
}
</script>

<template>
  <div
    class="av"
    :class="[state, { holding }]"
    :style="{ width: props.size + 'px', height: props.size + 'px' }"
    @pointerdown.prevent="onPointerDown"
    @pointermove="onPointerMove"
    @pointerup="onPointerUp"
    @pointercancel="onPointerUp"
  >
    <svg viewBox="0 0 100 100" class="dumpling" aria-hidden="true">
      <defs>
        <radialGradient id="yb-body" cx="42%" cy="32%" r="78%">
          <stop offset="0%" class="body-hi" />
          <stop offset="100%" class="body-lo" />
        </radialGradient>
      </defs>

      <!-- 状态光晕 -->
      <circle class="halo" cx="50" cy="54" r="44" />

      <!-- 身体（呼吸 squash-stretch 在这层） -->
      <g class="body">
        <ellipse cx="50" cy="54" rx="34" ry="30" fill="url(#yb-body)" />
        <!-- 腮红 -->
        <ellipse class="blush" cx="33" cy="62" rx="5.5" ry="3.2" />
        <ellipse class="blush" cx="67" cy="62" rx="5.5" ry="3.2" />

        <!-- 眼睛（think 时瞳孔上移） -->
        <g class="eyes" :class="{ 'eyes-up': state === 'think' }">
          <ellipse class="eye" cx="40" cy="50" rx="3.4" ry="4.6" />
          <ellipse class="eye" cx="60" cy="50" rx="3.4" ry="4.6" />
        </g>

        <!-- 嘴型按状态 -->
        <path v-if="state === 'idle'" class="mouth" d="M43 60 Q50 66 57 60" fill="none" />
        <ellipse v-else-if="state === 'listen'" class="mouth-fill" cx="50" cy="62" rx="3.6" ry="4.4" />
        <path v-else-if="state === 'think'" class="mouth" d="M45 63 Q50 61.5 55 63" fill="none" />
        <path v-else-if="state === 'work'" class="mouth" d="M44 62.5 L56 62.5" fill="none" />
        <g v-else>
          <ellipse class="mouth-fill" cx="50" cy="62" rx="4.6" ry="5.4" />
          <ellipse class="tongue" cx="50" cy="64.5" rx="2.6" ry="1.8" />
        </g>

        <!-- 汗滴（work） -->
        <path v-if="state === 'work'" class="sweat" d="M74 34 q3.5 5 0 7.5 q-3.5 -2.5 0 -7.5" />
      </g>

      <!-- 思考气泡点（think） -->
      <g v-if="state === 'think'" class="think-dots">
        <circle cx="76" cy="34" r="2" />
        <circle cx="81" cy="27" r="2.6" />
        <circle cx="87" cy="19" r="3.2" />
      </g>

      <!-- 声波弧（listen 左侧 / say 右侧） -->
      <g v-if="state === 'listen'" class="waves waves-l">
        <path d="M14 46 q-5 8 0 16" />
        <path d="M8 42 q-8 12 0 24" />
      </g>
      <g v-else-if="state === 'say'" class="waves waves-r">
        <path d="M86 46 q5 8 0 16" />
        <path d="M92 42 q8 12 0 24" />
      </g>
    </svg>
  </div>
</template>

<style scoped>
.av {
  position: relative;
  width: 64px;
  height: 64px;
  cursor: grab;
  user-select: none;
  touch-action: none;
}
.av:active {
  cursor: grabbing;
}
/* 按住反馈：团子微微变大，提示继续按住 = 语音 */
.av.holding .dumpling {
  transform: scale(1.08);
  transition: all 0.45s ease;
}
.av .dumpling {
  transition: all 0.15s ease;
}
.dumpling {
  width: 100%;
  height: 100%;
  display: block;
  overflow: visible;
  /* 柔和投影：让团子从毛玻璃底上轻轻浮起 */
  filter: drop-shadow(0 1px 2px rgba(90, 70, 50, 0.06)) drop-shadow(0 6px 16px rgba(90, 70, 50, 0.08));
}

/* ---- 颜色映射 ---- */
.body-hi {
  stop-color: var(--yb-dumpling-hi);
}
.body-lo {
  stop-color: var(--yb-dumpling-lo);
}
.halo {
  fill: var(--ring, var(--yb-idle));
  opacity: 0.14;
  transition: fill 0.15s ease;
}
.eye,
.think-dots circle {
  fill: var(--yb-dumpling-ink);
}
.mouth {
  stroke: var(--yb-dumpling-ink);
  stroke-width: 2.6;
  stroke-linecap: round;
}
.mouth-fill {
  fill: var(--yb-dumpling-ink);
}
.tongue {
  fill: var(--yb-dumpling-blush);
}
.blush {
  fill: var(--yb-dumpling-blush);
  opacity: 0.75;
}
.sweat {
  fill: var(--yb-listen);
}
.waves path {
  stroke: var(--ring, var(--yb-idle));
  stroke-width: 2.4;
  stroke-linecap: round;
  fill: none;
  transition: stroke 0.15s ease;
}

/* ---- 状态色 ---- */
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

/* ---- 动画 ---- */
/* 呼吸：身体轻微 squash-stretch */
.body {
  transform-box: fill-box;
  transform-origin: 50% 88%;
  animation: breathe 3.2s infinite ease-in-out;
}
@keyframes breathe {
  0%,
  100% {
    transform: scale(1, 1);
  }
  50% {
    transform: scale(1.03, 0.965);
  }
}

/* 眨眼：每 4s 快速闭合一次 */
.eye {
  transform-box: fill-box;
  transform-origin: center;
  animation: blink 4.2s infinite;
}
@keyframes blink {
  0%,
  91%,
  100% {
    transform: scaleY(1);
  }
  94%,
  96% {
    transform: scaleY(0.08);
  }
}

/* think：双眼上移 */
.eyes {
  transition: transform 0.15s ease;
}
.eyes-up {
  transform: translateY(-2.5px);
}

/* 活跃状态：光晕呼吸 */
.av.listen .halo,
.av.think .halo,
.av.work .halo,
.av.say .halo {
  animation: halo-pulse 1.4s infinite alternate ease-in-out;
}
@keyframes halo-pulse {
  from {
    opacity: 0.08;
    transform: scale(0.97);
  }
  to {
    opacity: 0.22;
    transform: scale(1.05);
  }
}
.halo {
  transform-box: fill-box;
  transform-origin: center;
}

/* 声波弧渐次闪烁 */
.waves path {
  animation: wave 1.1s infinite ease-in-out;
}
.waves path:last-child {
  animation-delay: 0.25s;
}
@keyframes wave {
  0%,
  100% {
    opacity: 0.25;
  }
  50% {
    opacity: 0.9;
  }
}

/* 思考点漂浮 */
.think-dots circle {
  animation: think-float 1.6s infinite ease-in-out;
}
.think-dots circle:nth-child(2) {
  animation-delay: 0.2s;
}
.think-dots circle:nth-child(3) {
  animation-delay: 0.4s;
}
@keyframes think-float {
  0%,
  100% {
    opacity: 0.3;
    transform: translateY(0);
  }
  50% {
    opacity: 0.9;
    transform: translateY(-1.5px);
  }
}

/* 汗滴滑落 */
.sweat {
  animation: sweat-drop 1.8s infinite ease-in;
}
@keyframes sweat-drop {
  0% {
    opacity: 0;
    transform: translateY(0);
  }
  25% {
    opacity: 1;
  }
  75% {
    opacity: 1;
    transform: translateY(5px);
  }
  100% {
    opacity: 0;
    transform: translateY(8px);
  }
}
</style>
