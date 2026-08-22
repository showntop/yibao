<script setup lang="ts">
// 大窗「主屏」页（容器）：页头问候 + 此刻概览 + 待批准收件箱 + 时间线/回顾视图 + 常驻输入条。
// 各块已拆为自包含子组件（feed/FeedTimeline·FeedRecap·FeedInbox），共享样式在 assets/home-feed.css。
//
// 信息架构（重构要点）：
//   1. 双列非对称——左主列一条「统一时间线」，右副列 320px 放需要你动手的事。
//   2. 时间线合并了原「已完成任务」与「Feed 动态」：两者语义都是「发生过的事」，
//      按时间分组（sticky 今天/昨天/更早）+ 行首图标区分类型。
//   3. 待批准是唯一「必须你决定」的事，独占右列顶部，也是全页唯一允许用琥珀强调处。
//   4. Dock 移出主屏 → 归到插件页。
//   5. 筛选从独立一行 chips 改为时间线头部的分段控件。
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { getCurrentWindow } from "@tauri-apps/api/window";
import InputBar from "../../components/common/InputBar.vue";
import FeedTimeline from "./FeedTimeline.vue";
import FeedRecap from "./FeedRecap.vue";
import FeedInbox from "./FeedInbox.vue";
import {
  onWidgets,
  getWidgetsOnce,
  panelAction,
  runInput,
  recapCheck,
  type FeedStats,
  type WidgetPayload,
} from "../../lib/brain";
import { formatContextPrefix, type InputContext } from "../../lib/at-mention";
import { elapsedSince, fmtHHMM } from "../../lib/time";

// chat：提交/点动态 → 切对话页；draft 非空时带给对话页预填
// unread：未读动态数同步给父（Home.vue sidebar 徽标用）
const emit = defineEmits<{ chat: [draft?: string]; unread: [n: number] }>();

// ---- 页头：问候 / 日期 / 此刻时间 ----
const greeting = computed(() => {
  const h = new Date().getHours();
  return h < 6 ? "夜深了" : h < 11 ? "早上好" : h < 14 ? "中午好" : h < 18 ? "下午好" : "晚上好";
});
const dateLine = computed(() => {
  const d = new Date();
  const week = "日一二三四五六"[d.getDay()];
  return `${d.getMonth() + 1} 月 ${d.getDate()} 日 星期${week}`;
});
const now = ref(new Date());
let clockTimer: ReturnType<typeof setInterval> | null = null;
const clockTime = computed(() => fmtHHMM(now.value));

// ---- 此刻概览：概览数字条由 FeedTimeline 的 stats 上报（数据单一真源在时间线域）----
const stats = ref<FeedStats>({ pending_reminders: 0, running_tasks: 0, done_24h: 0, unread: 0, ignored: 0 });
function onStats(s: FeedStats, running: { id: string; kind: string; label: string; prompt: string; status: string; created_at: number }[]) {
  stats.value = s;
  runningTasks.value = running;
}
const overview = computed(() => {
  const o: { key: string; n: number; label: string }[] = [];
  if (stats.value.done_24h > 0) o.push({ key: "done", n: stats.value.done_24h, label: "今日完成" });
  if (stats.value.running_tasks > 0) o.push({ key: "run", n: stats.value.running_tasks, label: "正在跑" });
  if (stats.value.pending_reminders > 0) o.push({ key: "rem", n: stats.value.pending_reminders, label: "待提醒" });
  return o;
});
// 未读数同步给父组件（Home.vue sidebar 徽标）：stats 每次刷新（含乐观更新）即重发。
watch(
  () => stats.value.unread,
  (n) => emit("unread", n),
);

// ---- 主屏 widget：插件一瞥卡（展示型；交互去全面板）----
const widgets = ref<WidgetPayload[]>([]);
const runningTasks = ref<{ id: string; kind: string; label: string; prompt: string; status: string; created_at: number }[]>([]);

async function reloadWidgets() {
  const r = await getWidgetsOnce();
  widgets.value = r.widgets;
}

/** 点 widget 入口 → 调声明的 open 方法开全面板（panel 事件回来，插件页自动接管切页）。 */
function openWidget(w: WidgetPayload) {
  if (!w.open) return;
  const pid = w.panel.split(":")[0];
  void panelAction(w.open, {}, undefined, `panel:${pid}`).catch(() => {});
}

function openTasks() {
  void panelAction("agents.task_list", {}, undefined, "panel:agents").catch(() => {});
}

// ---- 视图切换：动态 | 回顾 ----
type FeedView = "feed" | "recap";
const view = ref<FeedView>("feed");
function openRecap(_day: string) {
  view.value = "recap";
}

// ---- 常驻输入条：提交后切对话页看回复 ----
function submit(text: string, contexts: InputContext[] = []) {
  void runInput(formatContextPrefix(contexts) + text, "pet").catch(() => {});
  emit("chat");
}

