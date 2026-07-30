<script setup lang="ts">
// 大窗「主屏」页（OS 感 §4.2）：问候条 + Feed 动态 + 插件 Dock + 常驻输入条。
// 定位：回答「它为我做了什么 / 有什么等我处理」；发起对话由底部输入条完成（提交后切对话页）。
// Feed 点击 → 带上下文草稿切对话页（草稿自包含：大脑看不到 Feed，上下文随草稿走）。
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import InputBar from "./InputBar.vue";
import SchemaPanel from "./SchemaPanel.vue";
import {
  getFeedOnce,
  fetchFeed,
  onFeed,
  getWidgetsOnce,
  fetchWidgets,
  onWidgets,
  onBrainEvent,
  onPendingConfirms,
  panelAction,
  runInput,
  sendConfirmBatch,
  markFeedRead,
  markAllFeedRead,
  markFeedStatus,
  feedTierOf,
  setDockPin,
  getDockListOnce,
  onDockList,
  onDockPinSet,
  type FeedItem,
  type FeedStats,
  type RunningTask,
  type PendingConfirm,
  type WidgetPayload,
  type DockItem,
} from "../lib/brain";

// chat：提交/点动态 → 切对话页；draft 非空时带给对话页预填
// unread：未读动态数同步给父（Home.vue nav 红点用）
const emit = defineEmits<{ chat: [draft?: string]; unread: [n: number] }>();

// ---- 问候条 ----
const stats = ref<FeedStats>({ pending_reminders: 0, running_tasks: 0, done_24h: 0, unread: 0, ignored: 0 });

// 动态问候：时段 + 基于 stats 拼 1-2 句叙事；全 0 退化「暂时清净，随时叫我」。
// 每次 onFeed 刷新 stats 即重算（=每次回主屏刷新）。
const greeting = computed(() => {
  const h = new Date().getHours();
  const slot = h < 11 ? "早上好" : h < 18 ? "下午好" : "晚上好";
  const bits: string[] = [];
  if (stats.value.done_24h > 0) bits.push(`过去一天跑完了 ${stats.value.done_24h} 个任务`);
  if (stats.value.pending_reminders > 0) bits.push(`今天有 ${stats.value.pending_reminders} 个提醒`);
  if (stats.value.running_tasks > 0) bits.push(`${stats.value.running_tasks} 个任务正在跑`);
  const tail = bits.length ? bits.slice(0, 2).join("、") : "暂时清净，随时叫我";
  return `${slot}，${tail}`;
});

const dateLine = computed(() => {
  const d = new Date();
  const week = "日一二三四五六"[d.getDay()];
  return `${d.getMonth() + 1}月${d.getDate()}日 星期${week}`;
});

// 未读数同步给父组件（Home.vue nav 红点）：stats 每次刷新（含乐观更新）即重发。
watch(
  () => stats.value.unread,
  (n) => emit("unread", n),
);

// ---- Feed 动态 ----
const items = ref<FeedItem[]>([]);
const runningTasks = ref<RunningTask[]>([]);
const loaded = ref(false);

// ---- 处置态筛选 + 忽略折叠（C 子项目 §4.5）----
// statusFilter：全部 / 跟进 / 忽略；跟进项单独看，忽略项默认折叠，筛"忽略"=展开忽略项。
type StatusFilter = "all" | "follow" | "ignore";
const statusFilter = ref<StatusFilter>("all");
// all 视图下：忽略项是否展开（点 fold 切换；切别的筛选自动收回）
const showIgnored = ref(false);

// 完成任务基础列表（最近 5 条 task，保持原"已完成"区容量）
const completedBase = computed(() => items.value.filter((it) => it.kind === "task").slice(0, 5));
// 按处置态筛选后展示
const completedTasks = computed(() => filterByStatus(completedBase.value));
// all 视图下被折叠的忽略项
const completedIgnored = computed(() => completedBase.value.filter((it) => it.status === "ignore"));

// 动态基础列表（reminder + event，按时间倒序）
const activityBase = computed(() => items.value.filter((it) => it.kind !== "task"));
const activityItems = computed(() => filterByStatus(activityBase.value));
const activityIgnored = computed(() => activityBase.value.filter((it) => it.status === "ignore"));

/** 按 statusFilter 筛选：follow/ignore 单独看；all 排除忽略项（折叠展示）。 */
function filterByStatus(list: FeedItem[]): FeedItem[] {
  if (statusFilter.value === "follow") return list.filter((it) => it.status === "follow");
  if (statusFilter.value === "ignore") return list.filter((it) => it.status === "ignore");
  return list.filter((it) => it.status !== "ignore");
}

// 有任何 Feed 项时显示筛选 chips（完成 + 动态任一非空）
const hasAnyFeedItem = computed(() => completedBase.value.length > 0 || activityBase.value.length > 0);

// 完成区是否渲染：各筛选下按是否有匹配项判定（all 时 base 非空即渲染以容纳忽略折叠）
const showCompletedZone = computed(() => {
  if (statusFilter.value === "follow") return completedBase.value.some((it) => it.status === "follow");
  if (statusFilter.value === "ignore") return completedIgnored.value.length > 0;
  return completedBase.value.length > 0;
});

