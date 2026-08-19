<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { getMemListOnce, onBrainEvent, type MemItem, type FeedStats } from "../lib/brain";
import NeuralBrain from "./NeuralBrain.vue";
import Avatar from "./Avatar.vue";
import HomeWidget from "./HomeWidget.vue";

type AgentState = "idle" | "listen" | "think" | "work" | "say" | "success" | "error";
type CapabilityKind = "sense" | "think" | "act";

interface PluginInfo { id: string; name: string }
interface MemoryPoint {
  id: string;
  text: string;
  full: string;
  x: number;
  y: number;
  tone: number;
  fresh: boolean;
}

const props = defineProps<{ state: AgentState; compact?: boolean }>();
const emit = defineEmits<{ chat: [draft: string]; toggle: [] }>();

const memories = ref<MemItem[]>([]);
const plugins = ref<PluginInfo[]>([]);
const loaded = ref(false);
const memFailed = ref(false);
const stats = ref<FeedStats>({ pending_reminders: 0, running_tasks: 0, done_24h: 0, unread: 0, ignored: 0 });
const todayChats = ref(0);
const activeCapability = ref<CapabilityKind | null>(null);
const freshIds = ref<Set<string>>(new Set());
let lastMemIds = new Set<string>();

function hashStr(s: string): number {
  let h = 5381;
  for (let i = 0; i < s.length; i += 1) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

function memoryLabel(m: MemItem): string {
  const parts = m.text.replace(/[，。！？、；：""''（）【】\n]/g, " ").split(/\s+/).filter(Boolean);
  const label = (parts.slice(0, 2).join("·") || m.text.slice(0, 8)).trim();
  return label.length > 10 ? `${label.slice(0, 10)}…` : label;
}

const MEMORY_POSITIONS = [
  { x: 19, y: 24 }, { x: 77, y: 22 }, { x: 11, y: 56 },
  { x: 86, y: 56 }, { x: 28, y: 83 }, { x: 71, y: 82 },
];

const memoryPoints = computed<MemoryPoint[]>(() =>
  memories.value.slice(0, MEMORY_POSITIONS.length).map((m, index) => ({
    id: m.id,
    text: memoryLabel(m),
    full: m.text,
    x: MEMORY_POSITIONS[index].x,
    y: MEMORY_POSITIONS[index].y,
    tone: hashStr(m.id) % 3,
    fresh: freshIds.value.has(m.id),
  })),
);

async function refreshMem() {
  const result = await getMemListOnce();
  const newIds = new Set(result.items.map((item) => item.id));
  if (lastMemIds.size) {
    const added = new Set([...newIds].filter((id) => !lastMemIds.has(id)));
    if (added.size) {
      freshIds.value = added;
      window.setTimeout(() => { freshIds.value = new Set(); }, 1800);
    }
  }
  lastMemIds = newIds;
  memories.value = result.items;
  memFailed.value = result.failed;
  loaded.value = true;
}

async function refreshFeedStats() {
  try {
    const result = await invoke<FeedStats | null>("get_feed_stats", { days: 1 });
    if (result && typeof result === "object") {
      stats.value = {
        pending_reminders: Number(result.pending_reminders) || 0,
        running_tasks: Number(result.running_tasks) || 0,
        done_24h: Number(result.done_24h) || 0,
        unread: Number(result.unread) || 0,
        ignored: Number(result.ignored) || 0,
      };
    }
  } catch { /* sidecar unavailable: keep a stable zero state */ }

  try {
    const result = await invoke<{ items?: { ts: number }[] } | null>("get_feed", { limit: 200 });
    if (result && Array.isArray(result.items)) {
      const today = Math.floor(new Date().setHours(0, 0, 0, 0) / 1000);
      todayChats.value = result.items.filter((item) => item.ts >= today).length;
    }
  } catch { /* keep previous value */ }
}

const todayNewMems = computed(() => {
  const today = Math.floor(new Date().setHours(0, 0, 0, 0) / 1000);
  return memories.value.filter((item) => item.created_at && Math.floor(new Date(item.created_at).getTime() / 1000) >= today).length;
});

function useCountUp(target: () => number) {
  const display = ref(0);
  watch(target, (value) => {
    const from = display.value;
    const to = Number(value) || 0;
    if (from === to || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      display.value = to;
      return;
    }
    const start = performance.now();
    const tick = (now: number) => {
      const progress = Math.min(1, (now - start) / 520);
      display.value = Math.round(from + (to - from) * (1 - Math.pow(1 - progress, 3)));
      if (progress < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }, { immediate: true });
  return display;
}

const doneDisplay = useCountUp(() => stats.value.done_24h);
const chatsDisplay = useCountUp(() => todayChats.value);
const memsDisplay = useCountUp(() => todayNewMems.value);

const STATE_LINES: Record<AgentState, string> = {
  idle: "安静待命",
  listen: "正在感知",
  think: "正在思考",
  work: "正在行动",
  say: "正在回应",
  success: "刚刚完成",
  error: "需要留意",
};
const stateText = computed(() => STATE_LINES[props.state]);
const stateDetail = computed(() => {
  if (props.state === "listen") return "耳与眼正在接收上下文";
  if (props.state === "think") return "记忆与计划正在连接";
  if (props.state === "work") return "正在调用能力完成任务";
  if (props.state === "error") return "有一步没有按预期完成";
  return "记忆、感知与行动保持连接";
});

const capabilityGroups = computed(() => [
  { id: "sense" as const, label: "感知", count: 3, detail: "屏幕 · 语音 · 上下文" },
  { id: "think" as const, label: "思考", count: memories.value.length, detail: "记忆 · 规划 · 调度" },
  { id: "act" as const, label: "行动", count: plugins.value.length, detail: "工具 · 编码 · 自动化" },
]);
const activeGroup = computed(() => capabilityGroups.value.find((group) => group.id === activeCapability.value));

function toggleCapability(id: CapabilityKind) {
  activeCapability.value = activeCapability.value === id ? null : id;
}

function askMemory(point: Pick<MemoryPoint, "full">) {
  emit("chat", `关于「${point.full.length > 40 ? `${point.full.slice(0, 40)}…` : point.full}」：`);
}

function greet() {
  emit("chat", "查看当前状态、记忆和可用能力");
}

function launchSkill(plugin: PluginInfo) {
  emit("chat", `打开${plugin.name}面板`);
}

let memTimer: ReturnType<typeof setInterval> | null = null;
let unBrain: (() => void) | null = null;
onMounted(async () => {
  try { await refreshMem(); } catch { loaded.value = true; }
  try { plugins.value = await invoke<PluginInfo[]>("list_plugins").catch(() => []); } catch { plugins.value = []; }
  await refreshFeedStats();
  memTimer = setInterval(() => { void refreshMem(); void refreshFeedStats(); }, 45000);
  try {
    unBrain = await onBrainEvent((event) => {
      if (event.kind === "final_reply" || event.kind === "action_result") {
        void refreshMem();
        void refreshFeedStats();
      }
    });
  } catch { /* event stream unavailable */ }
});

onUnmounted(() => {
  if (memTimer) clearInterval(memTimer);
  unBrain?.();
});
</script>

<template>
  <aside class="agent" :class="[state, { compact }]">
    <HomeWidget id="identity" class="identity-widget">
      <button class="identity" type="button" title="和译宝聊聊它的记忆与能力" @click="greet">
        <span class="identity-avatar" title="折叠左栏" @click.stop="emit('toggle')"><Avatar :state="state" :size="36" compact /></span>
        <span class="identity-copy">
          <span class="identity-line"><strong>译宝</strong><i class="state-dot" />{{ stateText }}</span>
          <span>{{ stateDetail }}</span>
        </span>
      </button>
    </HomeWidget>

    <HomeWidget id="mind" class="mind-widget" aria-label="译宝的记忆、感知和行动能力">
      <h2 class="yb-widget-head">认知</h2>
      <div class="mind-well">
        <NeuralBrain
          :state="state"
          :state-text="stateText"
          :memories="memoryPoints"
          :loaded="loaded"
          :mem-failed="memFailed"
          :sense-count="3"
          :think-count="memories.length"
          :act-count="plugins.length"
          :active-capability="activeCapability"
          @capability="toggleCapability"
          @memory="askMemory"
          @status="greet"
        />
      </div>

      <Transition name="cap-detail">
        <div v-if="activeGroup" class="capability-detail">
          <span class="cap-detail-title">{{ activeGroup.label }}</span>
          <span class="cap-detail-copy">{{ activeGroup.detail }}</span>
          <div v-if="activeGroup.id === 'act' && plugins.length" class="skill-links">
            <button v-for="plugin in plugins.slice(0, 3)" :key="plugin.id" type="button" @click="launchSkill(plugin)">
              {{ plugin.name }}
            </button>
          </div>
        </div>
      </Transition>
    </HomeWidget>

    <HomeWidget id="today" class="today-widget" aria-label="今日概要">
      <h2 class="yb-widget-head">今日</h2>
      <div class="today-cells">
        <span class="today-cell"><b>{{ doneDisplay }}</b>完成</span>
        <span class="today-cell"><b>{{ chatsDisplay }}</b>对话</span>
        <span class="today-cell"><b>{{ memsDisplay }}</b>记忆</span>
      </div>
    </HomeWidget>
  </aside>
</template>

<style scoped>
.agent {
  display: contents;
  color: var(--yb-paper-ink);
  user-select: none;
}

button {
  font: inherit;
}

.identity {
  width: 100%;
  min-width: 0;
  padding: 10px 40px 10px 12px;
  border: 0;
  background: transparent;
  display: flex;
  align-items: center;
  gap: 10px;
  color: inherit;
  text-align: left;
  cursor: pointer;
}

/* 本尊 Avatar：纯展示（状态灯/眨眼仍动），禁用手势——避免左栏内拖动误移窗口 */
.identity-avatar {
  flex: none;
  cursor: pointer;
}
.identity-avatar :deep(.av) {
  cursor: pointer;
}

.identity-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
  font-size: 11px;
  color: var(--yb-paper-ink-dim);
  white-space: nowrap;
}

.identity-line {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--yb-paper-ink-dim);
}

.identity-line strong {
  margin-right: 2px;
  font-size: 13px;
  color: var(--yb-paper-ink);
}

.state-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--yb-state-idle);
}

