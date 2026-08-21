<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import HomeWidget from "./HomeWidget.vue";
import {
  getMemListOnce,
  getPerceptionOnce,
  getWidgetsOnce,
  onWidgets,
  type MemItem,
  type PerceptionItem,
  type WidgetPayload,
} from "../lib/brain";
import {
  catchFace,
  glimpseFace,
  pickSpark,
  pluginGlanceLine,
  readScratch,
  readSparkDismiss,
  writeScratch,
  writeSparkDismiss,
} from "../lib/home-glance-faces";

defineProps<{ only?: "spark" | "glimpse" | "catch" | "scratch" }>();
const emit = defineEmits<{ chat: [draft: string] }>();

const memories = ref<MemItem[]>([]);
const perception = ref<PerceptionItem[]>([]);
const widgets = ref<WidgetPayload[]>([]);
const scratch = ref("");
const dismissed = ref<string | null>(null);
let timer: ReturnType<typeof setInterval> | null = null;
let unWidgets: (() => void) | null = null;

const day = computed(() => new Date().toISOString().slice(0, 10));
const noteLine = computed(() => {
  const notes = widgets.value.find((widget) => widget.panel.startsWith("notes:"));
  return notes ? pluginGlanceLine(notes.schema, notes.data).split(" · ")[0] ?? "" : "";
});
const focus = computed(() => {
  const last = perception.value.find((item) => item.source === "app");
  return [last?.payload.app, last?.payload.title].filter(Boolean).join(" ");
});
const spark = computed(() => pickSpark(memories.value, focus.value, dismissed.value));
const glimpse = computed(() => glimpseFace(perception.value));
const caught = computed(() => catchFace(perception.value, noteLine.value));

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] ?? c);
}

/** 把记忆文本里与当前焦点（应用/标题）相关的词用 <mark> 高亮，其余原样转义。 */
const sparkHtml = computed(() => {
  const t = spark.value?.text ?? "";
  const word = focus.value
    .split(/\s+/)
    .find((w) => w.length >= 2 && t.includes(w));
  if (!word) return escapeHtml(t);
  const i = t.indexOf(word);
  return `${escapeHtml(t.slice(0, i))}<mark class="hl">${escapeHtml(t.slice(i, i + word.length))}</mark>${escapeHtml(t.slice(i + word.length))}`;
});

function dismissSpark() {
  if (!spark.value) return;
  dismissed.value = spark.value.id;
  writeSparkDismiss(spark.value.id, day.value, window.localStorage);
}

function talkSpark() {
  if (!spark.value) return;
  emit("chat", `关于「${spark.value.text}」：`);
}

function talkCatch() {
  if (!caught.value) return;
  emit("chat", caught.value.kind === "note" ? `看看闪念「${caught.value.text}」` : `这段刚复制的：「${caught.value.text}」`);
}

function talkGlimpse() {
  if (!glimpse.value) return;
  emit("chat", `我在 ${glimpse.value.app}${glimpse.value.title ? ` · ${glimpse.value.title}` : ""}`);
}

function persistScratch() {
  writeScratch(scratch.value, window.localStorage);
}

async function refresh() {
  const [mem, seen, cards] = await Promise.all([
    getMemListOnce().catch(() => ({ items: [] as MemItem[] })),
    getPerceptionOnce(20).catch(() => ({ items: [] as PerceptionItem[] })),
    getWidgetsOnce().catch(() => ({ widgets: [] as WidgetPayload[] })),
  ]);
  memories.value = mem.items ?? [];
  perception.value = seen.items ?? [];
  widgets.value = cards.widgets ?? [];
}

onMounted(async () => {
  scratch.value = readScratch(window.localStorage);
  dismissed.value = readSparkDismiss(window.localStorage, day.value);
  await refresh();
  timer = setInterval(() => { void refresh(); }, 12000);
  try {
    unWidgets = await onWidgets((payload) => { widgets.value = payload?.widgets ?? []; });
  } catch { /* 插件不在线时接到只看剪贴板 */ }
});

onUnmounted(() => {
  if (timer) clearInterval(timer);
  unWidgets?.();
});

watch(scratch, persistScratch);
</script>