// 动态区空态文案：随筛选变化（跟进/忽略无项时给明确提示）
const activityEmptyText = computed(() => {
  if (statusFilter.value === "follow") return "没有跟进的动态";
  if (statusFilter.value === "ignore") return "没有忽略的动态";
  return "还没有其他动态——提醒、记忆和主动消息会出现在这里";
});

async function reload() {
  const r = await getFeedOnce();
  items.value = r.items;
  stats.value = r.stats;
  runningTasks.value = r.running_tasks ?? [];
  loaded.value = true;
}

/** 全部已读：乐观置 items read=1 + stats.unread=0，后端确认后重拉对齐。 */
async function markAllRead() {
  const prevReads = items.value.map((it) => it.read);
  const prevUnread = stats.value.unread;
  items.value.forEach((it) => (it.read = 1));
  stats.value = { ...stats.value, unread: 0 };
  try {
    await markAllFeedRead();
  } catch {
    // 失败回滚乐观态
    items.value.forEach((it, i) => (it.read = prevReads[i]));
    stats.value = { ...stats.value, unread: prevUnread };
    return;
  }
  // 重拉对齐（markAll 成功后的权威状态）
  void fetchFeed().catch(() => {});
}

// ---- 待批准队列（OS 感 §4.5 收件箱 Question 面：高风险操作排队一键批/拒） ----
// 收件箱：多选 + 顶部一键批/拒 + 每条独立 remember。批量为主，单条快批为辅。
const approvals = ref<PendingConfirm[]>([]);
// 多选集合：N>1 时默认全选（鼓励批量）；N<=1 留空走单条快批按钮。
const selectedApprovals = ref<Set<string>>(new Set());
// 每条独立 remember（默认 false；勾选后该技能会话内不再询问——大脑侧会话级记忆）
const rememberMap = ref<Record<string, boolean>>({});

const selectedCount = computed(() => selectedApprovals.value.size);
const hasInbox = computed(() =>
  runningTasks.value.length > 0 || approvals.value.length > 0 || showCompletedZone.value,
);

/** brain.ts sendConfirmBatch 内部乐观出队（_pcRemove → emit → approvals 替换）后校正默认选择：
 *  N>1 全选鼓励批量；N<=1 清空走单条快批。同时清理 rememberMap 陈旧项。 */
watch(
  () => approvals.value,
  (l) => {
    if (l.length > 1) {
      selectedApprovals.value = new Set(l.map((p) => p.id));
    } else {
      selectedApprovals.value = new Set();
    }
    const live = new Set(l.map((p) => p.id));
    for (const k of Object.keys(rememberMap.value)) {
      if (!live.has(k)) delete rememberMap.value[k];
    }
  },
);

function isSelected(id: string): boolean {
  return selectedApprovals.value.has(id);
}

function onToggleSelect(id: string, e: Event) {
  const checked = (e.target as HTMLInputElement).checked;
  const next = new Set(selectedApprovals.value);
  if (checked) next.add(id);
  else next.delete(id);
  selectedApprovals.value = next;
}

function rememberOf(id: string): boolean {
  return rememberMap.value[id] ?? false;
}

function onToggleRemember(id: string, e: Event) {
  rememberMap.value = { ...rememberMap.value, [id]: (e.target as HTMLInputElement).checked };
}

/** 单条快批/拒：调 sendConfirmBatch 单条（remember 按本条勾选）。
 *  乐观出队与失败回滚由 brain.ts 的共享队列统一处理；这里保留局部兜底。 */
async function decideApproval(p: PendingConfirm, approved: boolean) {
  const remember = rememberOf(p.id);
  try {
    await sendConfirmBatch([{ id: p.id, approved, remember }]);
  } catch {
    if (!approvals.value.some((x) => x.id === p.id)) {
      approvals.value = [...approvals.value, p];
    }
  }
}

/** 全部批准/拒绝：对选中的项调 sendConfirmBatch（按各条 remember 勾选）。
 *  乐观出队与失败回滚由共享队列处理；空选时调用方禁用。 */
async function batchDecide(approved: boolean) {
  const targets = approvals.value.filter((p) => selectedApprovals.value.has(p.id));
  if (!targets.length) return;
  const items = targets.map((p) => ({
    id: p.id,
    approved,
    remember: rememberOf(p.id),
  }));
  // 局部快照：共享队列会回滚；此处兜底避免订阅链异常时卡片消失。
  const snapshot = targets.slice();
  try {
    await sendConfirmBatch(items);
    targets.forEach((p) => delete rememberMap.value[p.id]);
  } catch {
    const existing = new Set(approvals.value.map((p) => p.id));
    const restore = snapshot.filter((p) => !existing.has(p.id));
    if (restore.length) approvals.value = [...approvals.value, ...restore];
  }
}

// ---- 主屏 widget：插件一瞥卡（schema 协议的 widget 类型，展示型；交互去全面板） ----
const widgets = ref<WidgetPayload[]>([]);

