<script setup lang="ts">
/* AgentBrain — 智能体栏：AI 的「内心」人格化（玻璃大脑 + 词云内嵌 + 粒子技能）。
 *
 * 视觉参考：半透明大脑球（毛玻璃穹顶）= AI 的"思维空间"；
 * 记忆词漂浮在球内（像 hologram）；周围粒子技能球（带虚线轨道）= AI 的能力；
 * 角色本体在球中央，与左下内容形成"上脑下心"的层次。
 *
 * 人格化细节：
 * - 状态文案多样化 / think 态大脑转紫 + 词云加速
 * - 角色点击台词 / 状态灯跟随 / 记忆 ns 分类
 * - 实时增词（轮询+对话事件，hash 稳定位置，fresh 入场浮现）
 * - 词 hover 浮层（完整记忆）/ 点击粒子爆散
 * - 下半部：今日和你的对话（数字面板）+ 性格签名（人格化座右铭）
 */
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { invoke } from "@tauri-apps/api/core";
import Avatar from "./Avatar.vue";
import YbIcon from "./YbIcon.vue";
import { getMemListOnce, onBrainEvent, type MemItem, type FeedStats } from "../lib/brain";

type AgentState = "idle" | "listen" | "think" | "work" | "say" | "success" | "error";
const props = defineProps<{ state: AgentState }>();
const emit = defineEmits<{ chat: [draft: string] }>();

interface PluginInfo { id: string; name: string }
interface MemWord {
  text: string; full: string; size: number;
  x: number; y: number; delay: number; dur: number; tone: number; fresh: boolean;
}
interface BurstParticle { id: number; x: number; y: number; dx: number; dy: number; delay: number; }

const memories = ref<MemItem[]>([]);
const plugins = ref<PluginInfo[]>([]);
const loaded = ref(false);
const memFailed = ref(false);
const stats = ref<FeedStats>({ pending_reminders: 0, running_tasks: 0, done_24h: 0, unread: 0, ignored: 0 });
const todayChats = ref(0);

const memFilter = ref<string | null>(null);
const memNamespaces = computed(() => {
  const map = new Map<string, string>();
  for (const m of memories.value) if (!map.has(m.ns)) map.set(m.ns, m.label || "译宝");
  return [...map.entries()];
});
const visibleMemories = computed(() =>
  memFilter.value === null ? memories.value : memories.value.filter((m) => m.ns === memFilter.value),
);

function hashStr(s: string): number {
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}
function wordText(m: MemItem): string {
  const parts = m.text.replace(/[，。！？、；：""''（）【】\n]/g, " ").split(/\s+/).filter((p) => p.length > 0);
  const pick = (parts.slice(0, 2).join("·") || m.text.slice(0, 8)).trim();
  return pick.length > 12 ? pick.slice(0, 12) + "…" : pick;
}
const words = computed<MemWord[]>(() =>
  visibleMemories.value.slice(0, 24).map((m) => {
    const h = hashStr(m.id);
    const text = wordText(m);
    if (!text) return null;
    return {
      text, full: m.text,
      size: 10 + (h % 6),
      x: Math.round((((h >> 3) % 97) / 97) * 150 - 75),
      y: Math.round((((h >> 7) % 75) / 75) * 66 - 48), // 穹顶内（上 2/3，角色下方不落词）
      delay: (h % 70) / 10,
      dur: 5 + (h % 50) / 10,
      tone: h % 4,
      fresh: freshIds.value.has(m.id),
    };
  }).filter((w): w is MemWord => w !== null),
);

// ---- 实时增词 ----
const freshIds = ref<Set<string>>(new Set());
let lastMemIds = new Set<string>();
async function refreshMem() {
  const m = await getMemListOnce();
  const newIds = new Set(m.items.map((x) => x.id));
  if (lastMemIds.size > 0) {
    const added = new Set([...newIds].filter((id) => !lastMemIds.has(id)));
    if (added.size) {
      freshIds.value = added;
      setTimeout(() => { freshIds.value = new Set(); }, 1800);
    }
  }
  lastMemIds = newIds;
  memories.value = m.items;
  memFailed.value = m.failed;
  loaded.value = true;
}

