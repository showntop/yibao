<script setup lang="ts">
import { ref } from "vue";
import { startDrag } from "../lib/window";

// 译宝 · 天青鹅蛋角色：立体光影 + 小手 + 天线（兼状态灯）。
// 七态：idle/listen/think/work/say + 短暂 valence（success/error）。
// size：常态球 64 / 聊天头部 44。保留 click/longpress/drag 手势状态机。
const props = withDefaults(
  defineProps<{
    state: "idle" | "listen" | "think" | "work" | "say" | "success" | "error";
    size?: number;
  }>(),
  { size: 64 },
);
const emit = defineEmits<{ (e: "click"): void; (e: "longpress"): void }>();

// 拖动 vs 点击 vs 长按：pointerdown 记坐标并起 450ms 计时；
// 移动 >4px 触发 startDragging（取消计时）；到点未动未抬 = 长按（voice）；提前抬起且未拖 = click。
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

const INK = "var(--yb-body-ink)";
const BLUSH = "var(--yb-body-blush)";
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
    <svg viewBox="0 0 120 120" class="yb" aria-hidden="true">
      <defs>
        <linearGradient id="yb-body" x1="34%" y1="6%" x2="66%" y2="100%">
          <stop offset="0%" stop-color="var(--yb-body-hi)" />
          <stop offset="46%" stop-color="var(--yb-body-mid)" />
          <stop offset="100%" stop-color="var(--yb-body-lo)" />
        </linearGradient>
        <radialGradient id="yb-hi" cx="36%" cy="22%" r="32%">
          <stop offset="0%" stop-color="#ffffff" stop-opacity="0.95" />
          <stop offset="100%" stop-color="#ffffff" stop-opacity="0" />
        </radialGradient>
        <radialGradient id="yb-sh" cx="74%" cy="80%" r="46%">
          <stop offset="0%" stop-color="var(--yb-body-core-shadow)" stop-opacity="0.42" />
          <stop offset="100%" stop-color="var(--yb-body-core-shadow)" stop-opacity="0" />
        </radialGradient>
        <radialGradient id="yb-dot-glow" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stop-color="var(--dot)" stop-opacity="0.55" />
          <stop offset="100%" stop-color="var(--dot)" stop-opacity="0" />
        </radialGradient>
        <radialGradient id="yb-aura" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stop-color="var(--yb-accent)" stop-opacity="0.75" />
          <stop offset="60%" stop-color="var(--yb-accent)" stop-opacity="0.55" />
          <stop offset="100%" stop-color="var(--yb-accent)" stop-opacity="0" />
        </radialGradient>
        <clipPath id="yb-clip">
          <path d="M60 18 C76 18 84 34 84 57 C84 84 74 102 60 102 C46 102 36 84 36 57 C36 34 44 18 60 18 Z" />
        </clipPath>
        <filter id="yb-b1"><feGaussianBlur stdDeviation="1.4" /></filter>
        <filter id="yb-b2"><feGaussianBlur stdDeviation="3.2" /></filter>
        <filter id="yb-b3"><feGaussianBlur stdDeviation="5.5" /></filter>
      </defs>

      <!-- 落地投影 -->
      <ellipse cx="63" cy="112" rx="33" ry="6.5" fill="#3f372e" opacity="0.16" filter="url(#yb-b3)" />

      <!-- 氛围光晕：团子背后的淡淡天青底，idle 时缓慢呼吸（给存在感） -->
      <circle class="aura" cx="60" cy="60" r="58" fill="url(#yb-aura)" />

      <!-- 身体（呼吸在这层） -->
      <g class="body-grp">
        <path d="M60 18 C76 18 84 34 84 57 C84 84 74 102 60 102 C46 102 36 84 36 57 C36 34 44 18 60 18 Z" fill="url(#yb-body)" />
        <g clip-path="url(#yb-clip)">
          <rect x="28" y="10" width="86" height="100" fill="url(#yb-sh)" />
          <ellipse cx="44" cy="34" rx="28" ry="24" fill="url(#yb-hi)" />
          <ellipse cx="60" cy="106" rx="28" ry="10" fill="var(--yb-body-contact)" opacity="0.26" filter="url(#yb-b2)" />
        </g>
        <!-- 左侧边缘反光 -->
        <path d="M40 32 C37.6 47 37.8 73 44 94" fill="none" stroke="#ffffff" stroke-opacity="0.9" stroke-width="2.2" filter="url(#yb-b1)" />
        <!-- 小手 -->
        <ellipse cx="33" cy="64" rx="7.5" ry="11.5" fill="url(#yb-body)" transform="rotate(-14 33 64)" />
        <ellipse cx="34" cy="60" rx="3" ry="5" fill="#ffffff" opacity="0.5" transform="rotate(-14 34 60)" />
        <ellipse cx="87" cy="64" rx="7.5" ry="11.5" fill="url(#yb-body)" transform="rotate(14 87 64)" />
        <ellipse cx="88" cy="60" rx="3" ry="5" fill="var(--yb-body-core-shadow)" opacity="0.32" transform="rotate(14 88 60)" />
        <!-- 领巾（天青） -->
        <path d="M46 42 Q60 50 74 42" fill="none" stroke="var(--yb-accent)" stroke-width="3.2" stroke-linecap="round" />
        <path d="M46 41 Q60 49 74 41" fill="none" stroke="#ffffff" stroke-opacity="0.5" stroke-width="1" stroke-linecap="round" />

        <!-- 腮红（轻） -->
        <ellipse cx="47" cy="66" rx="4.2" ry="2.5" :fill="BLUSH" opacity="0.24" />
        <ellipse cx="73" cy="66" rx="4.2" ry="2.5" :fill="BLUSH" opacity="0.24" />

        <!-- 眼睛 / 嘴：按状态 -->
        <!-- idle -->
        <g v-if="state === 'idle'">
          <ellipse cx="51" cy="60" rx="3.2" ry="4.5" :fill="INK" />
          <ellipse cx="69" cy="60" rx="3.2" ry="4.5" :fill="INK" />
          <circle cx="52.2" cy="58.4" r="1.05" fill="#fff" />
          <circle cx="70.2" cy="58.4" r="1.05" fill="#fff" />
          <path d="M55 69 Q60 72 65 69" fill="none" :stroke="INK" stroke-width="2.4" stroke-linecap="round" />
        </g>
        <!-- listen -->
        <g v-else-if="state === 'listen'">
          <ellipse cx="51" cy="60" rx="3.2" ry="4.5" :fill="INK" />
          <ellipse cx="69" cy="60" rx="3.2" ry="4.5" :fill="INK" />
          <circle cx="52.2" cy="58.4" r="1.05" fill="#fff" />
          <circle cx="70.2" cy="58.4" r="1.05" fill="#fff" />
          <ellipse cx="60" cy="70" rx="2.8" ry="3.4" :fill="INK" />
        </g>
        <!-- think -->
        <g v-else-if="state === 'think'">
          <ellipse cx="51" cy="58.4" rx="3.2" ry="4.5" :fill="INK" />
          <ellipse cx="69" cy="58.4" rx="3.2" ry="4.5" :fill="INK" />
          <circle cx="52.2" cy="56.8" r="1.05" fill="#fff" />
          <circle cx="70.2" cy="56.8" r="1.05" fill="#fff" />
          <path d="M56 70 Q60 68.6 64 70" fill="none" :stroke="INK" stroke-width="2.4" stroke-linecap="round" />
        </g>
        <!-- work -->
        <g v-else-if="state === 'work'">
          <line x1="46.5" y1="54.5" x2="54" y2="56" :stroke="INK" stroke-width="1.8" stroke-linecap="round" />
          <line x1="73.5" y1="54.5" x2="66" y2="56" :stroke="INK" stroke-width="1.8" stroke-linecap="round" />
          <ellipse cx="51" cy="60.5" rx="3.2" ry="3.8" :fill="INK" />
          <ellipse cx="69" cy="60.5" rx="3.2" ry="3.8" :fill="INK" />
          <path d="M55 70 L65 70" fill="none" :stroke="INK" stroke-width="2.4" stroke-linecap="round" />
        </g>
        <!-- say -->
        <g v-else-if="state === 'say'">
          <ellipse cx="51" cy="60" rx="3.2" ry="4.5" :fill="INK" />
          <ellipse cx="69" cy="60" rx="3.2" ry="4.5" :fill="INK" />
          <circle cx="52.2" cy="58.4" r="1.05" fill="#fff" />
          <circle cx="70.2" cy="58.4" r="1.05" fill="#fff" />
          <ellipse cx="60" cy="70" rx="4" ry="4.6" :fill="INK" />
          <ellipse cx="60" cy="72.2" rx="2.2" ry="1.5" fill="#f0b8b8" opacity="0.85" />
        </g>
        <!-- success -->
        <g v-else-if="state === 'success'">
          <path d="M47 60.5 Q51 57.5 55 60.5" fill="none" :stroke="INK" stroke-width="2.4" stroke-linecap="round" />
          <path d="M65 60.5 Q69 57.5 73 60.5" fill="none" :stroke="INK" stroke-width="2.4" stroke-linecap="round" />
          <path d="M52 67 Q60 75 68 67" fill="none" :stroke="INK" stroke-width="2.6" stroke-linecap="round" />
        </g>
        <!-- error -->
        <g v-else>
          <path d="M47 61.5 Q51 64 55 61.5" fill="none" :stroke="INK" stroke-width="2.4" stroke-linecap="round" />
          <path d="M65 61.5 Q69 64 73 61.5" fill="none" :stroke="INK" stroke-width="2.4" stroke-linecap="round" />
          <path d="M55 71.5 Q60 69.2 65 71.5" fill="none" :stroke="INK" stroke-width="2.4" stroke-linecap="round" />
        </g>
      </g>

      <!-- 小短腿（破上下对称：底部两只小脚，一前一后略错开） -->
      <g class="feet">
        <ellipse cx="47" cy="104" rx="7.5" ry="5" fill="url(#yb-body)" transform="rotate(-9 47 104)" />
        <ellipse cx="73" cy="106" rx="7.5" ry="5" fill="url(#yb-body)" transform="rotate(11 73 106)" />
        <path d="M41 106 Q47 109 53 106" fill="none" stroke="var(--yb-body-core-shadow)" stroke-opacity="0.4" stroke-width="1.5" stroke-linecap="round" />
        <path d="M67 108 Q73 111 79 108" fill="none" stroke="var(--yb-body-core-shadow)" stroke-opacity="0.4" stroke-width="1.5" stroke-linecap="round" />
      </g>

      <!-- 天线 -->
      <line x1="60" y1="20" x2="60" y2="11" stroke="var(--yb-body-stem)" stroke-width="2" stroke-linecap="round" />
      <circle v-if="state === 'think'" class="ring" cx="60" cy="8" r="6.5" fill="none" stroke="var(--dot)" stroke-width="1.6" stroke-dasharray="3 3" />
      <g class="dot-grp">
        <circle cx="60" cy="8" r="6" fill="url(#yb-dot-glow)" />
        <circle cx="60" cy="8" r="3.4" fill="var(--dot)" />
      </g>

      <!-- success 星星 -->
      <path v-if="state === 'success'" class="spark" d="M86 26 l1.6 4.4 l4.4 1.6 l-4.4 1.6 l-1.6 4.4 l-1.6 -4.4 l-4.4 -1.6 l4.4 -1.6 Z" fill="#f2a03c" />
      <!-- error 汗滴 -->
      <path v-if="state === 'error'" d="M80 34 q2.6 3.4 0 5 q-2.6 -1.6 0 -5" fill="#6a9cc4" />

      <!-- 声波弧（listen 左 / say 右） -->
      <g v-if="state === 'listen'" class="waves">
        <path class="wave" d="M28 58 q-4 6 0 12" fill="none" stroke="var(--dot)" stroke-width="2.2" stroke-linecap="round" />
        <path class="wave" d="M23 53 q-7 11 0 22" fill="none" stroke="var(--dot)" stroke-width="2.2" stroke-linecap="round" />
      </g>
      <g v-else-if="state === 'say'" class="waves">
        <path class="wave" d="M92 58 q4 6 0 12" fill="none" stroke="var(--dot)" stroke-width="2.2" stroke-linecap="round" />
        <path class="wave" d="M97 53 q7 11 0 22" fill="none" stroke="var(--dot)" stroke-width="2.2" stroke-linecap="round" />
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
.yb {
  width: 100%;
  height: 100%;
  display: block;
  overflow: visible;
}