async function reloadWidgets() {
  const r = await getWidgetsOnce();
  widgets.value = r.widgets;
}

/** 点 widget 标题 → 调声明的 open 方法开全面板（panel 事件回来，插件页自动接管切页）。 */
function openWidget(w: WidgetPayload) {
  if (!w.open) return;
  const pid = w.panel.split(":")[0];
  void panelAction(w.open, {}, undefined, `panel:${pid}`).catch(() => {});
}

/** widget 数据兜底：取数异常时给 {}（SchemaPanel 要求 Record）。 */
function widgetData(w: WidgetPayload): Record<string, unknown> {
  return (w.data && typeof w.data === "object" ? w.data : {}) as Record<string, unknown>;
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

function elapsedSince(ts: number): string {
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (seconds < 60) return "刚开始";
  if (seconds < 3600) return `已运行 ${Math.floor(seconds / 60)} 分钟`;
  return `已运行 ${Math.floor(seconds / 3600)} 小时`;
}

function taskStatusLabel(it: FeedItem): string {
  const status = String(it.meta?.status ?? "done");
  return ({ done: "完成", failed: "失败", stopped: "已停止", interrupted: "已中断" } as Record<string, string>)[status]
    ?? "已结束";
}

function taskStatus(it: FeedItem): string {
  return String(it.meta?.status ?? "done");
}

function openTasks() {
  void panelAction("agents.task_list", {}, undefined, "panel:agents").catch(() => {});
}

/** 点动态 → 乐观置已读 + 带自包含上下文草稿切对话页（大脑看不到 Feed，上下文随草稿走）。
 *  it 来自 items 的 reactive proxy，直接改 it.read 即触发视图更新。 */
async function openInChat(it: FeedItem) {
  if (it.read === 0) {
    const prevUnread = stats.value.unread;
    it.read = 1; // 乐观置已读
    if (stats.value.unread > 0) stats.value = { ...stats.value, unread: stats.value.unread - 1 };
    try {
      await markFeedRead(it.id);
    } catch {
      // 失败回滚
      it.read = 0;
      stats.value = { ...stats.value, unread: prevUnread };
    }
  }
  const oneLine = it.text.replace(/\s+/g, " ").trim();
  const truncated = oneLine.length > 60 ? oneLine.slice(0, 60) + "…" : oneLine;
  const prompt = typeof it.meta?.prompt === "string" && it.meta.prompt ? it.meta.prompt : "";
  const draft = it.kind === "task" && prompt
    ? `关于任务「${prompt.length > 40 ? prompt.slice(0, 40) + "…" : prompt}」：`
    : `关于「${truncated}」：`;
  emit("chat", draft);
}

// ---- 处置态（C 子项目 §4.5）：跟进/忽略，乐观改 it.status + 失败回滚 ----

/** 设置处置态：乐观改 it.status + 失败回滚（参考 openInChat 的乐观模式）。
 *  与 read 正交：跟进/忽略不改已读态，read 筛选/高亮不受影响。 */
async function setStatus(it: FeedItem, status: "none" | "follow" | "ignore") {
  const prev = it.status;
  if (prev === status) return; // 幂等
  it.status = status;
  try {
    await markFeedStatus(it.id, status);
  } catch {
    it.status = prev; // 失败回滚
  }
}

/** 跟进/忽略按钮：点已在态则取消回 none（再点取消），否则切到该态。 */
function toggleStatus(it: FeedItem, target: "follow" | "ignore") {
  void setStatus(it, it.status === target ? "none" : target);
}

/** 切处置态筛选；回到 all 自动收起忽略折叠（保持默认折叠视图）。 */
function setStatusFilter(f: StatusFilter) {
  statusFilter.value = f;
  if (f === "all") showIgnored.value = false;
}

/** tier 轻着色 class（Review 蓝 / Notify 灰，按 kind 推导；B 分区已隐含 tier，此处仅轻标识）。 */
function tierClass(it: FeedItem): string {
  return `tier-${feedTierOf(it.kind).toLowerCase()}`;
}

// ---- 插件 Dock（后端 dock_list：pinned 优先 + 频率补齐；图钉可固定/取消）----
const dock = ref<DockItem[]>([]);

async function loadDock() {
  const r = await getDockListOnce();
  dock.value = r.dock;
}

/** 点插件 → 调它的 list 直调开主面板（panel 事件回来，插件页自动接管切页）。 */
function launchPlugin(p: DockItem) {
  void panelAction(`${p.id}.list`, {}, undefined, `panel:${p.id}`).catch(() => {});
}

/** 图钉：乐观翻转 pinned，后端确认后 onDockPinSet 回执整体覆盖对齐。 */
async function togglePin(p: DockItem) {
  const prev = p.pinned;
  p.pinned = !p.pinned;
  try {
    await setDockPin(p.id, p.pinned);
  } catch {
    p.pinned = prev; // 失败回滚
  }
  // onDockPinSet 订阅会用后端权威 dock 数组覆盖（成功/失败都对齐）
}

function pluginIcon(name: string): string {
  return [...name][0] ?? "?";
}

// ---- 常驻输入条：提交后切对话页看回复 ----
function submit(text: string) {
  void runInput(text, "pet").catch(() => {});
  emit("chat");
}

// ---- 订阅：新动态（任务播报/提醒触发）实时刷新 Feed 与 widget；待批队列 + Dock 订阅 ----
let unFeed: (() => void) | null = null;
let unWidgets: (() => void) | null = null;
let unEvent: (() => void) | null = null;
let unApprovals: (() => void) | null = null;
let unDock: (() => void) | null = null;
let unDockPin: (() => void) | null = null;
let refetchTimer: ReturnType<typeof setTimeout> | null = null;

function scheduleFeedRefresh() {
  if (refetchTimer !== null) clearTimeout(refetchTimer);
  refetchTimer = setTimeout(() => {
    void fetchFeed().catch(() => {});
    void fetchWidgets().catch(() => {});
  }, 800);
}

onMounted(async () => {
  await Promise.all([reload(), reloadWidgets(), loadDock()]);
  unApprovals = onPendingConfirms((l) => (approvals.value = l));
  unFeed = await onFeed((r) => {
    items.value = r.items;
    stats.value = r.stats;
    runningTasks.value = r.running_tasks ?? [];
  });
  unWidgets = await onWidgets((r) => {
    widgets.value = r.widgets;
  });
  unDock = await onDockList((r) => {
    dock.value = r.dock;
  });
  unDockPin = await onDockPinSet((r) => {
    dock.value = r.dock; // 后端权威数组覆盖（含图钉操作回执）
  });
  unEvent = await onBrainEvent((e) => {
    const agentChanged = e.kind === "action_result"
      && !!e.action?.skill_id?.startsWith("agents.");
    if (e.kind === "reminder" || agentChanged) scheduleFeedRefresh();
  });
});
onUnmounted(() => {
  unFeed?.();
  unWidgets?.();
  unEvent?.();
  unApprovals?.();
  unDock?.();
  unDockPin?.();
  if (refetchTimer !== null) clearTimeout(refetchTimer);
});
</script>

<template>
  <div class="feed-page">
    <!-- 问候条：时段 + 叙事（它为你盯着的事）+ 日期 -->
    <header class="hero" data-tauri-drag-region>
      <div class="hero-top" data-tauri-drag-region>
        <span class="hero-hi" data-tauri-drag-region>{{ greeting }}</span>
        <span class="hero-date" data-tauri-drag-region>{{ dateLine }}</span>
      </div>
    </header>

    <div class="scroll">
      <!-- 处置态筛选 chips（C 子项目）：全部 / 跟进 / 忽略；同时筛完成区 + 动态区 -->
      <div v-if="hasAnyFeedItem" class="filter-bar">
        <button
          class="chip"
          :class="{ active: statusFilter === 'all' }"
          @click="setStatusFilter('all')"
        >全部</button>
        <button
          class="chip"
          :class="{ active: statusFilter === 'follow' }"
          @click="setStatusFilter('follow')"
        >跟进</button>
        <button
          class="chip"
          :class="{ active: statusFilter === 'ignore' }"
          @click="setStatusFilter('ignore')"
        >忽略</button>
      </div>

      <!-- 任务收件箱：同页纵向三区，空区折叠。 -->
      <section v-if="hasInbox" class="sec sec-inbox">
        <div class="sec-title inbox-title">任务收件箱</div>

        <div v-if="runningTasks.length" class="inbox-zone zone-running">
          <div class="zone-title">进行中 · {{ runningTasks.length }}</div>
          <button
            v-for="task in runningTasks"
            :key="task.id"
            class="task-row running-row"
            @click="openTasks"
          >
            <span class="task-dot running"></span>
            <span class="task-main">
              <strong>{{ task.label }}</strong>
              <span>{{ task.prompt }}</span>
            </span>
            <span class="task-time">{{ elapsedSince(task.created_at) }}</span>
            <span class="task-go">查看 ›</span>
          </button>
        </div>

        <div v-if="approvals.length" class="inbox-zone zone-approvals sec-approvals">
          <div class="zone-title inbox-head">
            <span>待批准 · {{ approvals.length }}</span>
            <div v-if="approvals.length > 1" class="batch-btns">
              <button class="batch-no" :disabled="selectedCount === 0" @click="batchDecide(false)">
                全部拒绝{{ selectedCount ? ` (${selectedCount})` : "" }}
              </button>
              <button class="batch-yes" :disabled="selectedCount === 0" @click="batchDecide(true)">
                全部批准{{ selectedCount ? ` (${selectedCount})` : "" }}
              </button>
            </div>
          </div>
          <div
            v-for="p in approvals"
            :key="p.id"
            class="a-card"
            :class="{ selected: approvals.length > 1 && isSelected(p.id) }"
          >
            <label v-if="approvals.length > 1" class="a-check" title="选中后可一键批量">
              <input type="checkbox" :checked="isSelected(p.id)" @change="onToggleSelect(p.id, $event)" />
            </label>
            <div class="a-info">
              <span class="a-label">🔐 {{ p.label || p.skill }}</span>
              <span class="a-desc">{{ p.desc || p.skill }}</span>
            </div>
            <label class="a-remember" title="勾选后该技能在本会话内不再询问">
              <input type="checkbox" :checked="rememberOf(p.id)" @change="onToggleRemember(p.id, $event)" />
              <span>记住</span>
            </label>
            <div class="a-btns">
              <button class="a-no" @click="decideApproval(p, false)">拒绝</button>
              <button class="a-yes" @click="decideApproval(p, true)">批准</button>
            </div>
          </div>
        </div>

        <div v-if="showCompletedZone" class="inbox-zone zone-completed">
          <div class="zone-title">已完成 · 最近 {{ completedBase.length }} 条</div>
          <div
            v-for="it in completedTasks"
            :key="it.id"
            class="task-row completed-row"
            :class="[tierClass(it), { unread: it.read === 0, [`status-${it.status}`]: it.status !== 'none' }]"
            role="button"
            tabindex="0"
            @click="openInChat(it)"
            @keydown.enter="openInChat(it)"
          >
            <span class="tier-dot" aria-hidden="true"></span>
            <span class="task-status" :class="`status-${taskStatus(it)}`">{{ taskStatusLabel(it) }}</span>
            <span class="task-main"><span>{{ it.text }}</span></span>
            <span class="task-time">{{ relTime(it.ts) }}</span>
            <div class="f-actions" @click.stop>
              <button
                class="f-act"
                :class="{ on: it.status === 'follow' }"
                title="跟进"
                @click="toggleStatus(it, 'follow')"
              >跟进</button>
              <button
                class="f-act act-ignore"
                :class="{ on: it.status === 'ignore' }"
                title="忽略"
                @click="toggleStatus(it, 'ignore')"
              >忽略</button>
            </div>
          </div>
          <!-- 忽略折叠（仅 all 视图）：默认收起，展开后仍可取消忽略 -->
          <div v-if="statusFilter === 'all' && completedIgnored.length" class="ignored-fold">
            <button v-if="!showIgnored" class="fold-toggle" @click="showIgnored = true">
              已忽略 {{ completedIgnored.length }} 条 ›
            </button>
            <template v-else>
              <div class="fold-header">
                <span>已忽略 {{ completedIgnored.length }} 条</span>
                <button class="fold-collapse" @click="showIgnored = false">收起</button>
              </div>
              <div
                v-for="it in completedIgnored"
                :key="it.id"
                class="task-row completed-row ignored-row"
                :class="[tierClass(it)]"
                role="button"
                tabindex="0"
                @click="openInChat(it)"
                @keydown.enter="openInChat(it)"
              >
                <span class="tier-dot" aria-hidden="true"></span>
                <span class="task-status" :class="`status-${taskStatus(it)}`">{{ taskStatusLabel(it) }}</span>
                <span class="task-main"><span>{{ it.text }}</span></span>
                <span class="task-time">{{ relTime(it.ts) }}</span>
                <div class="f-actions" @click.stop>
                  <button
                    class="f-act"
                    :class="{ on: it.status === 'follow' }"
                    title="跟进"
                    @click="toggleStatus(it, 'follow')"
                  >跟进</button>
                  <button
                    class="f-act act-ignore"
                    :class="{ on: it.status === 'ignore' }"
                    title="取消忽略"
                    @click="toggleStatus(it, 'ignore')"
                  >忽略</button>
                </div>
              </div>
            </template>
          </div>
        </div>
      </section>

      <!-- widget 卡片区：插件供的一瞥卡（待办提醒/任务动态/最近闪念），点标题进全面板 -->
      <section v-if="widgets.length" class="sec sec-widgets">
        <div class="w-row">
          <div v-for="w in widgets" :key="w.panel" class="w-card">
            <button class="w-head" :class="{ link: !!w.open }" @click="openWidget(w)">
              <span class="w-title">{{ w.title }}</span>
              <span v-if="w.open" class="w-go">›</span>
            </button>
            <div class="w-body">
              <SchemaPanel :panel="w.panel" :schema="(w.schema as Record<string, any>)" :data="widgetData(w)" />
            </div>
          </div>
        </div>
      </section>

      <!-- Feed 动态：它在后台干的事，按时间倒序；未读项高亮，点击即已读 -->
      <section class="sec">
        <div class="sec-title feed-head">
          <span>动态</span>
          <button v-if="stats.unread > 0" class="mark-all" @click="markAllRead">全部已读</button>
        </div>
        <div
          v-if="loaded && !activityItems.length && !(statusFilter === 'all' && activityIgnored.length)"
          class="f-empty"
        >
          {{ activityEmptyText }}
        </div>
        <div
          v-for="it in activityItems"
          :key="it.id"
          class="f-row"
          :class="[tierClass(it), { unread: it.read === 0, [`status-${it.status}`]: it.status !== 'none' }]"
          role="button"
          tabindex="0"
          @click="openInChat(it)"
          @keydown.enter="openInChat(it)"
        >
          <span class="tier-dot" aria-hidden="true"></span>
          <span class="f-icon">{{ kindIcon(it) }}</span>
          <span class="f-text">{{ it.text }}</span>
          <span class="f-time">{{ relTime(it.ts) }}</span>
          <div class="f-actions" @click.stop>
            <button
              class="f-act"
              :class="{ on: it.status === 'follow' }"
              title="跟进"
              @click="toggleStatus(it, 'follow')"
            >跟进</button>
            <button
              class="f-act act-ignore"
              :class="{ on: it.status === 'ignore' }"
              title="忽略"
              @click="toggleStatus(it, 'ignore')"
            >忽略</button>
          </div>
        </div>
        <!-- 忽略折叠（仅 all 视图）：与完成区同语义 -->
        <div v-if="statusFilter === 'all' && activityIgnored.length" class="ignored-fold">
          <button v-if="!showIgnored" class="fold-toggle" @click="showIgnored = true">
            已忽略 {{ activityIgnored.length }} 条 ›
          </button>
          <template v-else>
            <div class="fold-header">
              <span>已忽略 {{ activityIgnored.length }} 条</span>
              <button class="fold-collapse" @click="showIgnored = false">收起</button>
            </div>
            <div
              v-for="it in activityIgnored"
              :key="it.id"
              class="f-row ignored-row"
              :class="[tierClass(it)]"
              role="button"
              tabindex="0"
              @click="openInChat(it)"
              @keydown.enter="openInChat(it)"
            >
              <span class="tier-dot" aria-hidden="true"></span>
              <span class="f-icon">{{ kindIcon(it) }}</span>
              <span class="f-text">{{ it.text }}</span>
              <span class="f-time">{{ relTime(it.ts) }}</span>
              <div class="f-actions" @click.stop>
                <button
                  class="f-act"
                  :class="{ on: it.status === 'follow' }"
                  title="跟进"
                  @click="toggleStatus(it, 'follow')"
                >跟进</button>
                <button
                  class="f-act act-ignore"
                  :class="{ on: it.status === 'ignore' }"
                  title="取消忽略"
                  @click="toggleStatus(it, 'ignore')"
                >忽略</button>
              </div>
            </div>
          </template>
        </div>
      </section>

      <!-- 插件 Dock：常用能力直达（主屏的「应用」）；图钉可固定/取消 -->
      <section class="sec">
        <div class="sec-title">常用</div>
        <div class="dock">
          <div v-for="p in dock" :key="p.id" class="dock-cell">
            <button class="dock-item" @click="launchPlugin(p)">
              <span class="dock-icon">{{ pluginIcon(p.name) }}</span>
              <span class="dock-name">{{ p.name }}</span>
            </button>
            <button
              class="dock-pin"
              :class="{ on: p.pinned }"
              :title="p.pinned ? '取消固定' : '固定到常用'"
              @click="togglePin(p)"
            >📌</button>
          </div>
          <div v-if="!dock.length" class="f-empty">没有发现插件</div>
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
  flex-wrap: wrap;
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
.sec-inbox {
  padding: var(--yb-space-2);
  border: 1px solid var(--yb-surface-border);
  border-radius: 16px;
  background: color-mix(in srgb, var(--yb-surface-solid) 88%, transparent);
  box-shadow: var(--yb-shadow-soft);
}
.inbox-title {
  color: var(--yb-text);
  font-size: var(--yb-fs-md);
}
.inbox-zone + .inbox-zone {
  margin-top: var(--yb-space-3);
  padding-top: var(--yb-space-3);
  border-top: 1px solid var(--yb-surface-border);
}
.zone-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--yb-space-2);
  padding: 0 var(--yb-space-2) var(--yb-space-2);
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-sm);
  font-weight: 600;
}
.task-row {
  display: flex;
  align-items: center;
  gap: var(--yb-space-2);
  width: 100%;
  padding: var(--yb-space-2) var(--yb-space-3);
  margin-bottom: var(--yb-space-2);
  border: 1px solid var(--yb-surface-border);
  border-radius: 12px;
  background: var(--yb-surface-solid);
  color: var(--yb-text);
  cursor: pointer;
  font: inherit;
  text-align: left;
}
.task-row:hover {
  border-color: var(--yb-accent);
}
.task-row.unread {
  border-color: var(--yb-accent);
  background: var(--yb-accent-soft);
}
.task-main {
  min-width: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.task-main strong,
.task-main span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.task-main span {
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-sm);
}
.task-time,
.task-go {
  flex-shrink: 0;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-sm);
}
.task-dot {
  width: 8px;
  height: 8px;
  flex-shrink: 0;
  border-radius: 50%;
}
.task-dot.running {
  background: var(--yb-accent);
  box-shadow: 0 0 0 4px var(--yb-accent-soft);
}
.task-status {
  flex-shrink: 0;
  padding: 2px 7px;
  border-radius: var(--yb-radius-lg);
  background: var(--yb-btn-neutral);
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-sm);
}
.status-done {
  color: var(--yb-state-success);
}
.status-failed {
  color: var(--yb-danger);
}
/* 待批准卡片：警示淡黄底（与对话/动态区分开），右侧批/拒按钮 */
.sec-approvals .zone-title {
  color: #b7791f;
}
/* 收件箱头：标题 + 顶部一键全批/全拒（仅 N>1 出现） */
.inbox-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--yb-space-2);
}
.batch-btns {
  display: flex;
  gap: var(--yb-space-2);
}
.batch-yes,
.batch-no {
  padding: 3px var(--yb-space-2);
  border-radius: var(--yb-radius-lg);
  font-size: var(--yb-fs-sm);
  font-family: inherit;
  cursor: pointer;
  transition: all 0.15s ease;
}
.batch-yes {
  border: none;
  background: var(--yb-accent);
  color: #fff;
}
.batch-yes:hover:not(:disabled) {
  background: var(--yb-accent-deep);
}
.batch-no {
  border: 1px solid var(--yb-surface-border);
  background: transparent;
  color: var(--yb-text-dim);
}
.batch-no:hover:not(:disabled) {
  color: var(--yb-danger);
  border-color: var(--yb-danger);
}
.batch-yes:disabled,
.batch-no:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.a-card {
  display: flex;
  align-items: center;
  gap: var(--yb-space-3);
  padding: var(--yb-space-2) var(--yb-space-3);
  margin-bottom: var(--yb-space-2);
  border: 1px solid rgba(183, 121, 31, 0.35);
  border-radius: 14px;
  background: rgba(255, 193, 99, 0.14);
  transition: border-color 0.15s ease, background 0.15s ease;
}
/* 选中态：accent 描边强调 */
.a-card.selected {
  border-color: var(--yb-accent);
  background: rgba(255, 193, 99, 0.22);
}
/* 多选 checkbox（仅 N>1 出现） */
.a-check {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  cursor: pointer;
}
.a-check input {
  width: 16px;
  height: 16px;
  cursor: pointer;
}
.a-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.a-label {
  font-size: var(--yb-fs-md);
  font-weight: 600;
  color: var(--yb-text);
}
.a-desc {
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-dim);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.a-btns {
  flex-shrink: 0;
  display: flex;
  gap: var(--yb-space-2);
}
/* remember 勾选框：会话内免询问，每条独立 */
.a-remember {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-dim);
  cursor: pointer;
  user-select: none;
}
.a-remember input {
  width: 14px;
  height: 14px;
  cursor: pointer;
}
.a-remember:hover {
  color: var(--yb-text);
}
.a-yes,
.a-no {
  padding: 4px var(--yb-space-3);
  border-radius: var(--yb-radius-lg);
  font-size: var(--yb-fs-md);
  font-family: inherit;
  cursor: pointer;
  transition: all 0.15s ease;
}
.a-yes {
  border: none;
  background: var(--yb-accent);
  color: #fff;
}
.a-yes:hover {
  background: var(--yb-accent-deep);
}
.a-no {
  border: 1px solid var(--yb-surface-border);
  background: transparent;
  color: var(--yb-text-dim);
}
.a-no:hover {
  color: var(--yb-danger);
  border-color: var(--yb-danger);
}
.sec-widgets {
  margin-top: var(--yb-space-2);
}
/* widget 卡片区：横排自适应卡片，固定高度一瞥（内部滚动；交互去全面板） */
.w-row {
  display: flex;
  flex-wrap: wrap;
  gap: var(--yb-space-3);
  padding: 0 var(--yb-space-2);
}
.w-card {
  flex: 1 1 230px;
  min-width: 210px;
  max-width: 380px;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--yb-surface-border);
  border-radius: 14px;
  background: var(--yb-surface-solid);
  box-shadow: var(--yb-shadow-soft);
  overflow: hidden;
}
.w-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--yb-space-2) var(--yb-space-3);
  border: none;
  background: transparent;
  font-family: inherit;
  text-align: left;
}
.w-head.link {
  cursor: pointer;
}
.w-head.link:hover .w-title,
.w-head.link:hover .w-go {
  color: var(--yb-accent-deep);
}
.w-title {
  font-size: var(--yb-fs-sm);
  font-weight: 600;
  color: var(--yb-text-dim);
  letter-spacing: 0.04em;
}
.w-go {
  color: var(--yb-text-dim);
  font-size: 14px;
}
.w-body {
  height: 148px;
  padding: 0 var(--yb-space-2) var(--yb-space-2);
  min-height: 0;
}
.sec-title {
  padding: 0 var(--yb-space-2) var(--yb-space-2);
  font-size: var(--yb-fs-sm);
  font-weight: 600;
  color: var(--yb-text-dim);
  letter-spacing: 0.04em;
}
/* Feed 头：标题 + 全部已读按钮（仅未读>0 时出现） */
.feed-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.mark-all {
  border: none;
  background: transparent;
  color: var(--yb-accent-deep);
  font-size: var(--yb-fs-sm);
  font-family: inherit;
  padding: 2px var(--yb-space-2);
  cursor: pointer;
  transition: opacity 0.15s ease;
}
.mark-all:hover {
  opacity: 0.7;
  text-decoration: underline;
}