// ---- 今日数字（统计对话/记忆/完成）----
async function refreshFeedStats() {
  try {
    const s = await invoke<FeedStats>("get_feed_stats", { days: 1 });
    stats.value = s;
  } catch { /* 大脑不在线/超时忽略 */ }
  try {
    const r = await invoke<{ items: { kind: string; ts: number }[]; stats: FeedStats }>("get_feed", { limit: 200 });
    const startOfToday = Math.floor(new Date().setHours(0, 0, 0, 0) / 1000);
    todayChats.value = r.items.filter((it) => it.ts >= startOfToday).length;
  } catch { /* 忽略 */ }
}
const todayNewMems = computed(() => {
  const startOfToday = Math.floor(new Date().setHours(0, 0, 0, 0) / 1000);
  return memories.value.filter((m) => m.created_at && Math.floor(new Date(m.created_at).getTime() / 1000) >= startOfToday).length;
});

let memTimer: ReturnType<typeof setInterval> | null = null;
let unBrain: (() => void) | null = null;
onMounted(async () => {
  await refreshMem();
  plugins.value = await invoke<PluginInfo[]>("list_plugins").catch(() => []);
  await refreshFeedStats();
  memTimer = setInterval(() => { void refreshMem(); void refreshFeedStats(); }, 45000);
  unBrain = await onBrainEvent((e) => {
    if (e.kind === "final_reply" || e.kind === "action_result") {
      void refreshMem();
      void refreshFeedStats();
    }
  });
});
onUnmounted(() => {
  if (memTimer !== null) clearInterval(memTimer);
  unBrain?.();
});

// ---- 状态人格化台词 ----
const stateIdx = ref(0);
const STATE_LINES: Record<string, string[]> = {
  idle: ["待命中", "在呢～", "随时听候"],
  listen: ["聆听中…", "在听呢"],
  think: ["思考中…", "让我想想…"],
  work: ["操作中…", "正在办"],
  say: ["说话中…", "在说呢"],
  success: ["搞定！", "完成！"],
  error: ["出错了…", "哎哟"],
};
const stateText = computed(() => {
  const lines = STATE_LINES[props.state] ?? STATE_LINES.idle;
  return lines[stateIdx.value % lines.length];
});
watch(() => props.state, () => { stateIdx.value += 1; });

// ---- 角色点击：说一句台词（本地气泡）----
const GREETINGS = [
  "在呢～叫我做什么都行",
  "今天想干点啥？",
  "我看到你最近在忙…要不要一起整理下？",
  "记住的东西都在这啦，点一下就能聊",
];
const line = ref("");
let lineTimer: ReturnType<typeof setTimeout> | null = null;
function onAgentClick() {
  line.value = GREETINGS[Math.floor(Math.random() * GREETINGS.length)];
  if (lineTimer !== null) clearTimeout(lineTimer);
  lineTimer = setTimeout(() => { line.value = ""; }, 3200);
}
onUnmounted(() => { if (lineTimer !== null) clearTimeout(lineTimer); });

// ---- 性格签名（人格化座右铭）----
const SIGNATURES = [
  "记住你的偏好，让对话越来越懂你。",
  "不只是回答，是陪你一起想。",
  "在呢～随时叫我。",
  "我们一起让事情变简单。",
];
const signature = SIGNATURES[Math.floor(Math.random() * SIGNATURES.length)];

// ---- 词云点击：粒子爆散 + 带记忆进对话 ----
const bursts = ref<BurstParticle[]>([]);
let burstSeq = 0;
function burstAt(x: number, y: number) {
  const start = burstSeq;
  for (let i = 0; i < 8; i++) {
    const ang = (Math.PI * 2 * i) / 8 + Math.random() * 0.6;
    const dist = 22 + Math.random() * 20;
    bursts.value.push({ id: burstSeq++, x, y, dx: Math.cos(ang) * dist, dy: Math.sin(ang) * dist, delay: Math.random() * 0.06 });
  }
  setTimeout(() => { bursts.value = bursts.value.filter((p) => p.id >= start); }, 700);
}
function ask(w: MemWord, e: MouseEvent) {
  const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
  const brainRect = (e.currentTarget as HTMLElement).closest(".brain")!.getBoundingClientRect();
  burstAt(rect.left - brainRect.left + rect.width / 2, rect.top - brainRect.top + rect.height / 2);
  emit("chat", `关于「${w.full.length > 40 ? w.full.slice(0, 40) + "…" : w.full}」：`);
}