// ---- 订阅：widget 实时刷新 + 开窗 recap_check（回顾数据/时间线数据由子组件自订阅）----
let unWidgets: (() => void) | null = null;
let unRecapVisible: (() => void) | null = null;

onMounted(async () => {
  await reloadWidgets();
  unWidgets = await onWidgets((r) => {
    widgets.value = r.widgets;
  });

  // 回顾：开窗触发 recap_check——大脑侧按 recap_last_day 去重，重复 fire 无害。
  // onVisibleChange 在 @tauri-apps/api 2.x 不存在；用 onFocusChanged + isVisible() 兜底
  // （PanelApp.vue 同款套路：窗口被聚焦≈被唤醒）。
  void (async () => {
    try {
      const win = getCurrentWindow();
      const fire = () => { void recapCheck().catch(() => {}); };
      if (await win.isVisible()) fire();
      unRecapVisible = await win.onFocusChanged(async ({ payload: focused }) => {
        if (focused && await win.isVisible()) fire();
      });
    } catch { /* 非 tauri 环境（Vite 设计预览）忽略 */ }
  })();
  clockTimer = setInterval(() => (now.value = new Date()), 1000);
});
onUnmounted(() => {
  unWidgets?.();
  unRecapVisible?.();
  if (clockTimer !== null) clearInterval(clockTimer);
});
</script>

<template>
  <div class="feed-page">
    <!-- 页头：问候 + 日期（左），此刻时间（右，秒级刷新） -->
    <header class="page-head" data-tauri-drag-region>
      <div class="head-line" data-tauri-drag-region>
        <div class="head-left" data-tauri-drag-region>
          <h1 class="greet" data-tauri-drag-region>{{ greeting }}</h1>
          <span class="date" data-tauri-drag-region>{{ dateLine }}</span>
        </div>
        <span class="head-time yb-num" data-tauri-drag-region>{{ clockTime }}</span>
      </div>
    </header>

    <!-- 此刻：AI 正在为你做什么（状态数字 + 进行中 + 插件 widget 一瞥） -->
    <section class="now-card">
      <div class="now-title"><span class="now-dot" />此刻</div>
      <div class="now-body">
        <!-- 状态数字：今日完成 / 正在跑 / 待提醒（0 的项不显） -->
        <div v-if="overview.length" class="now-chips">
          <span v-for="o in overview" :key="o.key" class="now-chip">
            <strong class="yb-num">{{ o.n }}</strong>{{ o.label }}
          </span>
        </div>
        <!-- 进行中任务 -->
        <div v-if="runningTasks.length" class="now-block">
          <div class="now-block-label">正在做</div>
          <button v-for="t in runningTasks" :key="t.id" class="run-row" @click="openTasks">
            <span class="run-dot" />
            <span class="run-main">
              <strong>{{ t.label }}</strong>
              <span>{{ t.prompt }}</span>
            </span>
            <span class="run-time">{{ elapsedSince(t.created_at) }}</span>
          </button>
        </div>
        <!-- 插件 widget 入口行：仅显示标题 + chevron，点全行打开全面板
         * 详细内容在主屏不展开（AI 原生：widget 是入口，不是详情聚合） -->
        <button
          v-for="w in widgets"
          :key="w.panel"
          class="now-widget"
          :disabled="!w.open"
          @click="openWidget(w)"
        >
          <span class="now-widget-title">{{ w.title }}</span>
          <svg class="now-chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M9 6l6 6-6 6" />
          </svg>
        </button>
        <div v-if="!overview.length && !runningTasks.length && !widgets.length" class="now-quiet">
          此刻很清净，随时叫我
        </div>
      </div>
    </section>

    <!-- 需要你决定：唯一必须你动手的事（琥珀强调，有才显） -->
    <FeedInbox />

    <!-- 主体：时间线（动态/回顾） -->
    <div class="body">
      <!-- ===== 左主列：统一时间线 ===== -->
      <section class="timeline-col">
        <!-- 顶部视图切换：动态｜回顾（macOS Segmented） -->
        <div class="segmented view-toggle">
          <button class="seg" :class="{ on: view === 'feed' }" @click="view = 'feed'">动态</button>
          <button class="seg" :class="{ on: view === 'recap' }" @click="view = 'recap'">回顾</button>
        </div>

        <FeedTimeline
          v-if="view === 'feed'"
          @chat="(draft) => emit('chat', draft)"
          @unread="(n) => emit('unread', n)"
          @stats="onStats"
        />
        <FeedRecap v-else @open-recap="openRecap" />
      </section>
    </div>

    <!-- 常驻输入条：提交后切对话页看回复 -->
    <div class="bar">
      <InputBar @submit="submit" />
    </div>
  </div>
</template>