/* Feed 行：图标 + 文本（两行截断）+ 相对时间；未读项 accent 高亮，点击带上下文进对话 */
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
/* 未读高亮：accent 边框 + 柔和高亮底（一眼可见待处理） */
.f-row.unread {
  border-color: var(--yb-accent);
  background: var(--yb-accent-soft);
}
.f-row.unread .f-text {
  font-weight: 600;
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

/* 处置态筛选 chips（C 子项目）：药丸状，active 态 accent 实心 */
.filter-bar {
  display: flex;
  gap: var(--yb-space-2);
  padding: var(--yb-space-2);
  margin-top: var(--yb-space-2);
}
.chip {
  padding: 4px var(--yb-space-3);
  border: 1px solid var(--yb-surface-border);
  border-radius: 999px;
  background: var(--yb-surface-solid);
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-sm);
  font-family: inherit;
  cursor: pointer;
  transition: all 0.15s ease;
}
.chip:hover {
  color: var(--yb-text);
  border-color: var(--yb-accent);
}
.chip.active {
  background: var(--yb-accent);
  border-color: var(--yb-accent);
  color: #fff;
}

/* 行内跟进/忽略按钮（f-actions）：默认低调轮廓，on 态高亮 */
.f-actions {
  flex-shrink: 0;
  display: flex;
  gap: 4px;
  margin-left: var(--yb-space-2);
}
.f-act {
  padding: 3px var(--yb-space-2);
  border: 1px solid var(--yb-surface-border);
  border-radius: var(--yb-radius-sm);
  background: transparent;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-sm);
  font-family: inherit;
  cursor: pointer;
  transition: all 0.15s ease;
}
.f-act:hover {
  color: var(--yb-text);
  border-color: var(--yb-accent);
}
/* 跟进 on：accent 着色 */
.f-act.on {
  background: var(--yb-accent-soft);
  border-color: var(--yb-accent);
  color: var(--yb-accent-deep);
}
/* 忽略 on：中性灰（与跟进区分，弱化被忽略项） */
.f-act.act-ignore.on {
  background: var(--yb-btn-neutral);
  border-color: var(--yb-text-dim);
  color: var(--yb-text-dim);
}