// ---- hover 浮层 ----
const hoverTip = ref<{ text: string; x: number; y: number; below: boolean } | null>(null);
let tipTimer: ReturnType<typeof setTimeout> | null = null;
function onWordEnter(w: MemWord, e: MouseEvent) {
  if (tipTimer !== null) clearTimeout(tipTimer);
  const el = e.currentTarget as HTMLElement;
  const rect = el.getBoundingClientRect();
  const brainRect = el.closest(".brain")!.getBoundingClientRect();
  hoverTip.value = {
    text: w.full,
    x: Math.min(150, Math.max(50, rect.left - brainRect.left + rect.width / 2)),
    y: Math.min(150, Math.max(50, rect.top - brainRect.top + rect.height / 2)),
    below: rect.top - brainRect.top + rect.height / 2 < 88,
  };
}
function onWordLeave() {
  if (tipTimer !== null) clearTimeout(tipTimer);
  tipTimer = setTimeout(() => { hoverTip.value = null; }, 130);
}

/** 技能点击 → 让 AI 打开它（技能 = AI 的"手"，展示在下方，不放脑部）。 */
function launchSkill(p: PluginInfo) {
  emit("chat", `打开${p.name}面板`);
}

// ---- 空态星云种子 ----
const SEEDS = Array.from({ length: 6 }, () => ({
  x: 40 + Math.random() * 120,
  y: 45 + Math.random() * 100,
  delay: Math.random() * 3,
  dur: 2.5 + Math.random() * 2,
}));
</script>

<template>
  <aside class="agent" :class="state">
    <!-- 大脑：玻璃穹顶 + 词云内嵌 + 周围粒子技能球 + 角色居中 -->
    <div class="brain">
      <div class="brain-glow" />
      <!-- 玻璃大脑球：词云在这里漂浮（毛玻璃穹顶，参考图核心） -->
      <div class="brain-dome">
        <!-- 点击粒子爆散 -->
        <span
          v-for="p in bursts"
          :key="p.id"
          class="burst"
          :style="{ left: p.x + 'px', top: p.y + 'px', '--dx': p.dx + 'px', '--dy': p.dy + 'px', animationDelay: p.delay + 's' }"
        />
        <!-- 词云（内嵌于 dome，dome overflow hidden 裁切到圆内） -->
        <span
          v-for="(w, i) in words"
          :key="i"
          class="mem-wrap"
          :class="{ fresh: w.fresh }"
          :style="{ '--wx': w.x + 'px', '--wy': w.y + 'px', '--wd': w.delay + 's', '--wt': w.dur + 's' }"
        >
          <button
            class="mem-word"
            :class="`t${w.tone}`"
            :style="{ fontSize: w.size + 'px' }"
            @mouseenter="onWordEnter(w, $event)"
            @mouseleave="onWordLeave"
            @click="ask(w, $event)"
          >{{ w.text }}</button>
        </span>
        <!-- 空态星云种子 -->
        <span
          v-if="loaded && !visibleMemories.length && !memFailed"
          v-for="(s, i) in SEEDS"
          :key="i"
          class="seed"
          :style="{ left: s.x + 'px', top: s.y + 'px', animationDelay: s.delay + 's', animationDuration: s.dur + 's' }"
        />
      </div>
      <!-- 角色本体（从穹顶底部长出，z 最高，台词气泡在下方） -->
      <div class="brain-core" @click="onAgentClick">
        <Avatar :state="state" :size="68" />
        <transition name="say-line">
          <span v-if="line" class="say-line">{{ line }}</span>
        </transition>
      </div>
    </div>

    <div class="agent-name">译宝</div>
    <div class="agent-state" :class="state"><i class="ag-dot" />{{ stateText }}</div>

    <!-- 记忆分类标签 -->
    <div v-if="memNamespaces.length > 1" class="agent-filters">
      <button class="ag-filter" :class="{ on: memFilter === null }" @click="memFilter = null">全部</button>
      <button v-for="[ns, label] in memNamespaces" :key="ns" class="ag-filter" :class="{ on: memFilter === ns }" @click="memFilter = ns">{{ label }}</button>
    </div>

    <!-- 记忆/技能计数 -->
    <div class="agent-meta">
      <div class="ag-item" :title="memFailed ? '记忆后端暂不可用' : '它记得你什么'">
        <YbIcon name="sparkle" :size="12" />
        <span>记忆</span>
        <b class="yb-num">{{ loaded ? visibleMemories.length : "…" }}</b>
      </div>
      <div class="ag-item" title="它能调用什么">
        <YbIcon name="plug" :size="12" />
        <span>技能</span>
        <b class="yb-num">{{ loaded ? plugins.length : "…" }}</b>
      </div>
    </div>

    <!-- 技能（AI 的"手"）：入口 chips 展示在下方，不放脑部 -->
    <div v-if="plugins.length" class="agent-skills">
      <button v-for="p in plugins.slice(0, 5)" :key="p.id" class="ag-skill" @click="launchSkill(p)">
        {{ p.name }}
      </button>
      <span v-if="plugins.length > 5" class="ag-more">+{{ plugins.length - 5 }}</span>
    </div>

    <!-- 今日和你的对话：人格化数字面板 -->
    <section class="agent-today" v-if="loaded">
      <div class="at-title">今天和你的对话</div>
      <div class="at-grid">
        <div class="at-cell" title="今日完成的任务">
          <b class="yb-num">{{ stats.done_24h }}</b>
          <span>完成</span>
        </div>
        <div class="at-cell" title="今日对话条数">
          <b class="yb-num">{{ todayChats }}</b>
          <span>对话</span>
        </div>
        <div class="at-cell" title="今日新增记忆">
          <b class="yb-num">{{ todayNewMems }}</b>
          <span>新记忆</span>
        </div>
      </div>
    </section>

    <!-- 性格签名（人格化座右铭）-->
    <p class="agent-sig">「{{ signature }}」</p>

    <p v-if="loaded && !visibleMemories.length && !memFailed" class="agent-hint">
      大脑还在形成——跟译宝聊聊你的偏好和习惯，记忆会像星云一样浮现。
    </p>
  </aside>
