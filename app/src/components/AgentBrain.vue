<script setup lang="ts">
/* AgentBrain — 智能体栏：AI 的「内心」人格化可视化。
 *
 * 大团子角色居中 = AI 本体；周围记忆词云 = 大脑里流动的想法
 * （记忆条目提取关键词，缓慢漂浮 + 呼吸 + 深浅层次，像星云）。
 * 人格化细节：
 * - 状态文案多样化（性格化台词，状态切换换一句）
 * - think 态大脑光晕转紫 + 词云加速（"在思考"的感觉）
 * - 点击角色 → 说一句台词（本地气泡，不打扰对话）
 * - 点击记忆词 → 带完整记忆进对话
 * - 记忆按命名空间分类（全部 / 底座 / 插件）可切换
 * - 空记忆时星云种子 + 引导语
 */
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { invoke } from "@tauri-apps/api/core";
import Avatar from "./Avatar.vue";
import YbIcon from "./YbIcon.vue";
import { getMemListOnce, onBrainEvent, type MemItem } from "../lib/brain";

type AgentState = "idle" | "listen" | "think" | "work" | "say" | "success" | "error";
const props = defineProps<{ state: AgentState }>();
const emit = defineEmits<{ chat: [draft: string] }>();

interface PluginInfo { id: string; name: string }
interface MemWord {
  text: string;        // 词云显示文本（截断）
  full: string;        // 完整记忆文本（点击用）
  size: number;        // 字号（11-17）
  x: number;           // 相对中心偏移 px
  y: number;
  delay: number;       // 动画延迟
  dur: number;         // 动画时长
  tone: number;        // 色阶 0-3（深→浅）
  fresh: boolean;      // 新浮现的记忆（入场动画）
}
interface BurstParticle {
  id: number;
  x: number;   // 爆散起点（brain 内绝对坐标）
  y: number;
  dx: number;  // 飞散位移
  dy: number;
  delay: number;
}

const memories = ref<MemItem[]>([]);
const plugins = ref<PluginInfo[]>([]);
const loaded = ref(false);
const memFailed = ref(false);

// ---- 记忆命名空间分类 ----
const memFilter = ref<string | null>(null); // null=全部；""=底座；其余=插件 ns
const memNamespaces = computed(() => {
  const map = new Map<string, string>(); // ns → label
  for (const m of memories.value) {
    if (!map.has(m.ns)) map.set(m.ns, m.label || "译宝");
  }
  return [...map.entries()];
});
const visibleMemories = computed(() =>
  memFilter.value === null ? memories.value : memories.value.filter((m) => m.ns === memFilter.value),
);

/** 稳定伪随机：以记忆 id 为种子 → 词的位置/字号/色阶恒定，刷新不跳动。 */
function hashStr(s: string): number {
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}
function wordText(m: MemItem): string {
  const parts = m.text
    .replace(/[，。！？、；：""''（）【】\n]/g, " ")
    .split(/\s+/)
    .filter((p) => p.length > 0);
  const pick = (parts.slice(0, 2).join("·") || m.text.slice(0, 8)).trim();
  return pick.length > 12 ? pick.slice(0, 12) + "…" : pick;
}
/** 记忆 → 词云词（id 种子稳定生成，仅 fresh 标记随增量变化）。 */
const words = computed<MemWord[]>(() =>
  visibleMemories.value
    .slice(0, 22)
    .map((m) => {
      const h = hashStr(m.id);
      const text = wordText(m);
      if (!text) return null;
      return {
        text,
        full: m.text,
        size: 11 + (h % 7),
        x: Math.round((((h >> 3) % 97) / 97) * 150 - 75),
        y: Math.round((((h >> 7) % 93) / 93) * 120 - 60),
        delay: (h % 70) / 10,
        dur: 5 + (h % 50) / 10,
        tone: h % 4,
        fresh: freshIds.value.has(m.id),
      };
    })
    .filter((w): w is MemWord => w !== null),
);

const memoryCount = computed(() => visibleMemories.value.length);
const skillCount = computed(() => plugins.value.length);