/* 处置态行级轻标识：跟进描边、忽略半透 */
.task-row.status-follow,
.f-row.status-follow {
  border-color: var(--yb-accent);
}
.task-row.status-ignore,
.f-row.status-ignore {
  opacity: 0.6;
}

/* tier 轻着色（§4.5 三分级）：小色点贴行首，Review 蓝 / Notify 灰 */
.tier-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
  align-self: center;
}
.tier-review .tier-dot {
  background: var(--yb-accent);
}
.tier-notify .tier-dot {
  background: var(--yb-text-dim);
  opacity: 0.55;
}

/* 忽略折叠（C 子项目）：折叠条虚框低调，展开后与普通行同构但半透 */
.ignored-fold {
  margin-top: var(--yb-space-2);
}
.fold-toggle {
  width: 100%;
  padding: var(--yb-space-2) var(--yb-space-3);
  border: 1px dashed var(--yb-surface-border);
  border-radius: 12px;
  background: transparent;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-sm);
  font-family: inherit;
  cursor: pointer;
  transition: all 0.15s ease;
}
.fold-toggle:hover {
  color: var(--yb-text);
  border-color: var(--yb-accent);
}
.fold-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--yb-space-2) var(--yb-space-3) var(--yb-space-1);
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-sm);
  font-weight: 600;
}
.fold-collapse {
  border: none;
  background: transparent;
  color: var(--yb-accent-deep);
  font-size: var(--yb-fs-sm);
  font-family: inherit;
  cursor: pointer;
  padding: 2px var(--yb-space-2);
}
.fold-collapse:hover {
  text-decoration: underline;
}
.ignored-row {
  opacity: 0.65;
}

