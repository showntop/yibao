<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import brainShellUrl from "../assets/brain-shell.png";

type AgentState = "idle" | "listen" | "think" | "work" | "say" | "success" | "error";
type CapabilityKind = "sense" | "think" | "act";

interface NeuralMemory {
  id: string;
  text: string;
  full: string;
  fresh: boolean;
}

const props = withDefaults(defineProps<{
  state: AgentState;
  stateText: string;
  memories: NeuralMemory[];
  loaded: boolean;
  memFailed: boolean;
  senseCount: number;
  thinkCount: number;
  actCount: number;
  activeCapability: CapabilityKind | null;
  density?: "map" | "tile";
}>(), { density: "map" });

const emit = defineEmits<{
  capability: [kind: CapabilityKind];
  memory: [memory: NeuralMemory];
  status: [];
}>();

const canvas = ref<HTMLCanvasElement | null>(null);
const stage = ref<HTMLElement | null>(null);
let context: CanvasRenderingContext2D | null = null;
let resizeObserver: ResizeObserver | null = null;
let frame = 0;
let width = 0;
let height = 0;
let reducedMotion = false;

const memoryPositions = [
  { x: 22, y: 45 },
  { x: 30, y: 65 },
  { x: 43, y: 78 },
  { x: 18, y: 72 },
];

const visibleMemories = computed(() => {
  if (props.density === "tile") return [];
  return props.memories.slice(0, memoryPositions.length).map((memory, index) => ({
    ...memory,
    ...memoryPositions[index],
  }));
});

const graphPoints = [
  [0.22, 0.22], [0.76, 0.21], [0.51, 0.49], [0.73, 0.74],
  [0.22, 0.45], [0.30, 0.65], [0.43, 0.78], [0.18, 0.72],
  [0.35, 0.27], [0.47, 0.22], [0.62, 0.28], [0.68, 0.39],
  [0.35, 0.43], [0.42, 0.55], [0.59, 0.58], [0.64, 0.68],
  [0.47, 0.69], [0.55, 0.76], [0.79, 0.50], [0.81, 0.64],
  [0.16, 0.34], [0.27, 0.33], [0.39, 0.18], [0.57, 0.18],
  [0.84, 0.35], [0.84, 0.78], [0.60, 0.84], [0.31, 0.82],
] as const;

const graphEdges = [
  [0, 8], [0, 20], [0, 21], [1, 10], [1, 23], [1, 24],
  [8, 9], [8, 12], [9, 2], [9, 10], [10, 2], [10, 11],
  [20, 4], [21, 4], [4, 12], [4, 5], [12, 2], [12, 13],
  [5, 13], [5, 7], [5, 6], [7, 27], [6, 16], [6, 17],
  [13, 2], [13, 16], [2, 11], [2, 14], [2, 16], [2, 10],
  [11, 18], [11, 14], [14, 15], [14, 18], [15, 3], [15, 17],
  [16, 17], [17, 26], [18, 19], [19, 3], [19, 25], [3, 25],
  [23, 10], [24, 18], [26, 3], [27, 16], [22, 8], [22, 9],
  [20, 8], [21, 12], [22, 12], [23, 11], [24, 11], [24, 19],
  [27, 13], [27, 5], [26, 15], [25, 15], [8, 13], [9, 13],
  [10, 14], [11, 15], [12, 16], [13, 14], [14, 16], [15, 16],
  [4, 13], [5, 16], [6, 13], [18, 14], [19, 15], [3, 17],
] as const;

const activeRoute = [0, 4, 12, 2, 14, 15, 3] as const;
// 旁路脉冲：另一条活跃路径（跨感知→行动的记忆链路），让网络更"活"，不只一条干线发光
const ambientRoute = [22, 9, 13, 5, 16, 17, 26] as const;

function nodePoint(index: number) {
  const point = graphPoints[index];
  return { x: point[0] * width, y: point[1] * height };
}

