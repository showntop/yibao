<script setup lang="ts">
// 大窗「主屏」页：macOS 驾驶舱布局（对齐通知中心 + 邮件的原生语言）。
//
// 信息架构（重构要点）：
//   1. 双列非对称——左主列一条「统一时间线」，右副列 320px 放需要你动手的事。
//   2. 时间线合并了原「已完成任务」与「Feed 动态」：两者语义都是「发生过的事」，
//      原先拆两个区块各带一套跟进/忽略，是同一交互实现了两遍。macOS 的做法不是
//      按类型拆区，而是按时间分组（sticky 今天/昨天/更早）+ 行首图标区分类型。
//   3. 待批准是唯一「必须你决定」的事，独占右列顶部，也是全页唯一允许用琥珀强调处。
//   4. Dock 移出主屏 → 归到插件页（那才是它的家），主屏不再有第二套插件入口。
//   5. 筛选从独立一行 chips 改为时间线头部的分段控件（Segmented Control）。
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import InputBar from "./InputBar.vue";
import SchemaPanel from "./SchemaPanel.vue";
import YbIcon from "./YbIcon.vue";
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
  type FeedItem,
  type FeedStats,
  type RunningTask,
  type PendingConfirm,
  type WidgetPayload,
  canRememberSkill,
} from "../lib/brain";

// chat：提交/点动态 → 切对话页；draft 非空时带给对话页预填
// unread：未读动态数同步给父（Home.vue sidebar 徽标用）
const emit = defineEmits<{ chat: [draft?: string]; unread: [n: number] }>();

// ---- 问候条 ----
const stats = ref<FeedStats>({ pending_reminders: 0, running_tasks: 0, done_24h: 0, unread: 0, ignored: 0 });

// 问候只说时段与人称，把「盯着的事」交给下方 stat 数字条——
// 原先把 2 句叙事拼进标题，导致主角句子过长且每次刷新跳动。
const greeting = computed(() => {
  const h = new Date().getHours();
  return h < 6 ? "夜深了" : h < 11 ? "早上好" : h < 14 ? "中午好" : h < 18 ? "下午好" : "晚上好";
});

const dateLine = computed(() => {
  const d = new Date();
  const week = "日一二三四五六"[d.getDay()];
  return `${d.getMonth() + 1} 月 ${d.getDate()} 日 星期${week}`;
});

// 概览数字条：把原问候里的叙事拆成可扫读的三个数（0 的项不显，全 0 显「暂时清净」）
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

// ---- 时间线（原「已完成」+「动态」合并）----
const items = ref<FeedItem[]>([]);
const runningTasks = ref<RunningTask[]>([]);
const loaded = ref(false);

// 筛选：全部 / 未读 / 跟进 / 忽略（macOS 分段控件）
type Seg = "all" | "unread" | "follow" | "ignore";
const seg = ref<Seg>("all");
const SEGS: { id: Seg; label: string }[] = [
  { id: "all", label: "全部" },
  { id: "unread", label: "未读" },
  { id: "follow", label: "跟进" },
  { id: "ignore", label: "已忽略" },
];

/** 全量条目（任务 + 提醒 + 事件），按时间倒序——后端已排序，这里只做筛选。 */
const timelineAll = computed(() =>
  [...items.value].sort((a, b) => b.ts - a.ts),
);

const timeline = computed(() => {
  const l = timelineAll.value;
  if (seg.value === "unread") return l.filter((it) => it.read === 0);
  if (seg.value === "follow") return l.filter((it) => it.status === "follow");
  if (seg.value === "ignore") return l.filter((it) => it.status === "ignore");
  // 全部：忽略项不混在主流里（要看去「已忽略」分段）
  return l.filter((it) => it.status !== "ignore");
});