// ---- 实时增词：轮询（记忆不频繁变化，45s 一次）+ 对话事件后刷新。
// 对比新旧 id：新增记忆标 fresh（入场浮现动画），已有词位置稳定不动。----
let memTimer: ReturnType<typeof setInterval> | null = null;
let unBrain: (() => void) | null = null;
const freshIds = ref<Set<string>>(new Set());
let lastMemIds = new Set<string>();
async function refreshMem() {
  const m = await getMemListOnce();
  const newIds = new Set(m.items.map((x) => x.id));
  // 首刷不标 fresh（避免全部闪一下）；后续只有真正新增的记忆浮现
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
onMounted(async () => {
  await refreshMem();
  plugins.value = await invoke<PluginInfo[]>("list_plugins").catch(() => []);
  memTimer = setInterval(() => void refreshMem(), 45000);
  // 对话有结果/动作后可能沉淀记忆 → 顺手刷新
  unBrain = await onBrainEvent((e) => {
    if (e.kind === "final_reply" || e.kind === "action_result") void refreshMem();
  });
});
onUnmounted(() => {
  if (memTimer !== null) clearInterval(memTimer);
  unBrain?.();
});

// ---- 状态人格化文案：性格化台词，状态切换换一句 ----
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
watch(
  () => props.state,
  () => { stateIdx.value += 1; },
);

// ---- 角色点击：说一句台词（本地气泡，不打扰对话）----
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

// ---- 词云点击：粒子爆散（词炸成星尘）+ 带记忆进对话 ----
const bursts = ref<BurstParticle[]>([]);
let burstSeq = 0;
function burstAt(x: number, y: number) {
  const start = burstSeq;
  for (let i = 0; i < 8; i++) {
    const ang = (Math.PI * 2 * i) / 8 + Math.random() * 0.6;
    const dist = 22 + Math.random() * 20;
    bursts.value.push({
      id: burstSeq++,
      x,
      y,
      dx: Math.cos(ang) * dist,
      dy: Math.sin(ang) * dist,
      delay: Math.random() * 0.06,
    });
  }
  setTimeout(() => {
    bursts.value = bursts.value.filter((p) => p.id >= start);
  }, 700);
}

/** 点记忆词 → 词位置粒子爆散 + 带完整记忆进对话。 */
function ask(w: MemWord, e: MouseEvent) {
  // brain 200px 容器中心 = (100, 100)；词偏移相对中心
  const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
  const brainRect = (e.currentTarget as HTMLElement).closest(".brain")!.getBoundingClientRect();
  burstAt(rect.left - brainRect.left + rect.width / 2, rect.top - brainRect.top + rect.height / 2);
  emit("chat", `关于「${w.full.length > 40 ? w.full.slice(0, 40) + "…" : w.full}」：`);
}

/** 技能点击 → 让 AI 打开它。 */
function launchSkill(p: PluginInfo) {
  emit("chat", `打开${p.name}面板`);
}

// ---- 空态星云种子（无记忆时的引导粒子）----
const SEEDS = Array.from({ length: 6 }, () => ({
  x: 40 + Math.random() * 120,
  y: 45 + Math.random() * 100,
  delay: Math.random() * 3,
  dur: 2.5 + Math.random() * 2,
}));
</script>

<template>
  <aside class="agent" :class="state">
    <!-- 大脑：角色 + 记忆词云（人格化核心） -->
    <div class="brain">
      <div class="brain-glow" />
      <!-- 空态星云种子：几颗闪烁的星，暗示大脑正在形成 -->
      <span v-if="loaded && !memoryCount && !memFailed" v-for="s in SEEDS" :key="s.x + s.y" class="seed" :style="{ left: s.x + 'px', top: s.y + 'px', animationDelay: s.delay + 's', animationDuration: s.dur + 's' }" />
      <!-- 点击粒子爆散 -->
      <span
        v-for="p in bursts"
        :key="p.id"
        class="burst"
        :style="{ left: p.x + 'px', top: p.y + 'px', '--dx': p.dx + 'px', '--dy': p.dy + 'px', animationDelay: p.delay + 's' }"
      />
      <!-- 记忆词：星云式漂浮（新浮现的记忆 fresh 入场） -->
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
          :title="w.full"
          @click="ask(w, $event)"
        >{{ w.text }}</button>
      </span>
      <!-- 角色本体（呼吸核心），点击说台词 -->
      <div class="brain-core" @click="onAgentClick">
        <Avatar :state="state" :size="76" />
        <transition name="say-line">
          <span v-if="line" class="say-line">{{ line }}</span>
        </transition>
      </div>
    </div>

    <div class="agent-name">译宝</div>
    <div class="agent-state" :class="state"><i class="ag-dot" />{{ stateText }}</div>

    <!-- 记忆分类标签（全部 / 底座 / 插件） -->
    <div v-if="memNamespaces.length > 1" class="agent-filters">
      <button class="ag-filter" :class="{ on: memFilter === null }" @click="memFilter = null">全部</button>
      <button v-for="[ns, label] in memNamespaces" :key="ns" class="ag-filter" :class="{ on: memFilter === ns }" @click="memFilter = ns">{{ label }}</button>
    </div>

    <div class="agent-meta">
      <div class="ag-item" :title="memFailed ? '记忆后端暂不可用' : '它记得你什么'">
        <YbIcon name="sparkle" :size="12" />
        <span>记忆</span>
        <b class="yb-num">{{ loaded ? memoryCount : "…" }}</b>
      </div>
      <div class="ag-item" title="它能调用什么">
        <YbIcon name="plug" :size="12" />
        <span>技能</span>
        <b class="yb-num">{{ loaded ? skillCount : "…" }}</b>
      </div>
    </div>

    <!-- 技能入口：点击让 AI 打开 -->
    <div v-if="plugins.length" class="agent-skills">
      <button v-for="p in plugins.slice(0, 4)" :key="p.id" class="ag-skill" @click="launchSkill(p)">
        {{ p.name }}
      </button>
      <span v-if="plugins.length > 4" class="ag-more">+{{ plugins.length - 4 }}</span>
    </div>

    <p v-if="loaded && !memoryCount && !memFailed" class="agent-hint">
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
  padding: 16px 12px 14px;
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
}
/* 大脑光晕：accent 星云底，缓慢呼吸；think 态转紫 + 加速（"在思考"） */
.brain-glow {
  position: absolute;
  inset: 8px;
  border-radius: 50%;
  background:
    radial-gradient(60% 60% at 50% 42%, rgba(var(--yb-c-sky-rgb), 0.16), rgba(var(--yb-c-sky-rgb), 0) 70%);
  filter: blur(2px);
  animation: glow-breathe 5s ease-in-out infinite;
  transition: background var(--yb-dur-fast) var(--yb-ease-out);
}
.agent.think .brain-glow {
  background:
    radial-gradient(60% 60% at 50% 42%, rgba(142, 124, 240, 0.20), rgba(142, 124, 240, 0) 70%);
  animation-duration: 2.4s;
}
/* 角色：中心，词浮在周围 */
.brain-core {
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  z-index: 1;
  cursor: pointer;
}
/* 台词气泡：点击角色浮现 */
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
  z-index: 2;
}
.say-line-enter-active,
.say-line-leave-active {
  transition: opacity var(--yb-dur-fast) var(--yb-ease-out), transform var(--yb-dur-fast) var(--yb-ease-out);
}
.say-line-enter-from,
.say-line-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(4px);
}
.say-line-enter-to {
  opacity: 1;
  transform: translateX(-50%) translateY(0);
}

