<script setup lang="ts">
// 大窗「主屏」页（OS 感 §4.2）：问候条 + Feed 动态 + 插件 Dock + 常驻输入条。
// 定位：回答「它为我做了什么 / 有什么等我处理」；发起对话由底部输入条完成（提交后切对话页）。
// Feed 点击 → 带上下文草稿切对话页（草稿自包含：大脑看不到 Feed，上下文随草稿走）。
import { computed, onMounted, onUnmounted, ref } from "vue";
import { invoke } from "@tauri-apps/api/core";
import InputBar from "./InputBar.vue";
import {
  getFeedOnce,
  fetchFeed,
  onFeed,
  onBrainEvent,
  panelAction,
  runInput,
  type FeedItem,
  type FeedStats,
} from "../lib/brain";

// chat：提交/点动态 → 切对话页；draft 非空时带给对话页预填
const emit = defineEmits<{ chat: [draft?: string] }>();

// ---- 问候条 ----
const stats = ref<FeedStats>({ pending_reminders: 0, running_tasks: 0, done_24h: 0 });

const greeting = computed(() => {
  const h = new Date().getHours();
  if (h < 6) return "夜深了";
  if (h < 12) return "早上好";
  if (h < 14) return "中午好";
  if (h < 18) return "下午好";
  return "晚上好";
});

const dateLine = computed(() => {
  const d = new Date();
  const week = "日一二三四五六"[d.getDay()];
  return `${d.getMonth() + 1}月${d.getDate()}日 星期${week}`;
});

const statChips = computed(() => {
  const chips: { icon: string; text: string }[] = [];
  if (stats.value.pending_reminders > 0)
    chips.push({ icon: "⏰", text: `待办提醒 ${stats.value.pending_reminders}` });
  if (stats.value.running_tasks > 0)
    chips.push({ icon: "🏃", text: `进行中任务 ${stats.value.running_tasks}` });
  if (stats.value.done_24h > 0)
    chips.push({ icon: "✅", text: `近 24h 完成 ${stats.value.done_24h}` });
  return chips;
});

// ---- Feed 动态 ----
const items = ref<FeedItem[]>([]);
const loaded = ref(false);

async function reload() {
  const r = await getFeedOnce();
  items.value = r.items;
  stats.value = r.stats;
  loaded.value = true;
}

/** 相对时间：刚刚 / N 分钟前 / N 小时前 / 昨天 / M月D日 */
function relTime(ts: number): string {
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  const d = new Date(ts * 1000);
  const today = new Date();
  if (d.getDate() === today.getDate() - 1 && diff < 172800) return "昨天";
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}

function kindIcon(it: FeedItem): string {
  if (it.kind === "reminder") return "⏰";
  if (it.kind === "task") return "🛠";
  return "💬";
}

/** 点动态 → 带自包含上下文草稿切对话页（大脑看不到 Feed，上下文随草稿走）。 */
function openInChat(it: FeedItem) {
  const oneLine = it.text.replace(/\s+/g, " ").trim();
  const truncated = oneLine.length > 60 ? oneLine.slice(0, 60) + "…" : oneLine;
  const prompt = typeof it.meta?.prompt === "string" && it.meta.prompt ? it.meta.prompt : "";
  const draft = it.kind === "task" && prompt
    ? `关于任务「${prompt.length > 40 ? prompt.slice(0, 40) + "…" : prompt}」：`
    : `关于「${truncated}」：`;
  emit("chat", draft);
}

// ---- 插件 Dock ----
interface PluginInfo { id: string; name: string }
const plugins = ref<PluginInfo[]>([]);

async function loadPlugins() {
  try {
    plugins.value = await invoke<PluginInfo[]>("list_plugins");
  } catch {
    plugins.value = [];
  }
}

/** 点插件 → 调它的 list 直调开主面板（panel 事件回来，插件页自动接管切页）。 */
function launchPlugin(p: PluginInfo) {
  void panelAction(`${p.id}.list`, {}, undefined, `panel:${p.id}`).catch(() => {});
}

function pluginIcon(name: string): string {
  return [...name][0] ?? "?";
}

// ---- 常驻输入条：提交后切对话页看回复 ----
function submit(text: string) {
  void runInput(text, "pet").catch(() => {});
  emit("chat");
}

// ---- 订阅：新动态（任务播报/提醒触发）实时刷新 Feed ----
let unFeed: (() => void) | null = null;
let unEvent: (() => void) | null = null;
let refetchTimer: ReturnType<typeof setTimeout> | null = null;

onMounted(async () => {
  await reload();
  void loadPlugins();
  unFeed = await onFeed((r) => {
    items.value = r.items;
    stats.value = r.stats;
  });
  unEvent = await onBrainEvent((e) => {
    if (e.kind !== "reminder") return;
    if (refetchTimer !== null) clearTimeout(refetchTimer);
    refetchTimer = setTimeout(() => void fetchFeed().catch(() => {}), 800);
  });
});
onUnmounted(() => {
  unFeed?.();
  unEvent?.();
  if (refetchTimer !== null) clearTimeout(refetchTimer);
});
</script>

