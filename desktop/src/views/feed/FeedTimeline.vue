<script setup lang="ts">
// 主屏「动态」时间线（自包含）：统一时间线（今天/昨天/更早分组 + 处置态 + 误报反馈 + coding 任务卡路由）。
// 自己订阅 feed 数据流；stats 变化经 emit 上报父（概览数字条 + sidebar 未读徽标）。
// 共享样式在 assets/home-feed.css（.tl-*）。
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import YbIcon from "../../components/common/YbIcon.vue";
import {
  getFeedOnce,
  fetchFeed,
  onFeed,
  onBrainEvent,
  panelAction,
  markFeedRead,
  markAllFeedRead,
  markFeedStatus,
  sendFeedFeedback,
  feedTierOf,
  type FeedItem,
  type FeedStats,
  type RunningTask,
} from "../../lib/brain";
import { squashSpaces, stripTaskStatusEmoji, truncate } from "../../lib/text";
import { fmtHHMM, fmtMonthDay } from "../../lib/time";

const emit = defineEmits<{ chat: [draft?: string]; unread: [n: number]; stats: [s: FeedStats, running: RunningTask[]] }>();

// ---- 时间线数据（自订阅）----
const items = ref<FeedItem[]>([]);
const runningTasks = ref<RunningTask[]>([]);
const stats = ref<FeedStats>({ pending_reminders: 0, running_tasks: 0, done_24h: 0, unread: 0, ignored: 0 });
const loaded = ref(false);

// 未读数同步给父组件（Home.vue sidebar 徽标）；stats 变化同步给父（概览数字条）。
watch(() => stats.value.unread, (n) => emit("unread", n));
watch([stats, runningTasks], () => emit("stats", stats.value, runningTasks.value), { deep: true });

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

/** 动态类型 → YbIcon 图标名。任务按成败分图标：成功 check、失败 x。 */
function kindIcon(it: FeedItem): "clock" | "check" | "x" | "chat" {
  if (it.kind === "reminder") return "clock";
  if (it.kind === "task") return taskStatus(it) === "done" ? "check" : "x";
  return "chat";
}

function taskStatus(it: FeedItem): string {
  return String(it.meta?.status ?? "done");
}

/** 任务卡正文：剥 emoji 状态前缀（状态已由行首图标 + tl-tag 徽章表达，三重复反而吵）。 */
function displayText(it: FeedItem): string {
  return it.kind === "task" ? stripTaskStatusEmoji(it.text) : it.text;
}

function taskStatusLabel(it: FeedItem): string {
  return ({ done: "完成", failed: "失败", stopped: "已停止", interrupted: "已中断" } as Record<string, string>)[
    taskStatus(it)
  ] ?? "已结束";
}

/** 相对时间：组内只显示时刻（日期已由分组头承担），跨日项显示月日。 */
function itemTime(ts: number): string {
  const d = new Date(ts * 1000);
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return "刚刚";
  if (diff < 172800) return fmtHHMM(d);
  return fmtMonthDay(d);
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
  const oneLine = squashSpaces(it.text);
  const truncated = truncate(oneLine, 60);
  const prompt = typeof it.meta?.prompt === "string" && it.meta.prompt ? it.meta.prompt : "";
  const draft = it.kind === "task" && prompt
    ? `关于任务「${truncate(prompt, 40)}」：`
    : `关于「${truncated}」：`;
  emit("chat", draft);
}

// ---- coding 任务卡点击路由（B3）：attach 打开 coding:studio 面板并恢复该会话 ----
// 直调失败/会话不存在的回执经 onBrainEvent 按 tool_id 认领（面板没开成必须看得见，点了没反应是最差反馈）
const actionErr = ref("");

/** coding 任务卡 → coding.attach{session_id}（L0 直调）：面板窗/peek 由宿主既有表面裁决呈现，
 *  data={session_id, attach:true} 透传进面板 init，studio 面板 handleData 自动 resumeSession。 */
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

let unFeed: (() => void) | null = null;
let unEvent: (() => void) | null = null;
let refetchTimer: ReturnType<typeof setTimeout> | null = null;

function scheduleFeedRefresh() {
  if (refetchTimer !== null) clearTimeout(refetchTimer);
  refetchTimer = setTimeout(() => {
    void fetchFeed().catch(() => {});
  }, 800);
}

onMounted(async () => {
  await reload();
  unFeed = await onFeed((r) => {
    items.value = r.items;
    stats.value = r.stats;
    runningTasks.value = r.running_tasks ?? [];
  });
  unEvent = await onBrainEvent((e) => {
    const agentChanged = e.kind === "action_result"
      && !!e.action?.tool_id?.startsWith("agents.");
    if (e.kind === "reminder" || agentChanged) scheduleFeedRefresh();
    // coding.attach 失败回执认领：直调失败走 action_result(success=false)，闸门/异常走 error 事件
    if (e.action?.tool_id === "coding.attach") {
      if (e.kind === "error") actionErr.value = e.text ?? "打开编码会话失败";
      else if (e.kind === "action_result" && e.result && !e.result.success) {
        actionErr.value = e.result.error ?? "打开编码会话失败";
      }
    }
  });
});
onUnmounted(() => {
  unFeed?.();
  unEvent?.();
  if (refetchTimer !== null) clearTimeout(refetchTimer);
});
</script>

<template>
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
              <span class="tl-text">{{ displayText(it) }}</span>
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
          <button v-for="c in STARTERS" :key="c" class="te-chip" @click="emit('chat', c)">{{ c }}</button>
        </div>
      </div>
    </div>
</template>