/** 按「今天 / 昨天 / 更早」分组（macOS 通知中心语言：sticky 日期头 + 组内条目）。 */
const groups = computed(() => {
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000;
  const startOfYesterday = startOfToday - 86400;
  const buckets: { key: string; label: string; items: FeedItem[] }[] = [
    { key: "today", label: "今天", items: [] },
    { key: "yesterday", label: "昨天", items: [] },
    { key: "earlier", label: "更早", items: [] },
  ];
  for (const it of timeline.value) {
    if (it.ts >= startOfToday) buckets[0].items.push(it);
    else if (it.ts >= startOfYesterday) buckets[1].items.push(it);
    else buckets[2].items.push(it);
  }
  return buckets.filter((b) => b.items.length > 0);
});

const emptyText = computed(() => {
  if (!loaded.value) return "";
  if (seg.value === "unread") return "所有动态都看过了";
  if (seg.value === "follow") return "把值得回头看的动态标上「跟进」，它们会聚到这里";
  if (seg.value === "ignore") return "被忽略的动态会留在这里，随时可以恢复";
  return "任务结果、提醒和主动消息都会出现在这里";
});

const emptyTitle = computed(() => {
  if (seg.value === "unread") return "没有未读";
  if (seg.value === "follow") return "没有跟进中的事";
  if (seg.value === "ignore") return "没有忽略的动态";
  return "这里还很清净";
});

// 空态起步建议：新用户不知道能让译宝干什么，给三个具体入口
const STARTERS = ["帮我记一下今天的想法", "提醒我 30 分钟后休息", "看看我最近在忙什么"];

const unreadCount = computed(() => timelineAll.value.filter((it) => it.read === 0).length);

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

// ---- 待批准队列（高风险操作排队一键批/拒）----
// 收件箱：多选 + 顶部一键批/拒 + 每条独立 remember。批量为主，单条快批为辅。
const approvals = ref<PendingConfirm[]>([]);
// 多选集合：N>1 时默认全选（鼓励批量）；N<=1 留空走单条快批按钮。
const selectedApprovals = ref<Set<string>>(new Set());
// 每条独立 remember（默认 false；勾选后该技能会话内不再询问——大脑侧会话级记忆）
const rememberMap = ref<Record<string, boolean>>({});

const selectedCount = computed(() => selectedApprovals.value.size);

/** brain.ts sendConfirmBatch 内部乐观出队后校正默认选择：
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
  const approval = approvals.value.find((item) => item.id === id);
  return approval && canRememberSkill(approval.skill) ? rememberMap.value[id] ?? false : false;
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

/** 全部批准/拒绝：对选中的项调 sendConfirmBatch（按各条 remember 勾选）。 */
async function batchDecide(approved: boolean) {
  const targets = approvals.value.filter((p) => selectedApprovals.value.has(p.id));
  if (!targets.length) return;
  const list = targets.map((p) => ({
    id: p.id,
    approved,
    remember: rememberOf(p.id),
  }));
  // 局部快照：共享队列会回滚；此处兜底避免订阅链异常时卡片消失。
  const snapshot = targets.slice();
  try {
    await sendConfirmBatch(list);
    targets.forEach((p) => delete rememberMap.value[p.id]);
  } catch {
    const existing = new Set(approvals.value.map((p) => p.id));
    const restore = snapshot.filter((p) => !existing.has(p.id));
    if (restore.length) approvals.value = [...approvals.value, ...restore];
  }
}

// ---- 主屏 widget：插件一瞥卡（schema 协议的 widget 类型，展示型；交互去全面板）----
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

/** widget schema：后端给 unknown，非对象时给 null 让 SchemaPanel 走未知降级。 */
function widgetSchema(w: WidgetPayload): Record<string, any> | null {
  return w.schema && typeof w.schema === "object" ? (w.schema as Record<string, any>) : null;
}

/** 右副列是否有内容：全空则整列不渲染（否则空着占 320px，页面重心偏左）。 */
const hasSideContent = computed(
  () => approvals.value.length > 0 || runningTasks.value.length > 0 || widgets.value.length > 0,
);

