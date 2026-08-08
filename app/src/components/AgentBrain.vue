<script setup lang="ts">
/* AgentBrain — 智能体栏：AI 的「内心」人格化可视化。
 *
 * 大团子角色居中 = AI 本体；周围一圈记忆词云 = 大脑里流动的想法
 * （记忆条目提取关键词，缓慢漂浮 + 呼吸 + 深浅层次，像星云）。
 * 点击记忆词 → 带完整记忆进对话（「关于『XXX』：」）。
 * 底部：记忆 / 技能计数（它能记得什么、能干什么）。
 */
import { computed, onMounted, onUnmounted, ref } from "vue";
import { invoke } from "@tauri-apps/api/core";
import Avatar from "./Avatar.vue";
import YbIcon from "./YbIcon.vue";
import { getMemListOnce, type MemItem } from "../lib/brain";

defineProps<{ state: "idle" | "listen" | "think" | "work" | "say" | "success" | "error" }>();
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
}

const memories = ref<MemItem[]>([]);
const plugins = ref<PluginInfo[]>([]);
const loaded = ref(false);
const memFailed = ref(false);

onMounted(async () => {
  const m = await getMemListOnce();
  memories.value = m.items;
  memFailed.value = m.failed ?? false;
  loaded.value = true;
  plugins.value = await invoke<PluginInfo[]>("list_plugins").catch(() => []);
});
onUnmounted(() => {});

/** 记忆 → 词云词：文本拆词（标点/空白切分），取前 2 段拼合；随机位置/尺寸/节奏。 */
const words = computed<MemWord[]>(() => {
  const out: MemWord[] = [];
  for (const m of memories.value.slice(0, 20)) {
    const parts = m.text
      .replace(/[，。！？、；：""''（）【】\n]/g, " ")
      .split(/\s+/)
      .filter((p) => p.length > 0);
    const pick = (parts.slice(0, 2).join("·") || m.text.slice(0, 8)).trim();
    const text = pick.length > 12 ? pick.slice(0, 12) + "…" : pick;
    if (!text) continue;
    out.push({
      text,
      full: m.text,
      size: 11 + Math.round(Math.random() * 6),
      x: Math.round((Math.random() - 0.5) * 150),
      y: Math.round((Math.random() - 0.5) * 120),
      delay: Math.random() * 7,
      dur: 5 + Math.random() * 5,
      tone: Math.floor(Math.random() * 4),
    });
  }
  return out;
});

const memoryCount = computed(() => memories.value.length);
const skillCount = computed(() => plugins.value.length);

/** 点记忆词 → 带完整记忆进对话。 */
function ask(w: MemWord) {
  emit("chat", `关于「${w.full.length > 40 ? w.full.slice(0, 40) + "…" : w.full}」：`);
}
</script>

<template>
  <aside class="agent">
    <!-- 大脑：角色 + 记忆词云（人格化核心） -->
    <div class="brain">
      <div class="brain-glow" />
      <!-- 记忆词：星云式漂浮 -->
      <button
        v-for="(w, i) in words"
        :key="i"
        class="mem-word"
        :class="`t${w.tone}`"
        :title="w.full"
        :style="{
          fontSize: w.size + 'px',
          '--wx': w.x + 'px',
          '--wy': w.y + 'px',
          '--wd': w.delay + 's',
          '--wt': w.dur + 's',
        }"
        @click="ask(w)"
      >{{ w.text }}</button>
      <!-- 角色本体（呼吸核心） -->
      <div class="brain-core">
        <Avatar :state="state" :size="76" />
      </div>
    </div>

    <div class="agent-name">译宝</div>
    <div class="agent-state" :class="state"><i class="ag-dot" />{{ state === 'idle' ? '待命中' : state === 'think' ? '思考中' : state === 'work' ? '操作中' : state === 'listen' ? '聆听中' : state === 'say' ? '说话中' : '出错了' }}</div>

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
/* 大脑光晕：accent 星云底，缓慢呼吸 */
.brain-glow {
  position: absolute;
  inset: 8px;
  border-radius: 50%;
  background:
    radial-gradient(60% 60% at 50% 42%, rgba(var(--yb-c-sky-rgb), 0.16), rgba(var(--yb-c-sky-rgb), 0) 70%);
  filter: blur(2px);
  animation: glow-breathe 5s ease-in-out infinite;
}
/* 角色：中心，在词云之下（词浮在角色周围） */
.brain-core {
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  z-index: 1;
  pointer-events: none;
}

/* ---- 记忆词云 ---- */
.mem-word {
  position: absolute;
  left: 50%;
  top: 50%;
  border: none;
  background: transparent;
  padding: 0;
  white-space: nowrap;
  font-family: var(--yb-font);
  font-weight: var(--yb-fw-medium);
  line-height: 1;
  cursor: pointer;
  transform: translate(calc(-50% + var(--wx)), calc(-50% + var(--wy)));
  animation: word-float var(--wt) ease-in-out var(--wd) infinite;
  transition: color var(--yb-dur-fast) var(--yb-ease-out), opacity var(--yb-dur-fast) var(--yb-ease-out);
}
@keyframes word-float {
  0%, 100% {
    transform: translate(calc(-50% + var(--wx)), calc(-50% + var(--wy)));
    opacity: 0.45;
  }
  50% {
    transform: translate(calc(-50% + var(--wx)), calc(-50% + var(--wy) - 7px));
    opacity: 0.9;
  }
}
/* 色阶：深→浅（天青系），越深的词越「重要」 */
.mem-word.t0 { color: var(--yb-c-sky-600); }
.mem-word.t1 { color: #5b96c4; }
.mem-word.t2 { color: #7fb0d6; }
.mem-word.t3 { color: #a5c8e2; }
.mem-word:hover {
  color: var(--yb-accent);
  opacity: 1;
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