function curveControl(a: { x: number; y: number }, b: { x: number; y: number }, seed: number) {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const length = Math.max(1, Math.hypot(dx, dy));
  const bend = ((seed % 2 ? 1 : -1) * (5 + (seed % 4) * 2));
  return {
    x: (a.x + b.x) / 2 - (dy / length) * bend,
    y: (a.y + b.y) / 2 + (dx / length) * bend,
  };
}

function quadraticPoint(a: { x: number; y: number }, control: { x: number; y: number }, b: { x: number; y: number }, t: number) {
  const m = 1 - t;
  return {
    x: m * m * a.x + 2 * m * t * control.x + t * t * b.x,
    y: m * m * a.y + 2 * m * t * control.y + t * t * b.y,
  };
}

function drawCurve(aIndex: number, bIndex: number, alpha: number, lineWidth = 0.7) {
  if (!context) return;
  const a = nodePoint(aIndex);
  const b = nodePoint(bIndex);
  const control = curveControl(a, b, aIndex * 31 + bIndex * 17);
  context.beginPath();
  context.moveTo(a.x, a.y);
  context.quadraticCurveTo(control.x, control.y, b.x, b.y);
  context.strokeStyle = `rgba(77, 144, 196, ${alpha})`;
  context.lineWidth = lineWidth;
  context.stroke();
}

function stateSpeed() {
  if (props.state === "think") return 0.00011;
  if (props.state === "work") return 0.00014;
  if (props.state === "listen") return 0.000075;
  if (props.state === "success") return 0.000065;
  return 0.000045;
}

/** 沿一条路径播撒光脉冲（activeRoute 主链 + ambientRoute 旁路共用）。 */
function pulseAlong(route: readonly number[], count: number, speed: number, alpha: number, time: number) {
  if (!context) return;
  const segmentCount = route.length - 1;
  for (let index = 0; index < count; index += 1) {
    const progress = (time * speed + index / count) % 1;
    const scaled = progress * segmentCount;
    const segment = Math.min(segmentCount - 1, Math.floor(scaled));
    const local = scaled - segment;
    const a = nodePoint(route[segment]);
    const b = nodePoint(route[segment + 1]);
    const control = curveControl(a, b, route[segment] * 31 + route[segment + 1] * 17);
    const point = quadraticPoint(a, control, b, local);
    const gradient = context.createRadialGradient(point.x, point.y, 0, point.x, point.y, 4);
    gradient.addColorStop(0, `rgba(77, 144, 196, ${alpha})`);
    gradient.addColorStop(1, "rgba(77, 144, 196, 0)");
    context.beginPath();
    context.arc(point.x, point.y, 4, 0, Math.PI * 2);
    context.fillStyle = gradient;
    context.fill();
  }
}

function draw(time: number) {
  if (!context || !width || !height) return;
  context.clearRect(0, 0, width, height);
  context.lineCap = "round";

  graphEdges.forEach(([a, b], index) => drawCurve(a, b, 0.08 + (index % 4) * 0.01, index % 7 === 0 ? 0.85 : 0.62));
  for (let index = 0; index < activeRoute.length - 1; index += 1) {
    drawCurve(activeRoute[index], activeRoute[index + 1], props.state === "idle" ? 0.14 : 0.22, 0.9);
  }

  graphPoints.forEach((_, index) => {
    const point = nodePoint(index);
    const important = index < 8;
    context!.beginPath();
    context!.arc(point.x, point.y, important ? 1.45 : 0.9, 0, Math.PI * 2);
    context!.fillStyle = important ? "rgba(77, 144, 196, 0.36)" : "rgba(77, 144, 196, 0.16)";
    context!.fill();
  });

  if (!reducedMotion) {
    pulseAlong(activeRoute, props.state === "idle" ? 2 : 3, stateSpeed(), 0.45, time);
    pulseAlong(ambientRoute, 1, stateSpeed() * 0.68, 0.28, time);
  }

  if (!reducedMotion) frame = requestAnimationFrame(draw);
}