<template>
  <div class="feed-page">
    <!-- 问候条：时段问候 + 日期 + 统计 chips（它为你盯着的事） -->
    <header class="hero" data-tauri-drag-region>
      <div class="hero-top" data-tauri-drag-region>
        <span class="hero-hi" data-tauri-drag-region>{{ greeting }}</span>
        <span class="hero-date" data-tauri-drag-region>{{ dateLine }}</span>
      </div>
      <div class="hero-chips">
        <span v-for="c in statChips" :key="c.text" class="h-chip">{{ c.icon }} {{ c.text }}</span>
        <span v-if="!statChips.length" class="h-chip dim">今天安安静静，叫我做点什么吧</span>
      </div>
    </header>

    <div class="scroll">
      <!-- Feed 动态：它在后台干的事，按时间倒序 -->
      <section class="sec">
        <div class="sec-title">动态</div>
        <div v-if="loaded && !items.length" class="f-empty">
          还没有动态——派个任务、设个提醒，干完活我会记在这里
        </div>
        <button v-for="it in items" :key="it.id" class="f-row" @click="openInChat(it)">
          <span class="f-icon">{{ kindIcon(it) }}</span>
          <span class="f-text">{{ it.text }}</span>
          <span class="f-time">{{ relTime(it.ts) }}</span>
        </button>
      </section>

      <!-- 插件 Dock：常用能力直达（主屏的「应用」） -->
      <section class="sec">
        <div class="sec-title">常用</div>
        <div class="dock">
          <button v-for="p in plugins" :key="p.id" class="dock-item" @click="launchPlugin(p)">
            <span class="dock-icon">{{ pluginIcon(p.name) }}</span>
            <span class="dock-name">{{ p.name }}</span>
          </button>
          <div v-if="!plugins.length" class="f-empty">没有发现插件</div>
        </div>
      </section>
    </div>

    <!-- 常驻输入条：主屏任何位置都能直接开问（提交后切对话页看回复） -->
    <div class="bar">
      <InputBar @submit="submit" />
    </div>
  </div>
</template>

<style scoped>
.feed-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 0 var(--yb-space-3) var(--yb-space-2);
}
/* 问候条：主屏的「解锁第一眼」 */
.hero {
  padding: var(--yb-space-4) var(--yb-space-2) var(--yb-space-2);
  user-select: none;
}
.hero-top {
  display: flex;
  align-items: baseline;
  gap: var(--yb-space-3);
}
.hero-hi {
  font-size: 22px;
  font-weight: 700;
  letter-spacing: 0.01em;
  color: var(--yb-text);
}
.hero-date {
  font-size: var(--yb-fs-md);
  color: var(--yb-text-dim);
}
.hero-chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--yb-space-2);
  margin-top: var(--yb-space-2);
}
.h-chip {
  padding: 3px var(--yb-space-3);
  border-radius: var(--yb-radius-lg);
  background: var(--yb-accent-soft);
  color: var(--yb-accent-deep);
  font-size: var(--yb-fs-md);
}
.h-chip.dim {
  background: transparent;
  color: var(--yb-text-dim);
}

.scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  scrollbar-width: thin;
}
.scroll::-webkit-scrollbar {
  width: 6px;
}
.scroll::-webkit-scrollbar-thumb {
  background: var(--yb-surface-border);
  border-radius: 3px;
}
.sec {
  margin-top: var(--yb-space-3);
}
.sec-title {
  padding: 0 var(--yb-space-2) var(--yb-space-2);
  font-size: var(--yb-fs-sm);
  font-weight: 600;
  color: var(--yb-text-dim);
  letter-spacing: 0.04em;
}

/* Feed 行：图标 + 文本（两行截断）+ 相对时间；点击带上下文进对话 */
.f-row {
  display: flex;
  align-items: flex-start;
  gap: var(--yb-space-2);
  width: 100%;
  padding: var(--yb-space-2) var(--yb-space-3);
  margin-bottom: var(--yb-space-2);
  border: 1px solid var(--yb-surface-border);
  border-radius: 14px;
  background: var(--yb-surface-solid);
  box-shadow: var(--yb-shadow-soft);
  cursor: pointer;
  font-family: inherit;
  text-align: left;
  transition: all 0.15s ease;
}
.f-row:hover {
  border-color: var(--yb-accent);
  transform: translateY(-1px);
}
.f-icon {
  flex-shrink: 0;
  font-size: 14px;
  line-height: 1.6;
}
.f-text {
  flex: 1;
  min-width: 0;
  font-size: var(--yb-fs-md);
  line-height: 1.55;
  color: var(--yb-text);
  white-space: pre-wrap;
  word-break: break-word;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.f-time {
  flex-shrink: 0;
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-dim);
  line-height: 1.8;
}
.f-empty {
  padding: var(--yb-space-4) var(--yb-space-3);
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-md);
  text-align: center;
}

/* Dock：首字圆形图标 + 名称，与 iOS Dock 同语义 */
.dock {
  display: flex;
  flex-wrap: wrap;
  gap: var(--yb-space-3);
  padding: var(--yb-space-2);
}
.dock-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  width: 64px;
  border: none;
  background: transparent;
  cursor: pointer;
  font-family: inherit;
}
.dock-icon {
  width: 44px;
  height: 44px;
  display: grid;
  place-items: center;
  border-radius: 14px;
  background: linear-gradient(160deg, var(--yb-accent-soft), var(--yb-surface-solid));
  border: 1px solid var(--yb-surface-border);
  box-shadow: var(--yb-shadow-soft);
  font-size: 18px;
  font-weight: 600;
  color: var(--yb-accent-deep);
  transition: all 0.15s ease;
}
.dock-item:hover .dock-icon {
  transform: translateY(-2px);
  border-color: var(--yb-accent);
}
.dock-name {
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-dim);
  max-width: 64px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.bar {
  padding-top: var(--yb-space-2);
}
</style>