/** 相对时间：组内只显示时刻（日期已由分组头承担），跨日项显示月日。 */
function itemTime(ts: number): string {
  const d = new Date(ts * 1000);
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return "刚刚";
  if (diff < 172800) {
    return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  }
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

/** 动态类型 → YbIcon 图标名。任务按成败分图标：成功 check、失败 x。 */
function kindIcon(it: FeedItem): "clock" | "check" | "x" | "chat" {
  if (it.kind === "reminder") return "clock";
  if (it.kind === "task") return taskStatus(it) === "done" ? "check" : "x";
  return "chat";
}

function elapsedSince(ts: number): string {
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (seconds < 60) return "刚开始";
  if (seconds < 3600) return `已运行 ${Math.floor(seconds / 60)} 分钟`;
  return `已运行 ${Math.floor(seconds / 3600)} 小时`;
}

function taskStatus(it: FeedItem): string {
  return String(it.meta?.status ?? "done");
}

function taskStatusLabel(it: FeedItem): string {
  return ({ done: "完成", failed: "失败", stopped: "已停止", interrupted: "已中断" } as Record<string, string>)[
    taskStatus(it)
  ] ?? "已结束";
}

function openTasks() {
  void panelAction("agents.task_list", {}, undefined, "panel:agents").catch(() => {});
}

/** 点动态 → 乐观置已读 + 带自包含上下文草稿切对话页（大脑看不到 Feed，上下文随草稿走）。 */
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

// ---- 处置态：跟进/忽略，乐观改 it.status + 失败回滚 ----

/** 设置处置态：与 read 正交——跟进/忽略不改已读态。 */
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

/** tier 轻着色 class（Review 蓝 / Notify 灰，按 kind 推导）。 */
function tierClass(it: FeedItem): string {
  return `tier-${feedTierOf(it.kind).toLowerCase()}`;
}

// ---- 常驻输入条：提交后切对话页看回复 ----
function submit(text: string) {
  void runInput(text, "pet").catch(() => {});
  emit("chat");
}

// ---- 订阅：新动态实时刷新 Feed 与 widget；待批队列订阅 ----
let unFeed: (() => void) | null = null;
let unWidgets: (() => void) | null = null;
let unEvent: (() => void) | null = null;
let unApprovals: (() => void) | null = null;
let refetchTimer: ReturnType<typeof setTimeout> | null = null;

function scheduleFeedRefresh() {
  if (refetchTimer !== null) clearTimeout(refetchTimer);
  refetchTimer = setTimeout(() => {
    void fetchFeed().catch(() => {});
    void fetchWidgets().catch(() => {});
  }, 800);
}

onMounted(async () => {
  await Promise.all([reload(), reloadWidgets()]);
  unApprovals = onPendingConfirms((l) => (approvals.value = l));
  unFeed = await onFeed((r) => {
    items.value = r.items;
    stats.value = r.stats;
    runningTasks.value = r.running_tasks ?? [];
  });
  unWidgets = await onWidgets((r) => {
    widgets.value = r.widgets;
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
  if (refetchTimer !== null) clearTimeout(refetchTimer);
});
</script>

<template>
  <div class="feed-page">
    <!-- 页头：留出红绿灯安全区，问候为唯一主角 + 概览数字条 -->
    <header class="page-head" data-tauri-drag-region>
      <div class="head-line" data-tauri-drag-region>
        <h1 class="greet" data-tauri-drag-region>{{ greeting }}</h1>
        <span class="date yb-num" data-tauri-drag-region>{{ dateLine }}</span>
      </div>
      <div v-if="overview.length" class="overview">
        <span v-for="o in overview" :key="o.key" class="ov-item">
          <strong class="yb-num">{{ o.n }}</strong>{{ o.label }}
        </span>
      </div>
      <div v-else-if="loaded" class="overview quiet">暂时清净，随时叫我</div>
    </header>

    <!-- 主体：左时间线 + 右侧栏（待批准 / 进行中 / 一瞥卡） -->
    <div class="body">
      <!-- ===== 左主列：统一时间线 ===== -->
      <section class="timeline-col">
        <div class="tl-head">
          <div class="segmented">
            <button
              v-for="s in SEGS"
              :key="s.id"
              class="seg"
              :class="{ on: seg === s.id }"
              @click="seg = s.id"
            >
              {{ s.label }}
              <span v-if="s.id === 'unread' && unreadCount > 0" class="seg-n yb-num">{{ unreadCount }}</span>
            </button>
          </div>
          <button v-if="unreadCount > 0" class="link-btn" @click="markAllRead">全部标为已读</button>
        </div>

        <div class="tl-scroll">
          <template v-if="groups.length">
            <div v-for="g in groups" :key="g.key" class="tl-group">
              <div class="tl-date">{{ g.label }}</div>
              <button
                v-for="it in g.items"
                :key="it.id"
                class="tl-row"
                :class="[tierClass(it), { unread: it.read === 0, [`st-${it.status}`]: it.status !== 'none' }]"
                @click="openInChat(it)"
              >
                <span class="tl-ic" :class="`ic-${kindIcon(it)}`">
                  <YbIcon :name="kindIcon(it)" :size="13" />
                </span>
                <span class="tl-main">
                  <span class="tl-text">{{ it.text }}</span>
                  <span v-if="it.kind === 'task'" class="tl-tag" :class="`tag-${taskStatus(it)}`">
                    {{ taskStatusLabel(it) }}
                  </span>
                </span>
                <span class="tl-time yb-num">{{ itemTime(it.ts) }}</span>
                <!-- 行内操作：macOS 惯例——hover 才浮现，不常驻占位 -->
                <span class="tl-acts" @click.stop>
                  <button
                    class="tl-act"
                    :class="{ on: it.status === 'follow' }"
                    :title="it.status === 'follow' ? '取消跟进' : '跟进'"
                    @click="toggleStatus(it, 'follow')"
                  >
                    <YbIcon name="pin" :size="12" />
                  </button>
                  <button
                    class="tl-act"
                    :class="{ on: it.status === 'ignore' }"
                    :title="it.status === 'ignore' ? '取消忽略' : '忽略'"
                    @click="toggleStatus(it, 'ignore')"
                  >
                    <YbIcon name="x" :size="12" />
                  </button>
                </span>
              </button>
            </div>
          </template>
          <div v-else-if="loaded" class="tl-empty">
            <YbIcon name="inbox" :size="30" :stroke="1.3" />
            <p class="te-title">{{ emptyTitle }}</p>
            <p class="te-hint">{{ emptyText }}</p>
            <!-- 全部分段的首次空态：给几个起步建议（macOS 空态惯例：不只说「没有」，还给出路） -->
            <div v-if="seg === 'all'" class="te-chips">
              <button v-for="c in STARTERS" :key="c" class="te-chip" @click="submit(c)">{{ c }}</button>
            </div>
          </div>
        </div>
      </section>

      <!-- ===== 右副列：需要你动手的事 =====
           全空则整列不渲染——空着占 320px 会让时间线偏左、右边一片死白。 -->
      <aside v-if="hasSideContent" class="side-col">
        <div class="side-scroll">
          <!-- 待批准：全页唯一允许琥珀强调处 -->
          <section v-if="approvals.length" class="panel panel-pending">
            <div class="panel-head">
              <span class="panel-title">
                <YbIcon name="lock" :size="12" />待批准
                <span class="count yb-num">{{ approvals.length }}</span>
              </span>
            </div>
            <div class="panel-body">
              <div
                v-for="p in approvals"
                :key="p.id"
                class="ap-card"
                :class="{ selected: approvals.length > 1 && isSelected(p.id) }"
              >
                <div class="ap-top">
                  <label v-if="approvals.length > 1" class="ap-check" title="选中后可一键批量">
                    <input type="checkbox" :checked="isSelected(p.id)" @change="onToggleSelect(p.id, $event)" />
                  </label>
                  <div class="ap-info">
                    <strong class="ap-label">{{ p.label || p.skill }}</strong>
                    <span class="ap-desc">{{ p.desc || p.skill }}</span>
                  </div>
                </div>
                <label v-if="canRememberSkill(p.skill)" class="ap-remember" title="勾选后该技能在本会话内不再询问">
                  <input type="checkbox" :checked="rememberOf(p.id)" @change="onToggleRemember(p.id, $event)" />
                  <span>本会话不再询问</span>
                </label>
                <div class="ap-btns">
                  <button class="btn-ghost" @click="decideApproval(p, false)">拒绝</button>
                  <button class="btn-primary" @click="decideApproval(p, true)">批准</button>
                </div>
              </div>
              <div v-if="approvals.length > 1" class="ap-batch">
                <button class="btn-ghost" :disabled="selectedCount === 0" @click="batchDecide(false)">
                  拒绝选中{{ selectedCount ? ` (${selectedCount})` : "" }}
                </button>
                <button class="btn-primary" :disabled="selectedCount === 0" @click="batchDecide(true)">
                  批准选中{{ selectedCount ? ` (${selectedCount})` : "" }}
                </button>
              </div>
            </div>
          </section>

          <!-- 进行中 -->
          <section v-if="runningTasks.length" class="panel">
            <div class="panel-head">
              <span class="panel-title">
                <YbIcon name="spinner" :size="12" spin />进行中
                <span class="count yb-num">{{ runningTasks.length }}</span>
              </span>
              <button class="link-btn" @click="openTasks">查看</button>
            </div>
            <div class="panel-body">
              <button v-for="t in runningTasks" :key="t.id" class="run-row" @click="openTasks">
                <span class="run-dot" />
                <span class="run-main">
                  <strong>{{ t.label }}</strong>
                  <span>{{ t.prompt }}</span>
                </span>
                <span class="run-time">{{ elapsedSince(t.created_at) }}</span>
              </button>
            </div>
          </section>

          <!-- 插件一瞥卡 -->
          <section v-for="w in widgets" :key="w.panel" class="panel">
            <div class="panel-head">
              <span class="panel-title">{{ w.title }}</span>
              <button v-if="w.open" class="link-btn" @click="openWidget(w)">打开</button>
            </div>
            <div class="panel-body w-body">
              <SchemaPanel :panel="w.panel" :schema="widgetSchema(w)" :data="widgetData(w)" />
            </div>
          </section>
        </div>
      </aside>
    </div>

    <!-- 常驻输入条：提交后切对话页看回复 -->
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
  background: var(--yb-content-bg);
}

/* ---- 页头 ---- */
.page-head {
  flex-shrink: 0;
  padding: var(--yb-titlebar-h) var(--yb-space-5) var(--yb-space-4);
  user-select: none;
}
.head-line {
  display: flex;
  align-items: baseline;
  gap: var(--yb-space-3);
  flex-wrap: wrap;
}
/* 问候是全页唯一的展示级字号（macOS 大标题语义） */
.greet {
  margin: 0;
  font-size: 26px;
  font-weight: var(--yb-fw-bold);
  letter-spacing: -0.01em;
  line-height: var(--yb-lh-tight);
  color: var(--yb-text-strong);
}
.date {
  font-size: var(--yb-fs-md);
  color: var(--yb-text-dim);
}
/* 概览数字条：可扫读的三个数，替代原先拼长句的叙事问候 */
.overview {
  display: flex;
  gap: var(--yb-space-4);
  margin-top: var(--yb-space-2);
  font-size: var(--yb-fs-md);
  color: var(--yb-text-dim);
}
.ov-item strong {
  margin-right: 4px;
  font-size: var(--yb-fs-xl);
  font-weight: var(--yb-fw-bold);
  color: var(--yb-text);
}
.overview.quiet {
  color: var(--yb-text-faint);
}

/* ---- 主体双列 ---- */
.body {
  flex: 1;
  min-height: 0;
  display: flex;
  gap: var(--yb-space-5);
  padding: 0 var(--yb-space-5);
}
.timeline-col {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  /* 无右列时限宽：宽窗下单行拉到 1000px 会难扫读（macOS 列表同样限宽） */
  max-width: 760px;
}
.side-col {
  width: 320px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
}
/* 有右列时时间线不限宽（右列已占位） */
.body:has(.side-col) .timeline-col {
  max-width: none;
}
/* 窄窗（< 940）：右列收起，时间线独占——避免双列各自过窄 */
@media (max-width: 940px) {
  .side-col {
    display: none;
  }
}

/* ---- 时间线头：分段控件 + 全部已读 ---- */
.tl-head {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--yb-space-3);
  padding-bottom: var(--yb-space-3);
}
/* macOS Segmented Control：凹槽底 + 白滑块 */
.segmented {
  display: inline-flex;
  padding: 2px;
  border-radius: var(--yb-radius-xs);
  background: var(--yb-segment-track);
}
.seg {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px var(--yb-space-3);
  border: none;
  border-radius: var(--yb-radius-xs);
  background: transparent;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-md);
  font-family: inherit;
  cursor: pointer;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.seg:hover {
  color: var(--yb-text);
}
.seg.on {
  background: var(--yb-segment-thumb);
  color: var(--yb-text);
  font-weight: var(--yb-fw-medium);
  box-shadow: var(--yb-shadow-1);
}
.seg-n {
  font-size: var(--yb-fs-xs);
  color: var(--yb-accent-deep);
}
/* 文字按钮（macOS link 语义）：无边框无底，hover 下划线 */
.link-btn {
  border: none;
  background: transparent;
  color: var(--yb-accent-deep);
  font-size: var(--yb-fs-md);
  font-family: inherit;
  padding: 2px 0;
  cursor: pointer;
  white-space: nowrap;
}
.link-btn:hover:not(:disabled) {
  text-decoration: underline;
}

/* ---- 时间线滚动区 ---- */
.tl-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  scrollbar-width: thin;
  padding-bottom: var(--yb-space-3);
}
.tl-scroll::-webkit-scrollbar {
  width: 7px;
}
.tl-scroll::-webkit-scrollbar-thumb {
  background: var(--yb-border-strong);
  border-radius: var(--yb-radius-pill);
}
.tl-scroll::-webkit-scrollbar-track {
  background: transparent;
}
/* sticky 日期头：macOS 通知中心语言，滚动时压住下方内容 */
.tl-date {
  position: sticky;
  top: 0;
  z-index: 1;
  padding: var(--yb-space-2) var(--yb-space-1);
  background: var(--yb-sticky-bg);
  -webkit-backdrop-filter: var(--yb-sticky-blur);
  backdrop-filter: var(--yb-sticky-blur);
  font-size: var(--yb-fs-xs);
  font-weight: var(--yb-fw-bold);
  color: var(--yb-text-dim);
  letter-spacing: 0.04em;
}

