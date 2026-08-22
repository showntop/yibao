<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { invoke } from "@tauri-apps/api/core";
import {
  getDistillTimelineOnce,
  getMemListOnce,
  onBrainEvent,
  onPendingConfirms,
  type DistillDay,
  type MemItem,
  type FeedStats,
} from "../../lib/brain";
import { todayBands } from "../../lib/home/home-glance-faces.ts";
import { truncate } from "../../lib/text";
import NeuralBrain from "./NeuralBrain.vue";
import Avatar from "../../components/pet/Avatar.vue";
import HomeWidget from "../HomeWidget.vue";
import { useLiveAssembly } from "../../lib/home/home-chrome.ts";
import { faceOf } from "../../lib/home/home-assembly.ts";

type AgentState = "idle" | "listen" | "think" | "work" | "say" | "success" | "error";
type AvatarFace = AgentState | "notify";
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

const props = defineProps<{
  state: AgentState;
  compact?: boolean;
  only?: "identity" | "mind" | "today";
}>();
const emit = defineEmits<{ chat: [draft: string] }>();
const assembly = useLiveAssembly();
const mindFace = computed(() => {
  const face = faceOf(assembly.value, "mind", "map");
  return face === "tile" ? "tile" as const : "map" as const;
});
const identityFace = computed(() => faceOf(assembly.value, "identity", "tile"));

const memories = ref<MemItem[]>([]);
const plugins = ref<PluginInfo[]>([]);
const loaded = ref(false);
/** 今日概要的数据源是否已就绪（就绪前显示骨架，就绪后无数据则整块隐藏） */
const todayLoaded = ref(false);
const memFailed = ref(false);
const stats = ref<FeedStats>({ pending_reminders: 0, running_tasks: 0, done_24h: 0, unread: 0, ignored: 0 });
const todayChats = ref(0);
const todayDay = ref<DistillDay | null>(null);
const approvals = ref(0);
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
  return truncate(label, 10);
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

const stain = computed(() => todayBands({
  done: stats.value.done_24h,
  chats: todayChats.value,
  mems: todayNewMems.value,
  appSeconds: todayDay.value?.stats?.app_seconds,
}));

const avatarState = computed<AvatarFace>(() => {
  if (approvals.value && (props.state === "idle" || props.state === "success")) return "notify";
  return props.state;
});

const STATE_LINES: Record<AgentState, string> = {
  idle: "安静待命",
  listen: "正在感知",
  think: "正在思考",
  work: "正在行动",
  say: "正在回应",
  success: "刚刚完成",
  error: "需要留意",
};
const stateText = computed(() => avatarState.value === "notify" ? "有事找你" : STATE_LINES[props.state]);

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
  emit("chat", `关于「${truncate(point.full, 40)}」：`);
}

function greet() {
  emit("chat", "查看当前状态、记忆和可用能力");
}

function launchSkill(plugin: PluginInfo) {
  emit("chat", `打开${plugin.name}面板`);
}

let memTimer: ReturnType<typeof setInterval> | null = null;
let unBrain: (() => void) | null = null;
let unApprovals: (() => void) | null = null;
onMounted(async () => {
  try { await refreshMem(); } catch { loaded.value = true; }
  try { plugins.value = await invoke<PluginInfo[]>("list_plugins").catch(() => []); } catch { plugins.value = []; }
  await refreshFeedStats();
  try {
    const days = await getDistillTimelineOnce(2);
    const key = new Date().toISOString().slice(0, 10);
    todayDay.value = days.find((row) => row.day === key) ?? days[0] ?? null;
  } catch { todayDay.value = null; }
  todayLoaded.value = true;
  memTimer = setInterval(() => { void refreshMem(); void refreshFeedStats(); }, 45000);
  try { unApprovals = onPendingConfirms((list) => { approvals.value = list.length; }); } catch { /* 无确认队列时团子保持原态 */ }
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
  unApprovals?.();
});
</script>