</template>

<style scoped>
.agent {
  width: 240px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 14px 12px 12px;
  border-right: 1px solid var(--yb-border-base);
  background:
    radial-gradient(80% 60% at 50% 0%, rgba(var(--yb-c-sky-rgb), 0.05), transparent 70%),
    var(--yb-content-bg);
  overflow: hidden;
  user-select: none;
}

/* ---- 大脑容器 ---- */
.brain {
  position: relative;
  width: 200px;
  height: 200px;
  flex-shrink: 0;
  margin: 4px 0 2px;
}
/* 大脑光晕底（柔光，跟随穹顶椭圆） */
.brain-glow {
  position: absolute;
  left: 0;
  top: 0;
  width: 200px;
  height: 152px;
  border-radius: 100px 100px 64px 64px;
  background:
    radial-gradient(58% 52% at 50% 38%, rgba(var(--yb-c-sky-rgb), 0.16), rgba(var(--yb-c-sky-rgb), 0) 70%);
  filter: blur(2px);
  animation: glow-breathe 5s ease-in-out infinite;
  transition: background var(--yb-dur-fast) var(--yb-ease-out);
}
.agent.think .brain-glow {
  background: radial-gradient(58% 52% at 50% 38%, rgba(142, 124, 240, 0.22), rgba(142, 124, 240, 0) 70%);
  animation-duration: 2.4s;
}

/* ---- 玻璃大脑穹顶：椭圆（上圆下收，像蛋壳倒扣）+ 顶部弧线高光。
 * 词云内嵌于上 2/3（参考图核心），角色从穹顶底部"长出来"，不再正圆正中心。 ---- */
.brain-dome {
  position: absolute;
  left: 0;
  top: 0;
  width: 200px;
  height: 152px;
  border-radius: 100px 100px 64px 64px;
  background: rgba(255, 255, 255, 0.42);
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
  border: 1px solid rgba(255, 255, 255, 0.6);
  box-shadow:
    inset 0 0 22px rgba(255, 255, 255, 0.45),
    0 2px 12px rgba(var(--yb-c-sky-rgb), 0.10);
  overflow: hidden;
}
/* 玻璃高光：顶部一道弧线白光（参考图的玻璃感） */
.brain-dome::before {
  content: "";
  position: absolute;
  left: 16%;
  top: 3%;
  width: 68%;
  height: 42%;
  border-radius: 50%;
  background: radial-gradient(58% 46% at 50% 32%, rgba(255, 255, 255, 0.9), rgba(255, 255, 255, 0) 72%);
  pointer-events: none;
}
.agent.think .brain-dome {
  background: rgba(255, 255, 255, 0.5);
  box-shadow:
    inset 0 0 22px rgba(255, 255, 255, 0.5),
    0 2px 14px rgba(142, 124, 240, 0.18);
}