/* 时间线行：Finder 列表语义——无卡片描边，靠 hover 底色与行间 hairline */
.tl-row {
  position: relative;
  display: flex;
  align-items: center;
  gap: var(--yb-space-3);
  width: 100%;
  padding: var(--yb-space-3) var(--yb-space-2);
  border: none;
  border-bottom: 1px solid var(--yb-card-row-line);
  border-radius: 0;
  background: transparent;
  color: var(--yb-text);
  font-family: inherit;
  font-size: var(--yb-fs-lg);
  text-align: left;
  cursor: pointer;
  transition: background var(--yb-dur-fast) var(--yb-ease-out);
}
.tl-row:hover {
  background: var(--yb-row-hover);
}
/* 未读：左侧 accent 竖条（macOS 邮件未读标记语义），不整行染色 */
.tl-row.unread::before {
  content: "";
  position: absolute;
  left: 0;
  top: 50%;
  transform: translateY(-50%);
  width: 3px;
  height: 18px;
  border-radius: var(--yb-radius-pill);
  background: var(--yb-accent);
}
.tl-row.unread .tl-text {
  font-weight: var(--yb-fw-medium);
}
/* 跟进：行首图标转 accent；忽略：整行降透明 */
.tl-row.st-follow .tl-ic {
  color: var(--yb-accent);
}
.tl-row.st-ignore {
  opacity: 0.5;
}

