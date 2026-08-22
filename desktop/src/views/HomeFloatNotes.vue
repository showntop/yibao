<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { getWidgetsOnce, type WidgetPayload } from "../lib/brain";
import { pluginGlanceLine } from "../lib/home/home-glance-faces.ts";

/** 一条飘入对话窗的便签。 */
interface FloatNote {
  id: number;
  title: string; // 插件名 · 卡片名
  line: string;  // 内容行（"开战会 21:00"）
  born: number;  // 出生时间戳
  leaving: boolean; // 正在飘走
}

const notes = ref<FloatNote[]>([]);
let seq = 0;
let timer: ReturnType<typeof setInterval> | null = null;

/** 同一面板 + 同一内容去重：内容没变不重复触发。 */
const lastSeen = new Map<string, string>();
/** 首刷静默：挂载时的第一次拉取只记录基准，不飘便签（避免打开就一屋子纸）。 */
let silentFirst = true;
function watchWidgets(widgets: WidgetPayload[]) {
  const now = Date.now();
  for (const widget of widgets) {
    const line = pluginGlanceLine(widget.schema, widget.data);
    if (!line) {
      lastSeen.delete(widget.panel);
      continue;
    }
    const key = widget.panel;
    const prev = lastSeen.get(key);
    if (prev !== line) {
      lastSeen.set(key, line);
      if (silentFirst) continue; // 首刷只记录
      pushNote(widget.title, line, now);
    }
  }
  silentFirst = false;
}

function pushNote(title: string, line: string, now: number) {
  // 同一条便签重复进入时不叠：把它重置为"刚出生"
  const existing = notes.value.find((n) => n.title === title && n.line === line && !n.leaving);
  if (existing) {
    existing.born = now;
    return;
  }
  const id = ++seq;
  notes.value.push({ id, title, line, born: now, leaving: false });
  // 最多同时 2 条，第 3 条顶掉最旧
  while (notes.value.length > 2) notes.value.shift();
  // 4.5 秒后飘走（捕获本便签 id，不引用全局 seq）
  window.setTimeout(() => {
    const note = notes.value.find((n) => n.id === id);
    if (note && !note.leaving) note.leaving = true;
    window.setTimeout(() => {
      notes.value = notes.value.filter((n) => n.id !== id);
    }, 650);
  }, 4500);
}

async function refresh() {
  try {
    const result = await getWidgetsOnce().catch(() => ({ widgets: [] as WidgetPayload[] }));
    watchWidgets(result.widgets ?? []);
  } catch { /* sidecar unavailable */ }
}

onMounted(async () => {
  await refresh();
  timer = setInterval(() => { void refresh(); }, 12000);
});

onUnmounted(() => {
  if (timer !== null) clearInterval(timer);
  lastSeen.clear();
});
</script>

<template>
  <div class="float-notes" aria-live="polite">
    <TransitionGroup name="note" tag="div" class="float-notes-stack">
      <div v-for="note in notes" :key="note.id" class="float-note" :class="{ leaving: note.leaving }">
        <span class="fn-kicker">{{ note.title }}</span>
        <span class="fn-line">{{ note.line }}</span>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.float-notes {
  position: absolute;
  z-index: 20;
  top: 12px;
  right: 16px;
  pointer-events: none;
}
.float-notes-stack {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 8px;
}
.float-note {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-width: 220px;
  padding: 8px 12px 9px 14px;
  border-radius: 2px 10px 10px 2px;
  background:
    linear-gradient(90deg, var(--yb-accent) 3px, transparent 3px),
    var(--yb-note-mute);
  box-shadow: var(--yb-press), 0 6px 18px rgba(var(--yb-c-slate-rgb), 0.14);
  will-change: transform, opacity;
  animation: float-sway 2.8s var(--yb-ease-out) infinite 0.55s;
  transform-origin: 50% 100%;
}

/* 停留期间：纸条挂在右上角，像被风吹着轻轻左右晃动 */
@keyframes float-sway {
  0%, 100% { transform: rotate(-1.2deg) translateX(0); }
  50%      { transform: rotate(1.4deg) translateX(3px); }
}
.fn-kicker {
  color: var(--yb-paper-ink-dim);
  font-size: 10px;
  letter-spacing: 0.04em;
}
.fn-line {
  color: var(--yb-text-strong);
  font-size: 12.5px;
  font-weight: var(--yb-fw-medium);
  line-height: 1.4;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 入场：从底部被风吹起，飘到右上角贴下 */
.note-enter-active {
  transition: transform 520ms var(--yb-ease-out), opacity 320ms ease;
}
.note-enter-from {
  opacity: 0;
  transform: translateY(56px) rotate(-4deg);
}
.float-note.leaving {
  animation: none; /* 停掉晃动，让 transition 的飘走生效 */
}
.note-leave-active {
  transition: transform 780ms var(--yb-ease-in), opacity 500ms ease;
}
.note-leave-to {
  opacity: 0;
  transform: translateY(-140px) rotate(5deg) scale(0.95);
}
</style>
