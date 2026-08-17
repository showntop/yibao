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
import { getCurrentWindow } from "@tauri-apps/api/window";
import InputBar from "./InputBar.vue";
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
  sendFeedFeedback,
  feedTierOf,
  recapCheck,
  getDistillTimelineOnce,
  onRecapOpen,
  type FeedItem,
  type FeedStats,
  type RunningTask,
  type PendingConfirm,
  type WidgetPayload,
  type DistillDay,
  canRememberSkill,
  rememberLabelForSkill,
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

// 页头此刻时间（秒级刷新，等宽数字显示）
const now = ref(new Date());
let clockTimer: ReturnType<typeof setInterval> | null = null;
const clockTime = computed(() =>
  `${String(now.value.getHours()).padStart(2, "0")}:${String(now.value.getMinutes()).padStart(2, "0")}`,
);

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

// ---- 视图切换：动态 | 回顾（晨间反刍 + 每日回顾）----
type FeedView = "feed" | "recap";
const view = ref<FeedView>("feed");
const recapDays = ref<DistillDay[]>([]);
const recapLoaded = ref(false);
const recapFocusDay = ref<string | null>(null);

async function loadRecap() {
  recapDays.value = await getDistillTimelineOnce(14);
  recapLoaded.value = true;
}

/** 秒 → "1.2h"；0 返回空串（不显示该 app）。 */
function fmtHours(sec: number): string {
  return sec > 0 ? `${(sec / 3600).toFixed(1)}h` : "";
}

/** 深度专注段："09:30–11:00 · 14:00–15:30"（最多 3 段）。 */
function activeRangesLabel(stats: DistillDay["stats"]): string {
  const rs = stats.active_ranges ?? [];
  if (!rs.length) return "";
  const f = (t: number) =>
    `${String(new Date(t * 1000).getHours()).padStart(2, "0")}:${String(new Date(t * 1000).getMinutes()).padStart(2, "0")}`;
  return rs.slice(0, 3).map((r) => `${f(r[0])}–${f(r[1])}`).join(" · ");
}

const STATUS_LABELS: Record<string, string> = {
  ok: "已提炼",
  failed: "提炼失败",
  no_data: "当日无数据",
  pending: "未提炼",
};
function statusLabel(s: string): string {
  return STATUS_LABELS[s] ?? s;
}
function recapInsights(d: DistillDay) {
  return d.items.filter((i) => i.kind === "insight");
}
function recapEvents(d: DistillDay) {
  return d.items.filter((i) => i.kind === "event");
}