function resizeCanvas() {
  if (!canvas.value || !stage.value) return;
  const rect = stage.value.getBoundingClientRect();
  const ratio = Math.min(2, window.devicePixelRatio || 1);
  width = rect.width;
  height = rect.height;
  canvas.value.width = Math.round(width * ratio);
  canvas.value.height = Math.round(height * ratio);
  canvas.value.style.width = `${width}px`;
  canvas.value.style.height = `${height}px`;
  context = canvas.value.getContext("2d");
  context?.setTransform(ratio, 0, 0, ratio, 0, 0);
  cancelAnimationFrame(frame);
  draw(performance.now());
}

watch(() => props.state, () => { void nextTick(resizeCanvas); });
watch(() => props.density, () => { void nextTick(resizeCanvas); });

onMounted(() => {
  reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  resizeObserver = new ResizeObserver(resizeCanvas);
  if (stage.value) resizeObserver.observe(stage.value);
  resizeCanvas();
});

onUnmounted(() => {
  cancelAnimationFrame(frame);
  resizeObserver?.disconnect();
});
</script>

<template>
  <div
    class="neural-brain"
    :class="[`state-${state}`, `density-${density}`]"
    :style="{ '--brain-mask': `url(${brainShellUrl})` }"
  >
    <div ref="stage" class="brain-stage">
      <div class="brain-glaze" aria-hidden="true" />
      <canvas ref="canvas" class="neural-network" aria-hidden="true" />

      <button
        class="synapse functional sense-node"
        type="button"
        :class="{ active: activeCapability === 'sense' }"
        :aria-expanded="activeCapability === 'sense'"
        :tabindex="density === 'tile' ? -1 : 0"
        :aria-hidden="density === 'tile'"
        @click="emit('capability', 'sense')"
      >
        <i /><span>感知 <b>{{ senseCount }}</b></span>
      </button>

      <button
        class="synapse functional think-node"
        type="button"
        :class="{ active: activeCapability === 'think' }"
        :aria-expanded="activeCapability === 'think'"
        :tabindex="density === 'tile' ? -1 : 0"
        :aria-hidden="density === 'tile'"
        @click="emit('capability', 'think')"
      >
        <i /><span>思考 <b>{{ thinkCount }}</b></span>
      </button>

      <button
        class="synapse functional act-node"
        type="button"
        :class="{ active: activeCapability === 'act' }"
        :aria-expanded="activeCapability === 'act'"
        :tabindex="density === 'tile' ? -1 : 0"
        :aria-hidden="density === 'tile'"
        @click="emit('capability', 'act')"
      >
        <i /><span>行动 <b>{{ actCount }}</b></span>
      </button>

      <button
        v-for="memory in visibleMemories"
        :key="memory.id"
        class="synapse memory-node"
        :class="{ fresh: memory.fresh }"
        type="button"
        :style="{ left: `${memory.x}%`, top: `${memory.y}%` }"
        :title="memory.full"
        :aria-label="`记忆：${memory.full}`"
        @click="emit('memory', memory)"
      >
        <i /><span>{{ memory.text }}</span>
        <em class="mem-tip">{{ memory.full }}</em>
      </button>

      <button
        v-if="density === 'map' && loaded && !visibleMemories.length"
        class="synapse memory-node memory-placeholder"
        type="button"
        :aria-label="memFailed ? '记忆暂时离线' : '暂无记忆'"
        @click="emit('capability', 'think')"
      >
        <i /><span>{{ memFailed ? '记忆离线' : '记忆 0' }}</span>
      </button>
    </div>

    <div v-if="density === 'tile'" class="brain-legend" role="group" aria-label="感知、思考、行动">
      <button
        type="button"
        :class="{ active: activeCapability === 'sense' }"
        :aria-expanded="activeCapability === 'sense'"
        @click="emit('capability', 'sense')"
      >感知 <b>{{ senseCount }}</b></button>
      <button
        type="button"
        :class="{ active: activeCapability === 'think' }"
        :aria-expanded="activeCapability === 'think'"
        @click="emit('capability', 'think')"
      >思考 <b>{{ thinkCount }}</b></button>
      <button
        type="button"
        :class="{ active: activeCapability === 'act' }"
        :aria-expanded="activeCapability === 'act'"
        @click="emit('capability', 'act')"
      >行动 <b>{{ actCount }}</b></button>
    </div>
  </div>
</template>