.tl-ic {
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: var(--yb-surface-3);
  color: var(--yb-text-faint);
}
/* 类型着色：提醒=accent、成功=绿、失败=红、记忆=琥珀（弱） */
.tl-ic.ic-clock {
  color: var(--yb-accent);
}
.tl-ic.ic-check {
  color: var(--yb-intent-ok);
}
.tl-ic.ic-x {
  color: var(--yb-danger);
}
.tl-main {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: var(--yb-space-2);
}
.tl-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  line-height: var(--yb-lh-ui);
}
/* 任务结果小标签：只在失败类着色，成功态不喧哗 */
.tl-tag {
  flex-shrink: 0;
  padding: 1px 6px;
  border-radius: var(--yb-radius-pill);
  background: var(--yb-btn-neutral);
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-xs);
}
.tl-tag.tag-failed,
.tl-tag.tag-interrupted {
  background: var(--yb-danger-soft);
  color: var(--yb-danger);
}
.tl-time {
  flex-shrink: 0;
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-faint);
}
/* 行内操作：hover / focus 才浮现（macOS 惯例，不常驻抢注意力） */
.tl-acts {
  flex-shrink: 0;
  display: flex;
  gap: 2px;
  opacity: 0;
  transition: opacity var(--yb-dur-fast) var(--yb-ease-out);
}
.tl-row:hover .tl-acts,
.tl-row:focus-within .tl-acts {
  opacity: 1;
}
/* 已置态的按钮常驻可见（否则用户看不出这行被跟进/忽略了） */
.tl-acts:has(.on) {
  opacity: 1;
}
.tl-act {
  width: 22px;
  height: 22px;
  display: grid;
  place-items: center;
  border: none;
  border-radius: var(--yb-radius-xs);
  background: transparent;
  color: var(--yb-text-faint);
  cursor: pointer;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.tl-act:hover {
  background: var(--yb-btn-neutral-hover);
  color: var(--yb-text);
}
.tl-act.on {
  background: var(--yb-accent-soft);
  color: var(--yb-accent-deep);
}

.tl-empty {
  height: 100%;
  min-height: 200px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--yb-space-1);
  color: var(--yb-text-faint);
}
.te-title {
  margin: var(--yb-space-2) 0 0;
  font-size: var(--yb-fs-xl);
  font-weight: var(--yb-fw-medium);
  color: var(--yb-text-dim);
}
.te-hint {
  margin: 0;
  max-width: 300px;
  text-align: center;
  font-size: var(--yb-fs-md);
  line-height: var(--yb-lh-base);
}
/* 起步建议：点一下就发给译宝（比空说明有用） */
.te-chips {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: var(--yb-space-2);
  margin-top: var(--yb-space-4);
}
.te-chip {
  padding: 4px var(--yb-space-3);
  border: 1px solid var(--yb-border-strong);
  border-radius: var(--yb-radius-pill);
  background: var(--yb-card-bg);
  color: var(--yb-accent-deep);
  font-size: var(--yb-fs-md);
  font-family: inherit;
  cursor: pointer;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.te-chip:hover {
  border-color: var(--yb-accent);
  background: var(--yb-accent-soft);
}

/* ---- 右副列：分组卡片（系统设置 Ventura+ 语言）---- */
.side-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  scrollbar-width: thin;
  display: flex;
  flex-direction: column;
  gap: var(--yb-space-3);
  padding-bottom: var(--yb-space-3);
}
.side-scroll::-webkit-scrollbar {
  width: 7px;
}
.side-scroll::-webkit-scrollbar-thumb {
  background: var(--yb-border-strong);
  border-radius: var(--yb-radius-pill);
}
.panel {
  border: 1px solid var(--yb-card-border);
  border-radius: var(--yb-card-radius);
  background: var(--yb-card-bg);
  overflow: hidden;
}
.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--yb-space-2);
  padding: var(--yb-space-2) var(--yb-space-3);
  border-bottom: 1px solid var(--yb-card-row-line);
  background: var(--yb-card-page-bg);
}
.panel-title {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: var(--yb-fs-md);
  font-weight: var(--yb-fw-bold);
  color: var(--yb-text-dim);
}
.count {
  padding: 0 5px;
  border-radius: var(--yb-radius-pill);
  background: var(--yb-btn-neutral);
  font-size: var(--yb-fs-xs);
}
.panel-body {
  padding: var(--yb-space-2);
}
/* 待批准面板：顶部一条琥珀提示边（全页唯一强调） */
.panel-pending {
  border-color: var(--yb-intent-pending);
  box-shadow: var(--yb-shadow-1);
}
.panel-pending .panel-head {
  background: var(--yb-intent-pending-soft);
}
.panel-pending .panel-title {
  color: var(--yb-intent-pending-ink);
}
.panel-pending .count {
  background: rgba(255, 255, 255, 0.6);
  color: var(--yb-intent-pending-ink);
}