/* ---- 状态灯色（天线 dot）---- */
.av.idle { --dot: var(--yb-state-idle); }
.av.listen { --dot: var(--yb-state-listen); }
.av.think { --dot: var(--yb-state-think); }
.av.work { --dot: var(--yb-state-work); }
.av.say { --dot: var(--yb-state-say); }
.av.success { --dot: var(--yb-state-success); }
.av.error { --dot: var(--yb-state-error); }

/* ---- 动画基础 ---- */
.body-grp {
  transform-box: fill-box;
  transform-origin: 50% 92%;
  animation: breathe 3.4s infinite ease-in-out;
}
.dot-grp {
  transform-box: fill-box;
  transform-origin: center;
}
.ring {
  transform-box: fill-box;
  transform-origin: center;
}
.spark {
  transform-box: fill-box;
  transform-origin: center;
}
.wave {
  transform-box: fill-box;
  transform-origin: center;
}

/* 按住反馈：整体微放大，提示继续按住 = 语音 */
.av.holding .yb {
  transform: scale(1.06);
  transition: transform 0.45s ease;
}
.yb {
  transition: transform 0.15s ease;
}

@keyframes breathe {
  0%, 100% { transform: scale(1); }
  50% { transform: scale(1.02, 0.985); }
}

/* ---- 各态灯动效 ---- */
.av.idle .dot-grp { animation: dim 3s infinite ease-in-out; }
.aura { transform-box: fill-box; transform-origin: center; }
.av.idle .aura { animation: aura-breathe 4.8s infinite ease-in-out; }
.av.listen .dot-grp { animation: pulse 1.2s infinite ease-in-out; }
.av.think .ring { animation: spin 2.4s linear infinite; }
.av.work .dot-grp { animation: pulse 1.7s infinite ease-in-out; }
.av.say .dot-grp { animation: glow 1s infinite alternate ease-in-out; }
.av.success .spark { animation: pop 1.2s ease-out infinite; }
.av.error .dot-grp { animation: shake 0.5s infinite ease-in-out; }