<style scoped>
.neural-brain {
  position: relative;
  width: 100%;
  color: var(--yb-text-dim);
}

.brain-stage {
  position: relative;
  width: 100%;
  height: 156px;
  overflow: hidden;
}

.brain-glaze,
.neural-network {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  -webkit-mask-image: var(--brain-mask);
  -webkit-mask-size: 100% 100%;
  -webkit-mask-position: center;
  -webkit-mask-repeat: no-repeat;
  mask-image: var(--brain-mask);
  mask-size: 100% 100%;
  mask-position: center;
  mask-repeat: no-repeat;
}

/* 釉下彩：轮廓只当遮罩，不再贴雾状 PNG */
.brain-glaze {
  z-index: 0;
  background:
    radial-gradient(ellipse 62% 54% at 48% 46%, rgba(var(--yb-c-sky-rgb), 0.16), transparent 72%),
    color-mix(in srgb, var(--yb-accent) 11%, var(--yb-note-mute));
  filter: blur(0.7px);
}

.yb-widget--glass .brain-glaze {
  background:
    radial-gradient(ellipse 62% 54% at 48% 46%, rgba(158, 200, 232, 0.22), transparent 72%),
    rgba(158, 200, 232, 0.10);
}

.neural-network {
  z-index: 1;
}

.synapse {
  position: absolute;
  z-index: 3;
  width: 26px;
  height: 26px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--yb-text-dim);
  cursor: pointer;
  transform: translate(-50%, -50%);
}

.synapse i {
  position: absolute;
  left: 50%;
  top: 50%;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  transform: translate(-50%, -50%);
  background: var(--yb-accent);
  box-shadow: 0 0 0 3px rgba(var(--yb-c-sky-rgb), 0.08);
  transition: transform 180ms var(--yb-ease-out), box-shadow 180ms var(--yb-ease-out), background 180ms var(--yb-ease-out);
}

.synapse span {
  position: absolute;
  left: 11px;
  top: -8px;
  white-space: nowrap;
  color: var(--yb-paper-ink-dim);
  font-size: 10px;
  font-weight: var(--yb-fw-medium);
  line-height: 16px;
}

.synapse b {
  margin-left: 2px;
  color: var(--yb-accent-deep);
  font-size: 9px;
  font-variant-numeric: tabular-nums;
}

.synapse:hover i,
.synapse:focus-visible i,
.synapse.active i {
  transform: translate(-50%, -50%) scale(1.2);
  background: var(--yb-accent);
  box-shadow: 0 0 0 4px rgba(var(--yb-c-sky-rgb), 0.12);
}

.synapse:focus-visible {
  outline: none;
  box-shadow: none;
}
.synapse:focus-visible i {
  box-shadow:
    0 0 0 3px var(--yb-widget-bg, var(--yb-surface-1)),
    0 0 0 5px rgba(var(--yb-c-sky-rgb), 0.55);
}

.sense-node { left: 23%; top: 22%; }
.think-node { left: 52%; top: 49%; }
.act-node { left: 73%; top: 74%; }
.memory-placeholder { left: 23%; top: 50%; }

.functional i { width: 8px; height: 8px; }
.think-node { width: 28px; height: 28px; }
.think-node i {
  width: 10px;
  height: 10px;
  background: var(--yb-accent);
  box-shadow: 0 0 0 3px rgba(var(--yb-c-sky-rgb), 0.10);
}
.think-node span {
  left: 13px;
  top: -9px;
  color: var(--yb-paper-ink);
  font-size: 11px;
  font-weight: var(--yb-fw-bold);
}

.memory-node i { width: 6px; height: 6px; }
.memory-node span {
  max-width: 76px;
  overflow: hidden;
  text-overflow: ellipsis;
  opacity: 0.78;
}
/* hover 浮层：完整记忆（截断标签的补全）。
 * 关键：必须显式 width——父 .memory-node 有 transform 形成 containing block，
 * 中文 max-content=1ch 会让 shrink-to-fit 把绝对定位子元素压到单字宽竖排。 */