// 切到回顾 mode 时按需加载（首次进入才拉一次）
watch(view, (v) => {
  if (v === "recap" && !recapLoaded.value) void loadRecap();
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

/** 点 widget 入口 → 调声明的 open 方法开全面板（panel 事件回来，插件页自动接管切页）。 */
function openWidget(w: WidgetPayload) {
  if (!w.open) return;
  const pid = w.panel.split(":")[0];
  void panelAction(w.open, {}, undefined, `panel:${pid}`).catch(() => {});
}

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

/** 乐观置已读（失败回滚）：openInChat 与 coding 任务卡 attach 共用。 */
async function markReadOptimistic(it: FeedItem) {
  if (it.read !== 0) return;
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

/** 点动态 → 乐观置已读 + 带自包含上下文草稿切对话页（大脑看不到 Feed，上下文随草稿走）。 */
async function openInChat(it: FeedItem) {
  await markReadOptimistic(it);
  const oneLine = it.text.replace(/\s+/g, " ").trim();
  const truncated = oneLine.length > 60 ? oneLine.slice(0, 60) + "…" : oneLine;
  const prompt = typeof it.meta?.prompt === "string" && it.meta.prompt ? it.meta.prompt : "";
  const draft = it.kind === "task" && prompt
    ? `关于任务「${prompt.length > 40 ? prompt.slice(0, 40) + "…" : prompt}」：`
    : `关于「${truncated}」：`;
  emit("chat", draft);
}

// ---- coding 任务卡点击路由（B3）：attach 打开 coding:chat 面板并恢复该会话 ----
// 直调失败/会话不存在的回执经 onBrainEvent 按 skill_id 认领（面板没开成必须看得见，点了没反应是最差反馈）
const actionErr = ref("");

/** coding 任务卡 → coding.attach{session_id}（L0 直调）：面板窗/peek 由宿主既有表面裁决呈现，
 *  data={session_id, attach:true} 透传进面板 init，chat.html 自动 resumeSession。 */
async function openCodingSession(it: FeedItem, sid: string) {
  actionErr.value = "";
  await markReadOptimistic(it);
  try {
    await panelAction("coding.attach", { session_id: sid }, undefined, "panel:coding");
  } catch (err) {
    actionErr.value = "打开编码会话失败：" + String(err);
  }
}

/** 时间线行点击路由：meta.plugin==="coding" 且带会话 id → attach 接管；其余原行为（带草稿切对话页）。 */
function onItemClick(it: FeedItem) {
  const sid = it.meta?.plugin === "coding" && typeof it.meta?.id === "string" ? it.meta.id : "";
  if (sid) {
    void openCodingSession(it, sid);
    return;
  }
  void openInChat(it);
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

// ---- 误报反馈（信任仪表写侧）：👍/👎 落 meta.feedback，同类 24h≥2👎 大脑降 quiet ----

/** 写反馈：乐观改 it.meta.feedback + 失败回滚。 */
async function setFeedback(it: FeedItem, feedback: "up" | "down" | "none") {
  const prev = (it.meta?.feedback as string | undefined) ?? "none";
  if (prev === feedback) return; // 幂等
  if (!it.meta) it.meta = {};
  it.meta.feedback = feedback;
  try {
    await sendFeedFeedback(it.id, feedback);
  } catch {
    it.meta.feedback = prev; // 失败回滚
  }
}

/** 👍/👎 按钮：点已在态则取消回 none，否则切到该态。 */
function toggleFeedback(it: FeedItem, target: "up" | "down") {
  const cur = (it.meta?.feedback as string | undefined) ?? "none";
  void setFeedback(it, cur === target ? "none" : target);
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
// 回顾：开窗 recap_check 的可见性监听 + recap-open deep-link
let unRecapVisible: (() => void) | null = null;
let unRecapOpen: (() => void) | null = null;
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
    // coding.attach 失败回执认领：直调失败走 action_result(success=false)，闸门/异常走 error 事件
    if (e.action?.skill_id === "coding.attach") {
      if (e.kind === "error") actionErr.value = e.text ?? "打开编码会话失败";
      else if (e.kind === "action_result" && e.result && !e.result.success) {
        actionErr.value = e.result.error ?? "打开编码会话失败";
      }
    }
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
  // deep-link：pet 窗气泡点击 → 切回顾 mode + 跳指定天
  unRecapOpen = await onRecapOpen((day) => {
    view.value = "recap";
    recapFocusDay.value = day;
    if (!recapLoaded.value) void loadRecap();
  });
  clockTimer = setInterval(() => (now.value = new Date()), 1000);
});
onUnmounted(() => {
  unFeed?.();
  unWidgets?.();
  unEvent?.();
  unApprovals?.();
  unRecapVisible?.();
  unRecapOpen?.();
  if (refetchTimer !== null) clearTimeout(refetchTimer);
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
        <div v-if="loaded && !overview.length && !runningTasks.length && !widgets.length" class="now-quiet">
          此刻很清净，随时叫我
        </div>
      </div>
    </section>

    <!-- 需要你决定：唯一必须你动手的事（琥珀强调，有才显） -->
    <section v-if="approvals.length" class="decide-card">
      <div class="decide-title">
        <YbIcon name="lock" :size="12" />需要你决定
        <span class="count yb-num">{{ approvals.length }}</span>
      </div>
      <div class="decide-body">
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
          <label v-if="canRememberSkill(p.skill)" class="ap-remember" :title="rememberLabelForSkill(p.skill)">
            <input type="checkbox" :checked="rememberOf(p.id)" @change="onToggleRemember(p.id, $event)" />
            <span>{{ rememberLabelForSkill(p.skill) }}</span>
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

    <!-- 主体：时间线（动态/回顾） -->
    <div class="body">
      <!-- ===== 左主列：统一时间线 ===== -->
      <section class="timeline-col">
        <!-- 顶部视图切换：动态｜回顾（macOS Segmented，复用 .segmented 样式） -->
        <div class="segmented view-toggle">
          <button class="seg" :class="{ on: view === 'feed' }" @click="view = 'feed'">动态</button>
          <button class="seg" :class="{ on: view === 'recap' }" @click="view = 'recap'">回顾</button>
        </div>

        <template v-if="view === 'feed'">
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

        <!-- coding 任务卡 attach 失败回执（会话已清理/直调失败）：细条亮出，下次点击自动清 -->
        <div v-if="actionErr" class="tl-err"><YbIcon name="alert" :size="13" />{{ actionErr }}</div>

        <div class="tl-scroll">
          <template v-if="groups.length">
            <div v-for="g in groups" :key="g.key" class="tl-group">
              <div class="tl-date">{{ g.label }}</div>
              <button
                v-for="it in g.items"
                :key="it.id"
                class="tl-row"
                :class="[tierClass(it), { unread: it.read === 0, [`st-${it.status}`]: it.status !== 'none' }]"
                @click="onItemClick(it)"
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
                    :class="{ on: it.meta?.feedback === 'up' }"
                    :title="it.meta?.feedback === 'up' ? '取消「有用」' : '有用'"
                    @click="toggleFeedback(it, 'up')"
                  >
                    <YbIcon name="thumb-up" :size="12" />
                  </button>
                  <button
                    class="tl-act"
                    :class="{ on: it.meta?.feedback === 'down' }"
                    :title="it.meta?.feedback === 'down' ? '取消「误报」' : '误报（同类将减少打扰）'"
                    @click="toggleFeedback(it, 'down')"
                  >
                    <YbIcon name="thumb-down" :size="12" />
                  </button>
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
        </template>

        <!-- 回顾视图：按天卡片（app 时长 + 深度专注段 + 洞察/事件 + 状态徽章） -->
        <section v-else class="recap-list">
          <div
            v-for="d in recapDays"
            :key="d.day"
            class="recap-day"
            :class="{ focus: d.day === recapFocusDay }"
          >
            <div class="rd-head">
              <strong class="rd-date yb-num">{{ d.day }}</strong>
              <span class="rd-status" :class="`st-${d.status}`">{{ statusLabel(d.status) }}</span>
            </div>
            <div v-if="d.status === 'ok'" class="rd-body">
              <p
                v-if="Object.keys(d.stats.app_seconds ?? {}).length"
                class="rd-stats yb-num"
              >
                {{
                  Object.entries(d.stats.app_seconds ?? {})
                    .sort((a, b) => b[1] - a[1])
                    .slice(0, 4)
                    .map(([k, v]) => `${k} ${fmtHours(v)}`)
                    .join(" · ")
                }}
              </p>
              <p v-if="activeRangesLabel(d.stats)" class="rd-blocks">深度专注 {{ activeRangesLabel(d.stats) }}</p>
              <ul class="rd-items">
                <li v-for="i in recapInsights(d)" :key="i.id" class="rd-item insight">💡 {{ i.text }}</li>
                <li v-for="i in recapEvents(d)" :key="i.id" class="rd-item event">📌 {{ i.text }}</li>
              </ul>
              <p
                v-if="!recapInsights(d).length && !recapEvents(d).length"
                class="rd-empty"
              >这天没有洞察</p>
            </div>
            <p v-else class="rd-empty">{{ statusLabel(d.status) }}</p>
          </div>
          <p v-if="recapLoaded && !recapDays.length" class="rd-empty">暂时没有回顾</p>
        </section>
      </section>

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

/* ---- 「此刻」卡：AI 正在为你做什么（状态 + 进行中 + widget 入口） ---- */
.now-card {
  flex-shrink: 0;
  margin: 0 var(--yb-space-5) var(--yb-space-3);
  border: 1px solid var(--yb-card-border);
  border-radius: var(--yb-card-radius);
  background: var(--yb-card-bg);
  box-shadow: var(--yb-shadow-1);
  overflow: hidden;
  /* 兜底：极端情况下（widget 很多+正在跑任务多）整体卡限高，主体时间线不被挤没 */
  max-height: 320px;
  overflow-y: auto;
  scrollbar-width: thin;
}
.now-title {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: var(--yb-space-2) var(--yb-space-4);
  border-bottom: 1px solid var(--yb-card-row-line);
  background: var(--yb-card-page-bg);
  font-size: var(--yb-fs-md);
  font-weight: var(--yb-fw-bold);
  color: var(--yb-text-dim);
  letter-spacing: 0.02em;
}
/* 此刻脉动点：accent 圆点 + 光晕 */
.now-dot {
  flex-shrink: 0;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--yb-accent);
  box-shadow: 0 0 0 3px var(--yb-accent-soft);
}
.now-body {
  padding: var(--yb-space-3) var(--yb-space-4);
  display: flex;
  flex-direction: column;
  gap: var(--yb-space-3);
}
/* 状态数字 chips：今日完成 / 正在跑 / 待提醒（0 不显） */
.now-chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--yb-space-2);
}
.now-chip {
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
  padding: 3px 10px;
  border: 1px solid var(--yb-border-base);
  border-radius: var(--yb-radius-pill);
  background: var(--yb-surface-2);
  font-size: var(--yb-fs-md);
  color: var(--yb-text-dim);
}
.now-chip strong {
  font-size: var(--yb-fs-lg);
  font-weight: var(--yb-fw-bold);
  line-height: 1;
  color: var(--yb-accent-deep);
}
.now-block {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.now-block-label {
  font-size: var(--yb-fs-sm);
  font-weight: var(--yb-fw-bold);
  color: var(--yb-text-faint);
  letter-spacing: 0.03em;
}
/* 插件 widget 入口行：单行紧凑（标题 + chevron），点全行打开全面板
 * AI 原生：主屏只暴露 widget 入口，不展示完整内容（详情去插件页） */
.now-widget {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--yb-space-2);
  width: 100%;
  padding: 7px var(--yb-space-3);
  border: 1px dashed var(--yb-card-border);
  border-radius: var(--yb-radius-sm);
  background: var(--yb-surface-2);
  color: var(--yb-text);
  font-family: inherit;
  font-size: var(--yb-fs-md);
  text-align: left;
  cursor: pointer;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.now-widget:hover:not(:disabled) {
  border-color: var(--yb-accent);
  background: var(--yb-surface-3);
  border-style: solid;
}
.now-widget:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}
.now-widget-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: var(--yb-fw-medium);
}
.now-chev {
  flex-shrink: 0;
  width: 12px;
  height: 12px;
  color: var(--yb-text-faint);
  transition: transform var(--yb-dur-fast) var(--yb-ease-out),
              color var(--yb-dur-fast) var(--yb-ease-out);
}
.now-widget:hover:not(:disabled) .now-chev {
  color: var(--yb-accent);
  transform: translateX(2px);
}
.now-quiet {
  font-size: var(--yb-fs-md);
  color: var(--yb-text-faint);
  line-height: 1.4;
}

/* ---- 「需要你决定」卡：待批准（琥珀强调，全页唯一） ---- */
.decide-card {
  flex-shrink: 0;
  margin: 0 var(--yb-space-5) var(--yb-space-3);
  border: 1px solid var(--yb-intent-pending);
  border-radius: var(--yb-card-radius);
  background: var(--yb-card-bg);
  box-shadow: var(--yb-shadow-1);
  overflow: hidden;
}
.decide-title {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: var(--yb-space-2) var(--yb-space-4);
  border-bottom: 1px solid var(--yb-card-row-line);
  background: var(--yb-intent-pending-soft);
  font-size: var(--yb-fs-md);
  font-weight: var(--yb-fw-bold);
  color: var(--yb-intent-pending-ink);
}
.decide-title .count {
  background: rgba(255, 255, 255, 0.6);
  color: var(--yb-intent-pending-ink);
}
.decide-body {
  padding: var(--yb-space-2);
}

/* ---- 页头 ---- */
.page-head {
  flex-shrink: 0;
  padding: 0 var(--yb-space-5) var(--yb-space-3);
  user-select: none;
}
.head-line {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--yb-space-3);
  flex-wrap: wrap;
}
.head-left {
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
/* 页头右侧此刻时间（秒级刷新，等宽数字） */
.head-time {
  font-size: 22px;
  font-weight: var(--yb-fw-bold);
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.01em;
  color: var(--yb-text-strong);
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
  /* 主屏无右副列了——时间线占满主体（之前限宽是为避免与右列争空间） */
  max-width: none;
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
/* coding 任务卡 attach 失败回执细条（同 PanelApp .error-bar 语言） */
.tl-err {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: var(--yb-space-1);
  margin-bottom: var(--yb-space-2);
  padding: 6px var(--yb-space-3);
  border-radius: var(--yb-radius-sm);
  background: var(--yb-danger-soft);
  color: var(--yb-danger);
  font-size: var(--yb-fs-md);
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
  transition: color var(--yb-dur-fast) var(--yb-ease-out);
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

/* 待批准卡片徽标（decide-title 内复用） */
.count {
  padding: 0 5px;
  border-radius: var(--yb-radius-pill);
  background: var(--yb-btn-neutral);
  font-size: var(--yb-fs-xs);
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
  border-radius: var(--yb-radius-sm);
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

/* ---- 底部输入条 ---- */
.bar {
  flex-shrink: 0;
  padding: var(--yb-space-3) var(--yb-space-5) var(--yb-space-4);
  border-top: 1px solid var(--yb-border-base);
  background: var(--yb-content-bg);
}

/* ---- 回顾视图：按天卡片 ---- */
/* 视图切换行：复用 .segmented 样式，独立一行（与下方筛选 segmented 不挤在一起） */
.view-toggle {
  flex-shrink: 0;
  margin-bottom: var(--yb-space-3);
}
.recap-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  scrollbar-width: thin;
  padding-bottom: var(--yb-space-3);
}
.recap-list::-webkit-scrollbar {
  width: 7px;
}
.recap-list::-webkit-scrollbar-thumb {
  background: var(--yb-border-strong);
  border-radius: var(--yb-radius-pill);
}
.recap-list::-webkit-scrollbar-track {
  background: transparent;
}
.recap-day {
  padding: var(--yb-space-3) 0;
  border-bottom: 1px solid var(--yb-card-row-line);
}
.recap-day.focus {
  /* deep-link 高亮：柔和强调，不抢主信息 */
  background: var(--yb-accent-soft);
  border-radius: var(--yb-radius-xs);
  padding-left: var(--yb-space-2);
  padding-right: var(--yb-space-2);
}
.rd-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}
.rd-date {
  font-size: var(--yb-fs-lg);
  font-weight: var(--yb-fw-bold);
  color: var(--yb-text-strong);
}
.rd-status {
  font-size: var(--yb-fs-xs);
  color: var(--yb-text-dim);
}
/* 状态色：失败用琥珀提醒，pending 用中性 */
.rd-status.st-failed { color: var(--yb-accent-deep); }
.rd-stats,
.rd-blocks {
  margin: var(--yb-space-1) 0;
  font-size: var(--yb-fs-md);
  color: var(--yb-text-dim);
}
.rd-items {
  list-style: none;
  margin: var(--yb-space-2) 0 0;
  padding: 0;
}
.rd-item {
  padding: 4px 0;
  font-size: var(--yb-fs-lg);
  line-height: var(--yb-lh-ui);
}
.rd-item.insight {
  color: var(--yb-text);
}
.rd-item.event {
  color: var(--yb-text-dim);
}
.rd-empty {
  color: var(--yb-text-faint);
  font-size: var(--yb-fs-md);
}
</style>
