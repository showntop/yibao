<!-- 地平线仪器条（design §5 / §10-P0）：时间刻度 + echo + 入口 + ctx，壳级贴底。
     规格：specimen/home-field.html；节点映射与 echo 文案的纯逻辑在 lib/home/horizon.ts。 -->
<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { getFeedOnce, onFeed, type FeedItem, type FeedResponse } from "../../lib/brain";
import { horizonEcho, horizonNodes } from "../../lib/home/horizon.ts";
import type { HomeAvatarState } from "../../lib/home/home-chat-session.ts";

const props = defineProps<{
  state: HomeAvatarState;
  proc: { label: string; done: boolean; ok?: boolean } | null;
}>();
const emit = defineEmits<{ entry: [id: "sessions" | "today"] }>();

const items = ref<FeedItem[]>([]);
const pulseId = ref<number | null>(null);
const now = ref(Date.now());
let unFeed: (() => void) | null = null;
let pulseTimer: ReturnType<typeof setTimeout> | null = null;
let clock: ReturnType<typeof setInterval> | null = null;
let maxSeen = 0;

onMounted(async () => {
  try {
    const feed = await getFeedOnce();
    items.value = feed.items ?? [];
    maxSeen = items.value.reduce((m, it) => Math.max(m, it.id), 0); // 基线：启动不脉冲
  } catch {
    // feed 不可得时地平线仍成立：刻度空着，echo/入口照常
  }
  unFeed = await onFeed((r: FeedResponse) => {
    const fresh = r.items ?? [];
    const top = fresh.reduce((m, it) => Math.max(m, it.id), 0);
    if (top > maxSeen) {
      maxSeen = top;
      firePulse(top); // 落账轻亮一下：动的是"信息到达"本身（design §6）
    }
    items.value = fresh;
  });
  clock = setInterval(() => (now.value = Date.now()), 30_000);
});

onUnmounted(() => {
  unFeed?.();
  if (pulseTimer) clearTimeout(pulseTimer);
  if (clock) clearInterval(clock);
});

function firePulse(id: number) {
  pulseId.value = id;
  if (pulseTimer) clearTimeout(pulseTimer);
  pulseTimer = setTimeout(() => (pulseId.value = null), 1400);
}

const nodes = computed(() => horizonNodes(items.value, now.value));
const echo = computed(() => horizonEcho({ state: props.state, proc: props.proc }));
</script>

<template>
  <footer class="horizon">
    <div class="nodes" :class="{ empty: !nodes.length }">
      <div
        v-for="n in nodes"
        :key="n.id"
        class="node"
        :class="{ hot: n.hot, pulse: n.id === pulseId }"
        :title="String(n.id)"
      >
        <i></i><span>{{ n.label }}</span>
      </div>
    </div>
    <div class="echo">
      <template v-if="echo">echo: <em :class="`tone-${echo.tone}`">{{ echo.text }}</em></template>
    </div>
    <div class="entries">
      <button class="entry" title="今日一瞥" @click="emit('entry', 'today')">今日</button>
      <button class="entry" title="会话列表" @click="emit('entry', 'sessions')">会话</button>
    </div>
    <div class="ctx">ctx: home · {{ state }}</div>
  </footer>
</template>

<style scoped>
.horizon {
  box-sizing: border-box;
  flex: none;
  height: 36px;
  display: flex;
  align-items: center;
  gap: 24px;
  padding: 0 20px;
  border-top: 1px solid var(--yb-line);
  background: var(--yb-paper-sticky);
  backdrop-filter: var(--yb-blur);
  font-family: var(--yb-mono);
  font-size: var(--yb-fs-xs);
  color: var(--yb-text-faint);
  user-select: none;
}
.nodes {
  display: flex;
  align-items: center;
  gap: 26px;
  min-width: 0;
  overflow: hidden;
}
.node {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
  flex: none;
}
.node i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--yb-c-slate-300);
}
.node.hot i {
  background: var(--yb-accent);
  box-shadow: 0 0 6px rgba(var(--yb-c-sky-rgb), 0.5);
}
.node span {
  font-size: 9px;
  letter-spacing: 0;
}
/* 落账脉冲：一次性，不循环；reduced-motion 由 tokens.css 全局规则关停 */
.node.pulse i {
  animation: horizon-pulse 1.2s ease-out 1;
}
@keyframes horizon-pulse {
  0% {
    box-shadow: 0 0 0 0 rgba(var(--yb-c-sky-rgb), 0.55);
  }
  100% {
    box-shadow: 0 0 0 8px rgba(var(--yb-c-sky-rgb), 0);
  }
}
.echo {
  color: var(--yb-text-dim);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}
.echo em {
  font-style: normal;
}
.echo .tone-ok {
  color: var(--yb-intent-ok);
}
.echo .tone-busy {
  color: var(--yb-accent);
}
.echo .tone-warn {
  color: var(--yb-c-amber-500); /* 待你定/异常 = 琥珀；红只留给不可逆外发（design §7） */
}
.entries {
  margin-left: auto;
  display: flex;
  gap: 18px;
}
.entry {
  border: 0;
  background: none;
  padding: 0;
  font: inherit;
  font-weight: var(--yb-fw-medium);
  color: var(--yb-text-dim);
  cursor: pointer;
}
.entry:hover {
  color: var(--yb-accent);
}
.ctx {
  color: var(--yb-text-faint);
  white-space: nowrap;
}
</style>