.mem-tip {
  position: absolute;
  left: 50%;
  bottom: 15px;
  width: 192px;
  padding: 6px 9px;
  border: 1px solid rgba(var(--yb-c-sky-rgb), 0.16);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.97);
  box-shadow: 0 10px 26px rgba(var(--yb-c-slate-rgb), 0.13);
  color: var(--yb-text);
  font-size: 10px;
  font-weight: var(--yb-fw-medium);
  line-height: 1.45;
  white-space: normal;
  word-break: break-word;
  opacity: 0;
  pointer-events: none;
  transform: translate(-50%, 3px);
  transition: opacity 150ms var(--yb-ease-out), transform 150ms var(--yb-ease-out);
  z-index: 12;
}
.memory-node:hover { z-index: 7; }
.memory-node:hover span,
.memory-node:focus-visible span { color: var(--yb-text-strong); opacity: 1; }
.memory-node:hover .mem-tip,
.memory-node:focus-visible .mem-tip { opacity: 1; transform: translate(-50%, 0); }
.memory-node.fresh i { animation: synapse-arrive 900ms var(--yb-ease-out) both; }

.state-think .think-node i {
  background: var(--yb-state-think);
  box-shadow: 0 0 0 4px rgba(var(--yb-c-sky-rgb), 0.10);
}
/* 思考中：中心节点外圈光环脉冲（与 canvas 加速的脉冲呼应，"大脑正在运转"） */
.state-think .think-node i::after {
  content: "";
  position: absolute;
  inset: -5px;
  border-radius: 50%;
  border: 1.5px solid rgba(var(--yb-c-sky-rgb), 0.5);
  animation: node-ring 1.8s ease-out infinite;
}
@keyframes node-ring {
  0% { transform: scale(0.8); opacity: 0.8; }
  100% { transform: scale(1.9); opacity: 0; }
}
.state-work .act-node i { background: var(--yb-state-work); }
.state-listen .sense-node i { background: var(--yb-state-listen); }
.state-success .act-node i { background: var(--yb-state-success); }
.state-error .think-node i { background: var(--yb-state-error); }

@keyframes synapse-arrive {
  0% { opacity: 0; transform: translate(-50%, -50%) scale(0.2); }
  55% { opacity: 1; transform: translate(-50%, -50%) scale(1.7); }
  100% { transform: translate(-50%, -50%) scale(1); }
}

.density-tile .brain-stage {
  height: 72px;
}
.density-tile .synapse.functional {
  pointer-events: none;
}
.density-tile .synapse span,
.density-tile .mem-tip {
  display: none;
}

.brain-legend {
  display: flex;
  align-items: stretch;
  margin: 0;
  overflow: hidden;
  border-top: 1px solid var(--yb-line);
}
.brain-legend button {
  flex: 1;
  min-width: 0;
  height: 28px;
  padding: 0 4px;
  border: 0;
  background: transparent;
  color: var(--yb-paper-ink-dim);
  font: inherit;
  font-size: 10px;
  font-weight: var(--yb-fw-medium);
  line-height: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  cursor: pointer;
  transition: color 160ms var(--yb-ease-out), background 160ms var(--yb-ease-out);
}
.brain-legend button + button {
  box-shadow: inset 1px 0 0 var(--yb-line);
}
.brain-legend button b {
  margin-left: 2px;
  color: var(--yb-accent-deep);
  font-size: 10px;
  font-variant-numeric: tabular-nums;
}
.brain-legend button:hover {
  color: var(--yb-paper-ink);
  background: color-mix(in srgb, var(--yb-accent) 8%, transparent);
}
.brain-legend button:focus-visible {
  z-index: 1;
  box-shadow: var(--yb-focus-ring);
}
.brain-legend button:active {
  transform: translateY(1px);
}
.brain-legend button.active {
  color: var(--yb-paper-ink);
  background: color-mix(in srgb, var(--yb-accent) 12%, transparent);
}

@media (prefers-reduced-motion: reduce) {
  .synapse i { transition: none; }
  .memory-node.fresh i { animation: none; }
  .state-think .think-node i::after { animation: none; }
  .brain-legend button { transition: none; }
}
</style>