/* ---- 角色：从穹顶底部"长出来"——上半被穹顶遮（融合），脸在下半露出 ---- */
.brain-core {
  position: absolute;
  left: 50%;
  top: 116px;
  transform: translateX(-50%);
  z-index: 2;
  cursor: pointer;
  filter: drop-shadow(0 2px 6px rgba(var(--yb-c-slate-rgb), 0.14));
}
.say-line {
  position: absolute;
  left: 50%;
  bottom: -22px;
  transform: translateX(-50%);
  width: max-content;
  max-width: 180px;
  padding: 4px 10px;
  border-radius: var(--yb-radius-sm);
  background: var(--yb-card-bg);
  border: 1px solid var(--yb-surface-border);
  box-shadow: var(--yb-shadow-2);
  color: var(--yb-text);
  font-size: var(--yb-fs-sm);
  line-height: 1.4;
  white-space: nowrap;
  text-align: center;
  z-index: 5;
}
.say-line-enter-active,
.say-line-leave-active {
  transition: opacity var(--yb-dur-fast) var(--yb-ease-out), transform var(--yb-dur-fast) var(--yb-ease-out);
}
.say-line-enter-from,
.say-line-leave-to { opacity: 0; transform: translateX(-50%) translateY(4px); }
.say-line-enter-to { opacity: 1; transform: translateX(-50%) translateY(0); }

/* ---- 词云 ---- */
.mem-wrap {
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(calc(-50% + var(--wx)), calc(-50% + var(--wy)));
  animation: word-float var(--wt) ease-in-out var(--wd) infinite;
  z-index: 1;
}
@keyframes word-float {
  0%, 100% { transform: translate(calc(-50% + var(--wx)), calc(-50% + var(--wy))); }
  50% { transform: translate(calc(-50% + var(--wx)), calc(-50% + var(--wy) - 6px)); }
}
.mem-word {
  display: block;
  border: none;
  background: transparent;
  padding: 0;
  white-space: nowrap;
  font-family: var(--yb-font);
  font-weight: var(--yb-fw-medium);
  line-height: 1;
  cursor: pointer;
  opacity: 0.6;
  transition: opacity var(--yb-dur-fast) var(--yb-ease-out), transform var(--yb-dur-fast) var(--yb-ease-out);
}
.mem-word:hover { opacity: 1; transform: scale(1.2); }
.mem-word.t0 { color: var(--yb-c-sky-600); }
.mem-word.t1 { color: #5b96c4; }
.mem-word.t2 { color: #7fb0d6; }
.mem-word.t3 { color: #a5c8e2; }
.agent.think .mem-wrap { animation-duration: calc(var(--wt) * 0.55); }
.mem-wrap.fresh {
  animation:
    word-in 0.55s var(--yb-ease-spring) both,
    word-float var(--wt) ease-in-out var(--wd) infinite;
  animation-delay: 0s, var(--wd);
}
@keyframes word-in {
  from { transform: translate(calc(-50% + var(--wx)), calc(-50% + var(--wy))) scale(0); opacity: 0; }
  to { transform: translate(calc(-50% + var(--wx)), calc(-50% + var(--wy))) scale(1); opacity: 0.6; }
}
.burst {
  position: absolute;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--yb-accent);
  pointer-events: none;
  animation: burst-fly 0.62s ease-out forwards;
  z-index: 4;
}
@keyframes burst-fly {
  from { opacity: 1; transform: translate(0, 0) scale(1); }
  to { opacity: 0; transform: translate(var(--dx), var(--dy)) scale(0.25); }
}
.seed {
  position: absolute;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--yb-accent);
  opacity: 0;
  animation: seed-twinkle ease-in-out infinite;
}
@keyframes seed-twinkle {
  0%, 100% { opacity: 0; transform: scale(0.6); }
  50% { opacity: 0.7; transform: scale(1.2); }
}

/* ---- 技能入口（AI 的"手"）：chips 展示在下方 ---- */
.agent-skills {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 4px;
  margin-top: 4px;
}
.ag-skill {
  display: inline-flex;
  align-items: center;
  padding: 2px 9px;
  border: 1px dashed var(--yb-card-border);
  border-radius: var(--yb-radius-sm);
  background: var(--yb-surface-2);
  color: var(--yb-accent-deep);
  font-size: var(--yb-fs-xs);
  font-family: inherit;
  cursor: pointer;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.ag-skill:hover {
  border-color: var(--yb-accent);
  border-style: solid;
  background: var(--yb-accent-soft);
}
.ag-more {
  display: inline-flex;
  align-items: center;
  font-size: var(--yb-fs-xs);
  color: var(--yb-text-faint);
}

/* ---- 名字与状态 ---- */
.agent-name {
  font-size: var(--yb-fs-xl);
  font-weight: var(--yb-fw-bold);
  letter-spacing: -0.01em;
  color: var(--yb-text-strong);
}
.agent-state {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-dim);
}
.ag-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--dot, var(--yb-state-idle));
}
.agent-state.idle { --dot: var(--yb-state-idle); }
.agent-state.listen { --dot: var(--yb-state-listen); }
.agent-state.think { --dot: var(--yb-state-think); }
.agent-state.work { --dot: var(--yb-state-work); }
.agent-state.say { --dot: var(--yb-state-say); }
.agent-state.success { --dot: var(--yb-state-success); }
.agent-state.error { --dot: var(--yb-state-error); }