<template>
  <aside class="life">
    <HomeWidget
      v-if="(!only || only === 'spark') && (glimpse || spark)"
      id="spark"
      class="is-fog"
      aria-label="余光 · 刚想起的"
    >
      <article class="combo">
        <button v-if="glimpse" class="fog" type="button" @click="talkGlimpse">
          <span class="fog-head">
            <span class="fog-dots" aria-hidden="true"><i></i><i></i><i></i></span>
            <span class="kicker">余光</span>
          </span>
          <span class="fog-app">{{ glimpse.app }}</span>
          <span v-if="glimpse.title" class="fog-title">{{ glimpse.title }}</span>
        </button>
        <section v-if="spark" class="spark-body">
          <p class="spark-text" v-html="sparkHtml"></p>
          <div class="note-acts">
            <button type="button" @click="talkSpark">接着说</button>
            <button type="button" class="quiet" @click="dismissSpark">先放下</button>
          </div>
        </section>
      </article>
    </HomeWidget>

    <HomeWidget v-if="(!only || only === 'catch') && caught" id="catch" aria-label="刚接到的东西">
      <button class="catch" type="button" @click="talkCatch">
        <span class="kicker">{{ caught.kind === "note" ? "闪念" : "刚复制" }}</span>
        <span>{{ caught.text }}</span>
      </button>
    </HomeWidget>

    <HomeWidget v-if="!only || only === 'scratch'" id="scratch" class="is-scratch" aria-label="草稿纸">
      <textarea
        v-model="scratch"
        class="pad"
        rows="4"
        placeholder="先扔一句…"
        spellcheck="false"
      />
    </HomeWidget>
  </aside>
</template>

<style scoped>
.life { display: contents; }