/* 待批准卡片 */
.ap-card {
  padding: var(--yb-space-2);
  border-radius: var(--yb-radius-xs);
}
.ap-card + .ap-card {
  border-top: 1px solid var(--yb-card-row-line);
}
.ap-card.selected {
  background: var(--yb-row-selected);
}
.ap-top {
  display: flex;
  align-items: flex-start;
  gap: var(--yb-space-2);
}
.ap-check input {
  margin: 2px 0 0;
  accent-color: var(--yb-accent);
  cursor: pointer;
}
.ap-info {
  min-width: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.ap-label {
  font-size: var(--yb-fs-lg);
  font-weight: var(--yb-fw-medium);
  line-height: var(--yb-lh-ui);
}
.ap-desc {
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-dim);
  line-height: var(--yb-lh-ui);
  /* 描述允许两行，超出截断（右列窄，单行会大量丢信息） */
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.ap-remember {
  display: flex;
  align-items: center;
  gap: 5px;
  margin: var(--yb-space-2) 0 0;
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-dim);
  cursor: pointer;
  user-select: none;
}
.ap-remember input {
  margin: 0;
  accent-color: var(--yb-accent);
  cursor: pointer;
}
.ap-btns,
.ap-batch {
  display: flex;
  gap: var(--yb-space-2);
  margin-top: var(--yb-space-2);
}
.ap-btns button,
.ap-batch button {
  flex: 1;
}
.ap-batch {
  padding-top: var(--yb-space-2);
  border-top: 1px solid var(--yb-card-row-line);
}