<template>
  <aside class="agent" :class="[state, { compact }]">
    <HomeWidget v-if="!only || only === 'identity'" id="identity" class="identity-widget" :class="{ 'is-seat': identityFace === 'seat' }">
      <button class="identity" type="button" title="和译宝聊聊它的记忆与能力" @click="greet">
        <span class="identity-avatar">
          <Avatar :state="avatarState" :size="identityFace === 'seat' ? 108 : 40" :compact="false" />
        </span>
        <span class="identity-copy">
          <span class="identity-line">
            <strong>译宝</strong>
            <i class="state-dot" :class="avatarState" />
            <em class="identity-state">{{ stateText }}</em>
          </span>
        </span>
      </button>
    </HomeWidget>

    <HomeWidget v-if="!only || only === 'mind'" id="mind" class="mind-widget" aria-label="译宝的记忆、感知和行动能力">
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
          :density="mindFace"
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

    <!-- 今日概要：加载中显示骨架；加载完无当日活动数据则整块隐藏（不让空白零件占屏） -->
    <HomeWidget v-if="!only || only === 'today'" id="today" class="today-widget" :class="{ 'is-loading': !todayLoaded }" aria-label="今日概要">
      <div v-if="todayLoaded" class="stain" aria-hidden="true">
        <i v-for="(band, index) in stain.values" :key="index" :style="{ opacity: 0.16 + band * 0.84 }" />
      </div>
      <div v-else class="stain skeleton" aria-hidden="true">
        <i v-for="n in 8" :key="n" />
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

:deep(.identity-widget) {
  display: flex;
}

.identity {
  flex: 1;
  width: 100%;
  min-width: 0;
  padding: 8px 28px 8px 10px;
  border: 0;
  background: transparent;
  display: flex;
  align-items: center;
  gap: 8px;
  color: inherit;
  text-align: left;
  cursor: pointer;
}

.identity-avatar {
  flex: none;
}

.identity-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
  font-size: 11px;
  color: var(--yb-paper-ink-dim);
  overflow: hidden;
}

.identity-copy > span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

:deep(.is-seat) {
  border: 0;
  background: transparent;
  box-shadow: none;
  overflow: visible;
}
:deep(.is-seat) .identity {
  flex-direction: column;
  align-items: center;
  padding: 0 4px 0;
  text-align: center;
  gap: 6px;
}
:deep(.is-seat) .identity-copy {
  white-space: normal;
  align-items: center;
}
:deep(.is-seat) .identity-copy > span {
  overflow: visible;
  text-overflow: clip;
  white-space: normal;
}
:deep(.is-seat) .identity-line {
  flex-wrap: wrap;
  justify-content: center;
}

.identity-line {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  max-width: 100%;
  color: var(--yb-paper-ink-dim);
}

.identity-line strong {
  flex: none;
  margin-right: 2px;
  font-size: 13px;
  font-style: normal;
  color: var(--yb-paper-ink);
}

.identity-state {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-style: normal;
  font-weight: inherit;
}

.state-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--yb-state-idle);
}

.listen .state-dot,
.state-dot.listen { background: var(--yb-state-listen); }
.think .state-dot,
.state-dot.think { background: var(--yb-state-think); }
.work .state-dot,
.state-dot.work { background: var(--yb-state-work); }
.say .state-dot,
.state-dot.say { background: var(--yb-state-say); }
.success .state-dot,
.state-dot.success { background: var(--yb-state-success); }
.error .state-dot,
.state-dot.error { background: var(--yb-state-error); }
.state-dot.notify { background: var(--yb-intent-pending); }

.mind-widget {
  flex: none;
}

.mind-well {
  margin: 6px 8px 8px;
  overflow: hidden;
  border-radius: calc(var(--yb-widget-radius) - 6px);
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--yb-widget-bg) 70%, transparent), var(--yb-note-mute)),
    var(--yb-widget-bg);
  box-shadow: var(--yb-press);
  opacity: 0.82;
  filter: saturate(0.92);
  transition: opacity 420ms var(--yb-ease-out), filter 420ms var(--yb-ease-out);
}
.think .mind-well,
.work .mind-well,
.listen .mind-well {
  opacity: 1;
  filter: saturate(1.06);
}
.mind-well :deep(.density-tile .brain-legend) {
  display: none;
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

.stain {
  display: grid;
  grid-template-columns: repeat(8, 1fr);
  gap: 3px;
  margin: 10px 8px;
  height: 28px;
  padding: 6px;
  overflow: hidden;
  border-radius: calc(var(--yb-widget-radius) - 8px);
  background: var(--yb-note-mute);
  box-shadow: var(--yb-press);
}
.stain i {
  display: block;
  height: 100%;
  border-radius: 2px 2px 0 0;
  background: var(--yb-accent-deep);
}
.stain.skeleton i {
  background: var(--yb-line);
  animation: stain-skeleton 1.4s var(--yb-ease-out) infinite;
}
.stain.skeleton i:nth-child(2n) { animation-delay: 0.22s; }
.stain.skeleton i:nth-child(3n) { animation-delay: 0.44s; }
@keyframes stain-skeleton {
  0%, 100% { opacity: 0.35; }
  50% { opacity: 0.8; }
}

@media (prefers-reduced-motion: reduce) {
  .stain.skeleton i { animation: none; opacity: 0.45; }
  .mind-well { transition: none; }
}

</style>