/* ---- 记忆分类标签 ---- */
.agent-filters {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 4px;
  margin-top: 2px;
}
.ag-filter {
  padding: 2px 8px;
  border: 1px solid var(--yb-border-base);
  border-radius: var(--yb-radius-pill);
  background: transparent;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-xs);
  font-family: inherit;
  cursor: pointer;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.ag-filter:hover { color: var(--yb-text); }
.ag-filter.on {
  background: var(--yb-accent-soft);
  border-color: var(--yb-accent);
  color: var(--yb-accent-deep);
  font-weight: var(--yb-fw-medium);
}

/* ---- 记忆/技能计数 ---- */
.agent-meta {
  display: flex;
  gap: 6px;
  margin-top: 6px;
}
.ag-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 9px;
  border: 1px solid var(--yb-border-base);
  border-radius: var(--yb-radius-pill);
  background: var(--yb-surface-2);
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-xs);
}
.ag-item svg { color: var(--yb-accent); }
.ag-item b { font-size: var(--yb-fs-sm); color: var(--yb-accent-deep); }

/* ---- 今日和你的对话（人格化数字面板）---- */
.agent-today {
  width: 100%;
  margin-top: 6px;
  padding: 8px 10px;
  border: 1px solid var(--yb-card-border);
  border-radius: var(--yb-radius-md);
  background: var(--yb-card-bg);
  box-shadow: var(--yb-shadow-1);
}
.at-title {
  font-size: var(--yb-fs-xs);
  font-weight: var(--yb-fw-bold);
  color: var(--yb-text-faint);
  letter-spacing: 0.04em;
  margin-bottom: 4px;
}
.at-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 4px;
}
.at-cell {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1px;
  padding: 4px 2px;
  border-radius: var(--yb-radius-xs);
  background: var(--yb-surface-2);
}
.at-cell b {
  font-size: 16px;
  font-weight: var(--yb-fw-bold);
  line-height: 1;
  color: var(--yb-accent-deep);
}
.at-cell span {
  font-size: var(--yb-fs-xs);
  color: var(--yb-text-dim);
}

/* ---- 性格签名 ---- */
.agent-sig {
  margin: 6px 2px 0;
  text-align: center;
  font-size: var(--yb-fs-xs);
  font-style: italic;
  line-height: 1.5;
  color: var(--yb-text-dim);
}

/* ---- 新用户引导 ---- */
.agent-hint {
  margin: 4px 4px 0;
  text-align: center;
  font-size: var(--yb-fs-xs);
  line-height: 1.45;
  color: var(--yb-text-faint);
}

@keyframes glow-breathe {
  0%, 100% { transform: scale(0.94); opacity: 0.75; }
  50% { transform: scale(1.04); opacity: 1; }
}
</style>