/* ---- 记忆词云 ---- */
.mem-wrap {
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(calc(-50% + var(--wx)), calc(-50% + var(--wy)));
  animation: word-float var(--wt) ease-in-out var(--wd) infinite;
}
@keyframes word-float {
  0%, 100% {
    transform: translate(calc(-50% + var(--wx)), calc(-50% + var(--wy)));
  }
  50% {
    transform: translate(calc(-50% + var(--wx)), calc(-50% + var(--wy) - 7px));
  }
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
  opacity: 0.55;
  transition: opacity var(--yb-dur-fast) var(--yb-ease-out), transform var(--yb-dur-fast) var(--yb-ease-out);
}
/* hover：放大 + 高亮（内层缩放，不与漂浮动画冲突） */
.mem-word:hover {
  opacity: 1;
  transform: scale(1.18);
}
/* 新浮现记忆：先入场（从中心弹出 + 淡入），再无缝接漂浮 */
.mem-wrap.fresh {
  animation:
    word-in 0.55s var(--yb-ease-spring) both,
    word-float var(--wt) ease-in-out var(--wd) infinite;
  animation-delay: 0s, var(--wd);
}
@keyframes word-in {
  from {
    transform: translate(calc(-50% + var(--wx)), calc(-50% + var(--wy))) scale(0);
    opacity: 0;
  }
  to {
    transform: translate(calc(-50% + var(--wx)), calc(-50% + var(--wy))) scale(1);
    opacity: 0.55;
  }
}

/* ---- 点击粒子爆散：词炸成星尘向外飞 + 淡出 ---- */
.burst {
  position: absolute;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--yb-accent);
  pointer-events: none;
  animation: burst-fly 0.62s ease-out forwards;
}
@keyframes burst-fly {
  from { opacity: 1; transform: translate(0, 0) scale(1); }
  to { opacity: 0; transform: translate(var(--dx), var(--dy)) scale(0.25); }
}
/* 色阶：深→浅（天青系） */
.mem-word.t0 { color: var(--yb-c-sky-600); }
.mem-word.t1 { color: #5b96c4; }
.mem-word.t2 { color: #7fb0d6; }
.mem-word.t3 { color: #a5c8e2; }
/* think 态：词云加速（大脑活跃） */
.agent.think .mem-wrap {
  animation-duration: calc(var(--wt) * 0.55);
}

/* 空态星云种子 */
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
.ag-filter:hover {
  color: var(--yb-text);
}
.ag-filter.on {
  background: var(--yb-accent-soft);
  border-color: var(--yb-accent);
  color: var(--yb-accent-deep);
  font-weight: var(--yb-fw-medium);
}

/* ---- 记忆/技能计数 ---- */
.agent-meta {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}
.ag-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  border: 1px solid var(--yb-border-base);
  border-radius: var(--yb-radius-pill);
  background: var(--yb-surface-2);
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-sm);
}
.ag-item svg {
  color: var(--yb-accent);
}
.ag-item b {
  font-size: var(--yb-fs-md);
  color: var(--yb-accent-deep);
}

/* ---- 技能入口 ---- */
.agent-skills {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 4px;
  margin-top: 6px;
}
.ag-skill {
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

/* ---- 新用户引导 ---- */
.agent-hint {
  margin: 8px 4px 0;
  text-align: center;
  font-size: var(--yb-fs-sm);
  line-height: var(--yb-lh-base);
  color: var(--yb-text-faint);
}

@keyframes glow-breathe {
  0%, 100% { transform: scale(0.94); opacity: 0.75; }
  50% { transform: scale(1.04); opacity: 1; }
}
</style>