/* 按钮：macOS 语言——主按钮 accent 实底，次按钮无底描边 */
.btn-primary,
.btn-ghost {
  padding: 5px var(--yb-space-3);
  border-radius: var(--yb-radius-xs);
  font-size: var(--yb-fs-md);
  font-family: inherit;
  font-weight: var(--yb-fw-medium);
  cursor: pointer;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.btn-primary {
  border: none;
  background: var(--yb-accent);
  color: var(--yb-text-on-accent);
}
.btn-primary:hover:not(:disabled) {
  background: var(--yb-accent-deep);
}
.btn-ghost {
  border: 1px solid var(--yb-border-strong);
  background: var(--yb-card-bg);
  color: var(--yb-text-dim);
}
.btn-ghost:hover:not(:disabled) {
  color: var(--yb-danger);
  border-color: var(--yb-danger);
}

/* 进行中行 */
.run-row {
  display: flex;
  align-items: center;
  gap: var(--yb-space-2);
  width: 100%;
  padding: var(--yb-space-2);
  border: none;
  border-radius: var(--yb-radius-xs);
  background: transparent;
  color: var(--yb-text);
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  transition: background var(--yb-dur-fast) var(--yb-ease-out);
}
.run-row:hover {
  background: var(--yb-row-hover);
}
.run-dot {
  flex-shrink: 0;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--yb-accent);
  box-shadow: 0 0 0 3px var(--yb-accent-soft);
}
.run-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.run-main strong {
  font-size: var(--yb-fs-md);
  font-weight: var(--yb-fw-medium);
  line-height: var(--yb-lh-ui);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.run-main span {
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-dim);
  line-height: var(--yb-lh-ui);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.run-time {
  flex-shrink: 0;
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-faint);
}

/* widget 一瞥卡：固定高度，内部自滚 */
.w-body {
  height: 150px;
  padding: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

/* ---- 底部输入条 ---- */
.bar {
  flex-shrink: 0;
  padding: var(--yb-space-3) var(--yb-space-5) var(--yb-space-4);
  border-top: 1px solid var(--yb-border-base);
  background: var(--yb-content-bg);
}
</style>