/* listen / say 声波渐次闪烁 */
.av.listen .wave,
.av.say .wave { animation: wv 1.1s infinite ease-in-out; }
.av.listen .wave:nth-child(2),
.av.say .wave:nth-child(2) { animation-delay: 0.2s; }

@keyframes dim { 0%, 100% { opacity: 0.5; } 50% { opacity: 0.85; } }
@keyframes aura-breathe {
  0%, 100% { transform: scale(0.9); opacity: 0.5; }
  50% { transform: scale(1.12); opacity: 1; }
}
@keyframes pulse {
  0%, 100% { transform: scale(0.8); opacity: 0.6; }
  50% { transform: scale(1.2); opacity: 1; }
}
@keyframes glow { from { opacity: 0.65; } to { opacity: 1; } }
@keyframes spin { to { transform: rotate(360deg); } }
@keyframes wv { 0%, 100% { opacity: 0.25; } 50% { opacity: 0.9; } }
@keyframes pop {
  0% { transform: scale(0); opacity: 0; }
  55% { transform: scale(1.25); opacity: 1; }
  100% { transform: scale(1); opacity: 1; }
}
@keyframes shake {
  0%, 100% { transform: translateX(0); }
  25% { transform: translateX(-1.2px); }
  75% { transform: translateX(1.2px); }
}

@media (prefers-reduced-motion: reduce) {
  .body-grp, .dot-grp, .ring, .spark, .wave { animation: none !important; }
}
</style>