.listen .state-dot { background: var(--yb-state-listen); }
.think .state-dot { background: var(--yb-state-think); }
.work .state-dot { background: var(--yb-state-work); }
.say .state-dot { background: var(--yb-state-say); }
.success .state-dot { background: var(--yb-state-success); }
.error .state-dot { background: var(--yb-state-error); }

.mind-widget {
  flex: none;
}

.mind-well {
  margin: 0 8px 8px;
  overflow: hidden;
  border-radius: calc(var(--yb-widget-radius) - 6px);
  background: var(--yb-note-mute);
  box-shadow: var(--yb-press);
}

.capability-detail {
  margin: 0 8px 8px;
  padding: 6px 8px;
  min-height: 0;
  border-radius: calc(var(--yb-widget-radius) - 6px);
  display: flex;
  align-items: center;
  gap: 6px;
  background: var(--yb-note-mute);
  box-shadow: var(--yb-press);
  color: var(--yb-paper-ink-dim);
}

.cap-detail-title { font-size: 11px; font-weight: var(--yb-fw-bold); color: var(--yb-paper-ink); }
.cap-detail-copy { flex: 1; min-width: 0; font-size: 10px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.skill-links { display: flex; gap: 4px; }
.skill-links button {
  max-width: 64px;
  padding: 3px 6px;
  border: 0;
  border-radius: 7px;
  background: var(--yb-widget-bg);
  color: var(--yb-accent-deep);
  font-size: 9px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  cursor: pointer;
}

.cap-detail-enter-active,
.cap-detail-leave-active { transition: opacity 180ms var(--yb-ease-out), transform 180ms var(--yb-ease-out); }
.cap-detail-enter-from,
.cap-detail-leave-to { opacity: 0; transform: translateY(-5px); }

.today-cells {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  margin: 2px 8px 8px;
  overflow: hidden;
  border-radius: calc(var(--yb-widget-radius) - 6px);
  background: var(--yb-note-mute);
  box-shadow: var(--yb-press);
}

.today-cell {
  padding: 8px 4px 9px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  font-size: 10px;
  color: var(--yb-paper-ink-dim);
}

.today-cell + .today-cell {
  box-shadow: inset 1px 0 0 var(--yb-line);
}

.today-cell b {
  color: var(--yb-accent-deep);
  font-size: 15px;
  font-weight: var(--yb-fw-bold);
  font-variant-numeric: tabular-nums;
  line-height: 1.1;
}

</style>