/* Dock：首字圆形图标 + 名称 + 角落图钉按钮，与 iOS Dock 同语义 */
.dock {
  display: flex;
  flex-wrap: wrap;
  gap: var(--yb-space-3);
  padding: var(--yb-space-2);
}
.dock-cell {
  position: relative;
  width: 64px;
}
.dock-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  width: 100%;
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
/* 图钉：默认隐藏，hover 显半透、已固定常驻；点即固定/取消 */
.dock-pin {
  position: absolute;
  top: -4px;
  right: 6px;
  width: 18px;
  height: 18px;
  display: grid;
  place-items: center;
  border: 1px solid var(--yb-surface-border);
  border-radius: 50%;
  background: var(--yb-surface-solid);
  box-shadow: var(--yb-shadow-soft);
  font-size: 9px;
  line-height: 1;
  cursor: pointer;
  padding: 0;
  opacity: 0;
  transition: opacity 0.15s ease, transform 0.15s ease, background 0.15s ease;
}
.dock-cell:hover .dock-pin {
  opacity: 0.65;
}
.dock-pin:hover {
  opacity: 1;
  transform: scale(1.15);
}
.dock-pin.on {
  opacity: 1;
  background: var(--yb-accent);
  border-color: var(--yb-accent);
}

.bar {
  padding-top: var(--yb-space-2);
}
</style>