.combo {
  display: flex;
  flex-direction: column;
  width: calc(100% - 16px);
  margin: 8px;
  overflow: hidden;
  border-radius: calc(var(--yb-widget-radius) - 6px);
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--yb-note-mute) 88%, #fff) 0%, var(--yb-note-mute) 70%, color-mix(in srgb, var(--yb-note-mute) 60%, #f0e5c4) 100%),
    var(--yb-widget-glaze);
  box-shadow: var(--yb-press), 0 1px 0 color-mix(in srgb, var(--yb-paper-ink) 6%, transparent) inset;
  position: relative;
  isolation: isolate;
}
.combo::before {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: inherit;
  pointer-events: none;
  z-index: 1;
  background: linear-gradient(112deg, transparent 18%, color-mix(in srgb, #fff 62%, transparent) 34%, transparent 52%);
  opacity: 0.55;
}
.combo::after {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: inherit;
  pointer-events: none;
  background: radial-gradient(120% 70% at 0% 0%, color-mix(in srgb, var(--yb-accent) 10%, transparent), transparent 60%);
  opacity: 0.9;
  z-index: -1;
}

.note,
.catch,
.pad {
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: calc(100% - 16px);
  margin: 8px;
  padding: 8px 10px 10px;
  border: 0;
  font: inherit;
  color: var(--yb-paper-ink);
  text-align: left;
}

.note {
  min-height: 64px;
  border-radius: 2px 10px 10px 2px;
  background:
    linear-gradient(90deg, color-mix(in srgb, var(--yb-accent) 35%, transparent) 2px, transparent 2px),
    color-mix(in srgb, var(--yb-note-mute) 70%, #f3e6c4);
  box-shadow: var(--yb-widget-shadow);
}

.note p {
  margin: 0;
  font-size: 12px;
  line-height: 1.45;
}

.note-acts {
  display: flex;
  gap: 8px;
  margin-top: 6px;
}

.note-acts button {
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--yb-accent-deep);
  font: inherit;
  font-size: 11px;
  cursor: pointer;
}

.note-acts .quiet { color: var(--yb-paper-ink-dim); }

.fog {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 3px;
  padding: 12px 14px 10px;
  border: 0;
  background: transparent;
  font: inherit;
  color: inherit;
  text-align: left;
  cursor: pointer;
  transition: padding var(--yb-dur-fast) var(--yb-ease-out), background var(--yb-dur-fast) var(--yb-ease-out);
}
.fog:hover { background: linear-gradient(180deg, color-mix(in srgb, #fff 35%, transparent), transparent 60%); padding-left: 18px; }
.fog:focus-visible { outline: 2px solid var(--yb-accent); outline-offset: -2px; border-radius: 6px; }

.fog-head {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.fog-dots {
  display: inline-flex;
  gap: 3px;
}
.fog-dots i {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: currentColor;
  opacity: 0.45;
  animation: dot-pulse 3.6s var(--yb-ease-out) infinite;
  transition: transform var(--yb-dur-fast) var(--yb-ease-out), opacity var(--yb-dur-fast) var(--yb-ease-out);
}
.fog-dots i:nth-child(2) { animation-delay: 0.4s; }
.fog-dots i:nth-child(3) { animation-delay: 0.8s; }

.fog:hover .fog-dots i {
  animation: none;
  opacity: 0.85;
}
.fog:hover .fog-dots i:nth-child(1) { transform: translateX(5px) scale(1); }
.fog:hover .fog-dots i:nth-child(2) { transform: scale(1.4); }
.fog:hover .fog-dots i:nth-child(3) { transform: translateX(-5px) scale(1); }

@keyframes dot-pulse {
  0%, 60%, 100% { opacity: 0.35; transform: scale(1); }
  30%           { opacity: 0.85; transform: scale(1.25); }
}

.fog-app {
  font-size: 13px;
  font-weight: var(--yb-fw-medium);
  letter-spacing: -0.01em;
  color: var(--yb-text-strong);
}

.fog-title {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--yb-paper-ink-dim);
  font-size: 11px;
}

.spark-body {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 6px 14px 12px;
  border-top: 1px solid color-mix(in srgb, var(--yb-paper-ink) 8%, transparent);
  background:
    linear-gradient(180deg, transparent, color-mix(in srgb, var(--yb-accent-soft) 40%, transparent) 100%);
  animation: spark-in 360ms var(--yb-ease-out) both;
}
.spark-body::before {
  content: "";
  position: absolute;
  top: -0.5px;
  left: 14px;
  right: 14px;
  height: 1px;
  background: linear-gradient(90deg, transparent, color-mix(in srgb, var(--yb-accent) 50%, transparent), transparent);
}
.spark-body p {
  margin: 0;
  font-size: 12.5px;
  line-height: 1.55;
  color: var(--yb-text);
  letter-spacing: -0.005em;
}
.spark-text {
  mask-image: linear-gradient(180deg, #000 88%, transparent);
  -webkit-mask-image: linear-gradient(180deg, #000 88%, transparent);
}
.hl {
  background: color-mix(in srgb, var(--yb-accent-soft) 78%, transparent);
  color: var(--yb-accent-deep);
  font-weight: var(--yb-fw-medium);
  border-radius: 3px;
  padding: 0 2px;
  box-decoration-break: clone;
  -webkit-box-decoration-break: clone;
  box-shadow: 0 1px 0 color-mix(in srgb, var(--yb-accent) 22%, transparent);
}

@keyframes spark-in {
  from { opacity: 0; transform: translateY(4px); filter: blur(2px); }
  to   { opacity: 1; transform: translateY(0);   filter: blur(0); }
}

.catch {
  border-radius: calc(var(--yb-widget-radius) - 8px);
  background: var(--yb-note-mute);
  box-shadow: var(--yb-press);
  cursor: pointer;
}

.kicker {
  color: var(--yb-paper-ink-dim);
  font-size: 10px;
  letter-spacing: 0.04em;
}

.catch > span:last-child {
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  font-size: 12px;
  line-height: 1.4;
}

.pad {
  min-height: 88px;
  resize: none;
  border-radius: 2px;
  background:
    repeating-linear-gradient(
      to bottom,
      transparent 0 21px,
      color-mix(in srgb, var(--yb-line) 80%, transparent) 21px 22px
    );
  box-shadow: var(--yb-press);
  color: var(--yb-paper-ink);
  font-size: 12px;
  line-height: 22px;
}

.pad::placeholder { color: var(--yb-text-faint); }

:deep(.is-note),
:deep(.is-fog),
:deep(.is-scratch) {
  background: transparent;
  box-shadow: none;
  border-color: transparent;
}

.fog:focus-visible,
.catch:focus-visible,
.note-acts button:focus-visible,
.pad:focus-visible {
  outline: 2px solid var(--yb-accent);
  outline-offset: 1px;
}
</style>
