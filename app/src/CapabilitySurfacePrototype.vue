<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import Avatar from "./components/Avatar.vue";
import YbIcon from "./components/YbIcon.vue";
import appLogo from "./assets/logo.png";
import {
  clearCapabilitySurfaceSnapshot,
  createDefaultSnapshot,
  loadCapabilitySurfaceSnapshot,
  saveCapabilitySurfaceSnapshot,
  type StageView,
  type SurfaceState,
  type TimelineEntry,
  type TimelineFilter,
  type Topic,
  type TopicStatus,
} from "./lib/capability-surface-state";

const columns: TopicStatus[] = ["想法", "待验证", "写作中"];
const savedSnapshot = loadCapabilitySurfaceSnapshot();
const initialSnapshot = savedSnapshot ?? createDefaultSnapshot();

const surface = ref<SurfaceState>(initialSnapshot.surface);
const hasOpened = ref(initialSnapshot.hasOpened);
const selectedId = ref<number | null>(initialSnapshot.selectedId);
const draft = ref(initialSnapshot.draft);
const stageView = ref<StageView>(initialSnapshot.stageView);
const timelineFilter = ref<TimelineFilter>("all");
const topics = ref<Topic[]>(initialSnapshot.topics.map((topic) => ({ ...topic })));
const timeline = ref<TimelineEntry[]>(initialSnapshot.timeline.map((entry) => ({ ...entry })));
const inputEl = ref<HTMLInputElement | null>(null);
const timelineEl = ref<HTMLElement | null>(null);
const restoredToast = ref(Boolean(savedSnapshot));
const entryId = ref(Math.max(0, ...timeline.value.map((entry) => entry.id)));
const clockNow = ref(Date.now());

let surfaceTimer: ReturnType<typeof setTimeout> | null = null;
let replyTimer: ReturnType<typeof setTimeout> | null = null;
let restoredTimer: ReturnType<typeof setTimeout> | null = null;
let clockTimer: ReturnType<typeof setInterval> | null = null;

const isOpen = computed(() => surface.value === "stage" || surface.value === "focus");
const selected = computed(() => topics.value.find((topic) => topic.id === selectedId.value) ?? null);
const scopeText = computed(() => selected.value ? `在看：${selected.value.title}` : isOpen.value ? "在看：选题看板" : "");
const surfaceLabel = computed(() => surface.value === "focus" ? "退出专注" : "专注查看");
const composerPlaceholder = computed(() => {
  if (selected.value) return "对译宝说，继续操作当前选题…";
  if (isOpen.value) return "对译宝说，操作当前看板…";
  return "对译宝说点什么…";
});
const filteredTimeline = computed(() => {
  if (timelineFilter.value === "activity") return timeline.value.filter((entry) => entry.kind === "activity");
  if (timelineFilter.value === "conversation") return timeline.value.filter((entry) => entry.kind !== "activity");
  return timeline.value;
});
const conversationCount = computed(() => timeline.value.filter((entry) => entry.kind !== "activity").length);
const activityCount = computed(() => timeline.value.filter((entry) => entry.kind === "activity").length);
const lastActivityId = computed(() => [...timeline.value].reverse().find((entry) => entry.kind === "activity")?.id ?? null);

function persistSnapshot() {
  saveCapabilitySurfaceSnapshot({
    version: 2,
    surface: isOpen.value ? "stage" : "closed",
    hasOpened: hasOpened.value,
    selectedId: selectedId.value,
    draft: draft.value,
    stageView: stageView.value,
    topics: topics.value,
    timeline: timeline.value,
  });
}

watch([surface, hasOpened, selectedId, draft, stageView, topics, timeline], persistSnapshot, { deep: true });
watch(() => filteredTimeline.value.length, () => {
  void nextTick(() => {
    if (timelineEl.value) timelineEl.value.scrollTop = timelineEl.value.scrollHeight;
  });
});

function appendEntry(entry: Omit<TimelineEntry, "id" | "ts"> & { ts?: number }): TimelineEntry {
  const item: TimelineEntry = { ...entry, id: ++entryId.value, ts: entry.ts ?? Date.now() };
  timeline.value.push(item);
  return item;
}

function appendActivity(action: string, text: string, options: Partial<TimelineEntry> = {}): TimelineEntry {
  return appendEntry({
    kind: "activity",
    plugin: "自媒体",
    action,
    text,
    status: "done",
    ...options,
  });
}

function openSurface() {
  if (isOpen.value || surface.value === "loading") return;
  timelineFilter.value = "all";
  surface.value = "loading";
  const reopening = hasOpened.value;
  hasOpened.value = true;
  const activity = appendActivity(
    reopening ? "恢复工作面" : "打开工作面",
    reopening ? "正在恢复上次的选题看板" : "正在准备选题看板",
    { status: "running", detail: "工作面附着在当前任务，沿用同一条对话与对象上下文。" },
  );
  surfaceTimer = setTimeout(() => {
    surface.value = "stage";
    activity.status = "done";
    activity.text = reopening ? "已恢复选题看板" : "选题看板已成为当前任务主舞台";
    surfaceTimer = null;
  }, 420);
}

function closeSurface() {
  surface.value = "closed";
  void nextTick(() => inputEl.value?.focus());
}

function toggleFocus() {
  surface.value = surface.value === "focus" ? "stage" : "focus";
}

function selectTopic(topic: Topic) {
  if (selectedId.value !== topic.id) {
    selectedId.value = topic.id;
    appendActivity("切换当前对象", `已把「${topic.title}」设为当前对象`, {
      object: topic.title,
      detail: "后续自然语言中的“这个”将优先指向该选题。按 Esc 可先清除对象作用域。",
    });
  }
  void nextTick(() => inputEl.value?.focus());
}

function clearSelected() {
  selectedId.value = null;
  void nextTick(() => inputEl.value?.focus());
}

function toggleActivity(entry: TimelineEntry) {
  if (entry.kind === "activity") entry.expanded = !entry.expanded;
}

function addTopic() {
  const id = Math.max(0, ...topics.value.map((topic) => topic.id)) + 1;
  const topic: Topic = {
    id,
    title: "译宝与能力表面的共同记忆",
    note: "对话、调用与对象状态如何统一恢复",
    status: "想法",
    sources: 0,
  };
  topics.value.unshift(topic);
  selectedId.value = id;
  appendActivity("新建选题", `已创建「${topic.title}」`, {
    object: topic.title,
    detail: "新选题已加入“想法”列，并自动成为共享命令栏的当前对象。",
  });
  void nextTick(() => inputEl.value?.focus());
}

function send() {
  const text = draft.value.trim();
  if (!text) return;
  timelineFilter.value = "all";
  const topicAtSend = selected.value;
  appendEntry({ kind: "user", text });
  draft.value = "";
  const activity = appendActivity(
    topicAtSend ? "补充选题" : "继续整理",
    topicAtSend ? `正在围绕「${topicAtSend.title}」查找案例` : "正在继续整理当前看板",
    {
      status: "running",
      object: topicAtSend?.title,
      detail: topicAtSend ? "查找关联案例、去重，并把可引用来源同步回当前选题。" : "继续分析当前任务并同步结果。",
    },
  );
  replyTimer = setTimeout(() => {
    activity.status = "done";
    activity.text = topicAtSend ? "已找到 5 个案例并同步进当前选题" : "已完成整理并同步工作面";
    activity.detail = topicAtSend ? "新增 5 个案例线索；对象作用域、插件结果与对话回复已写入同一任务时间线。" : "结果已写入当前任务时间线。";
    if (topicAtSend) topicAtSend.sources += 5;
    appendEntry({
      kind: "assistant",
      text: topicAtSend
        ? `已经围绕「${topicAtSend.title}」补了 5 个案例，素材数也同步更新了。协作过程和结果都留在左侧时间线里。`
        : "已经继续整理好了，结果与执行记录都同步到当前任务。",
    });
    replyTimer = null;
  }, 620);
}

function resetDemo() {
  const fresh = createDefaultSnapshot();
  if (surfaceTimer) clearTimeout(surfaceTimer);
  if (replyTimer) clearTimeout(replyTimer);
  clearCapabilitySurfaceSnapshot();
  surface.value = fresh.surface;
  hasOpened.value = fresh.hasOpened;
  selectedId.value = fresh.selectedId;
  draft.value = fresh.draft;
  stageView.value = fresh.stageView;
  topics.value = fresh.topics;
  timeline.value = fresh.timeline;
  timelineFilter.value = "all";
  entryId.value = Math.max(...fresh.timeline.map((entry) => entry.id));
  restoredToast.value = false;
}

function formatTime(ts: number): string {
  const diff = Math.max(0, clockNow.value - ts);
  if (diff < 60_000) return "刚刚";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`;
  return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false }).format(ts);
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === "Escape" && surface.value !== "closed") {
    event.preventDefault();
    if (selected.value) clearSelected();
    else if (surface.value === "focus") surface.value = "stage";
    else closeSurface();
  }
}

onMounted(() => {
  clockTimer = setInterval(() => { clockNow.value = Date.now(); }, 30_000);
  if (restoredToast.value) {
    restoredTimer = setTimeout(() => { restoredToast.value = false; }, 2600);
  }
});

onBeforeUnmount(() => {
  if (surfaceTimer) clearTimeout(surfaceTimer);
  if (replyTimer) clearTimeout(replyTimer);
  if (restoredTimer) clearTimeout(restoredTimer);
  if (clockTimer) clearInterval(clockTimer);
});
</script>

<template>
  <div
    class="prototype-shell"
    :class="{ 'surface-open': isOpen, 'surface-focus': surface === 'focus' }"
    @keydown="handleKeydown"
  >
    <header class="topbar">
      <div class="traffic-space" />
      <div class="brand">
        <img :src="appLogo" alt="译宝" />
        <span>译宝</span>
      </div>
      <nav class="nav" aria-label="主导航">
        <button class="nav-item active"><YbIcon name="inbox" :size="14" />主屏</button>
        <button class="nav-item"><YbIcon name="doc" :size="14" />数据</button>
      </nav>
      <div class="top-actions">
        <button
          v-if="hasOpened"
          class="activity-pill"
          :class="{ active: isOpen || surface === 'loading' }"
          type="button"
          @click="isOpen ? closeSurface() : openSurface()"
        >
          <span class="activity-mark"><YbIcon name="plug" :size="12" /></span>
          <span>自媒体 · 选题看板</span>
          <i :class="{ live: isOpen || surface === 'loading' }" />
        </button>
        <button class="icon-button" aria-label="搜索"><YbIcon name="search" :size="15" /></button>
        <button class="icon-button" aria-label="设置"><YbIcon name="gear" :size="15" /></button>
      </div>
    </header>

    <main class="workspace">
      <aside class="brain-column">
        <div class="brain-head">
          <div class="avatar-glow"><Avatar state="idle" :size="48" /></div>
          <div>
            <strong>译宝</strong>
            <span><i />等待下一步</span>
          </div>
        </div>
        <section class="brain-block">
          <h2>当前心智</h2>
          <button class="brain-row"><YbIcon name="sparkle" :size="13" /><span>正在围绕 AI OS 整理思路</span></button>
          <button class="brain-row"><YbIcon name="doc" :size="13" /><span>引用了 3 份产品笔记</span></button>
        </section>
        <section class="sessions">
          <div class="section-title"><span>最近会话</span><button type="button">新建</button></div>
          <button class="session active">
            <strong>AI OS 交互讨论</strong>
            <span>插件是能力，不是目的地</span>
          </button>
          <button class="session">
            <strong>Coding 产品化</strong>
            <span>昨天 · 继续改 yibao</span>
          </button>
          <button class="session">
            <strong>今日内容计划</strong>
            <span>周五 · 3 个选题待推进</span>
          </button>
        </section>
        <div v-if="isOpen" class="brain-rail-actions" aria-label="任务导航">
          <button class="active" type="button" aria-label="当前能力"><YbIcon name="plug" :size="16" /></button>
          <button type="button" aria-label="上下文"><YbIcon name="doc" :size="16" /></button>
          <button type="button" aria-label="待处理"><YbIcon name="inbox" :size="16" /></button>
          <span />
          <button type="button" aria-label="收起工作面" @click="closeSurface"><YbIcon name="x" :size="16" /></button>
        </div>
      </aside>

      <section
        class="conversation"
        aria-label="当前会话"
        :aria-hidden="surface === 'focus'"
        :inert="surface === 'focus'"
      >
        <header class="conversation-head">
          <div>
            <span>{{ isOpen ? "协作上下文" : "本次任务" }}</span>
            <strong>AI OS 交互讨论</strong>
          </div>
          <span class="quiet-state"><i />{{ isOpen ? "实时同步" : "已保存" }}</span>
        </header>

        <div v-if="isOpen" class="context-track-intro">
          <span><YbIcon name="sparkle" :size="12" />译宝仍在这里</span>
          <p>对话、插件调用和结果按发生顺序留在同一条任务时间线。</p>
        </div>

        <div v-if="isOpen" class="timeline-tools" aria-label="时间线筛选">
          <div class="timeline-tabs" role="tablist" aria-label="协作记录">
            <button type="button" :class="{ active: timelineFilter === 'all' }" @click="timelineFilter = 'all'">全部 <span>{{ timeline.length }}</span></button>
            <button type="button" :class="{ active: timelineFilter === 'conversation' }" @click="timelineFilter = 'conversation'">对话 <span>{{ conversationCount }}</span></button>
            <button type="button" :class="{ active: timelineFilter === 'activity' }" @click="timelineFilter = 'activity'">活动 <span>{{ activityCount }}</span></button>
          </div>
          <button class="timeline-reset" type="button" title="重置演示记录" aria-label="重置演示记录" @click="resetDemo"><YbIcon name="x" :size="12" /></button>
        </div>

        <div ref="timelineEl" class="message-list timeline-list">
          <template v-for="entry in filteredTimeline" :key="entry.id">
            <div v-if="entry.kind === 'user'" class="timeline-message user-wrap">
              <span class="message-time">{{ formatTime(entry.ts) }}</span>
              <div class="message user">{{ entry.text }}</div>
            </div>
            <div v-else-if="entry.kind === 'assistant'" class="message-row timeline-message">
              <Avatar state="idle" :size="22" compact />
              <div class="message-copy">
                <div class="message ai">{{ entry.text }}</div>
                <span class="message-time">{{ formatTime(entry.ts) }}</span>
              </div>
            </div>
            <article
              v-else
              class="activity-entry"
              :class="[`status-${entry.status ?? 'done'}`, { expanded: entry.expanded }]"
            >
              <button class="activity-summary" type="button" :aria-expanded="entry.expanded" @click="toggleActivity(entry)">
                <span class="capability-icon"><YbIcon :name="entry.status === 'running' ? 'spinner' : entry.status === 'failed' ? 'alert' : 'check'" :spin="entry.status === 'running'" :size="13" /></span>
                <span class="capability-copy">
                  <small>{{ entry.plugin }} · {{ entry.action }}<span v-if="entry.object"> · 当前对象</span></small>
                  <strong>{{ entry.text }}</strong>
                </span>
                <span class="message-time">{{ formatTime(entry.ts) }}</span>
              </button>
              <div v-if="entry.expanded && entry.detail" class="activity-detail">
                <span v-if="entry.object"><YbIcon name="pin" :size="11" />{{ entry.object }}</span>
                <p>{{ entry.detail }}</p>
              </div>
              <button v-if="!isOpen && entry.plugin === '自媒体' && entry.id === lastActivityId" class="activity-open" type="button" @click="openSurface">恢复工作面</button>
            </article>
          </template>
          <div v-if="!filteredTimeline.length" class="timeline-empty">这个筛选下还没有记录</div>
        </div>

      </section>

      <aside v-if="!isOpen" class="context-panel">
        <section class="context-head">
          <span>本次会话</span>
          <strong>AI OS 交互讨论</strong>
          <p>把插件调用变成当前任务里的自然协作</p>
          <em><i />等待下一步</em>
        </section>
        <section class="context-section">
          <h2>上下文 <span>3</span></h2>
          <button class="context-row"><span class="kind">文</span><span>OS 感设计调研</span><small>已引用</small></button>
          <button class="context-row"><span class="kind">文</span><span>能力表面讨论稿</span><small>刚刚</small></button>
          <button class="context-row"><span class="kind">忆</span><span>用户主导权原则</span><small>长期</small></button>
        </section>
        <section class="context-section">
          <h2>关联能力</h2>
          <button class="related-capability" type="button" @click="openSurface">
            <span class="related-icon"><YbIcon name="plug" :size="14" /></span>
            <span><strong>自媒体 · 选题看板</strong><small>已根据本次任务整理 5 个方向</small></span>
            <span class="related-action">展开</span>
          </button>
        </section>
        <section class="context-section">
          <h2>本次产出</h2>
          <button class="context-row"><YbIcon name="doc" :size="13" /><span>交互讨论稿.md</span><small>刚刚</small></button>
        </section>
      </aside>

      <Transition name="surface">
        <section v-if="isOpen" class="surface-zone" aria-label="自媒体选题工作面">
          <div class="stage">
            <header class="stage-head">
              <div class="stage-title">
                <span class="app-glyph">自</span>
                <div><small>本次任务 / 自媒体 · 主舞台</small><strong>选题看板</strong></div>
              </div>
              <div class="stage-actions">
                <span class="saved"><i />已同步</span>
                <button type="button" @click="toggleFocus"><YbIcon name="sparkle" :size="13" />{{ surfaceLabel }}</button>
                <button type="button" @click="closeSurface"><YbIcon name="x" :size="13" />收起</button>
              </div>
            </header>

            <div class="stage-toolbar">
              <div class="segmented">
                <button :class="{ active: stageView === 'board' }" type="button" @click="stageView = 'board'">看板</button>
                <button :class="{ active: stageView === 'list' }" type="button" @click="stageView = 'list'">列表</button>
              </div>
              <span>{{ topics.length }} 个选题 · {{ topics.filter((topic) => topic.status === '待验证').length }} 个待验证</span>
              <button class="add-button" type="button" @click="addTopic"><YbIcon name="sparkle" :size="12" />新选题</button>
            </div>

            <div v-if="stageView === 'board'" class="board">
              <section v-for="column in columns" :key="column" class="board-column">
                <header><span><i :class="`status-${column}`" />{{ column }}</span><em>{{ topics.filter((topic) => topic.status === column).length }}</em></header>
                <div class="cards">
                  <button
                    v-for="topic in topics.filter((item) => item.status === column)"
                    :key="topic.id"
                    class="topic-card"
                    :class="{ selected: selected?.id === topic.id }"
                    type="button"
                    @click="selectTopic(topic)"
                  >
                    <strong>{{ topic.title }}</strong>
                    <span>{{ topic.note }}</span>
                    <small><YbIcon name="doc" :size="11" />{{ topic.sources }} 个素材</small>
                  </button>
                </div>
              </section>
            </div>

            <div v-else class="topic-list" aria-label="选题列表">
              <div class="topic-list-head"><span>选题</span><span>状态</span><span>素材</span></div>
              <button
                v-for="topic in topics"
                :key="topic.id"
                type="button"
                :class="{ selected: selected?.id === topic.id }"
                @click="selectTopic(topic)"
              >
                <span><strong>{{ topic.title }}</strong><small>{{ topic.note }}</small></span>
                <em><i :class="`status-${topic.status}`" />{{ topic.status }}</em>
                <small><YbIcon name="doc" :size="11" />{{ topic.sources }}</small>
              </button>
            </div>

            <footer class="stage-foot">
              <span v-if="selected"><YbIcon name="pin" :size="12" />已把「{{ selected.title }}」加入当前对话上下文</span>
              <span v-else>点一张卡片，译宝就知道你说的“这个”是什么</span>
              <button v-if="selected" type="button" @click="clearSelected">移出上下文</button>
            </footer>
          </div>
        </section>
      </Transition>

      <form class="composer-wrap" aria-label="共享命令栏" @submit.prevent="send">
        <div class="skill-row">
          <span>{{ isOpen ? "当前工作面" : "当前能力" }}</span>
          <button type="button" class="skill-chip active" @click="openSurface"><YbIcon name="plug" :size="11" />{{ isOpen ? "选题看板" : "自媒体" }}</button>
          <button v-if="!isOpen" type="button" class="skill-chip"><YbIcon name="search" :size="11" />查找资料</button>
          <span v-if="isOpen" class="command-hint">译宝会理解你正在看的对象</span>
        </div>
        <div class="composer">
          <span v-if="isOpen" class="assistant-target"><Avatar state="idle" :size="20" compact /><strong>对译宝</strong></span>
          <span v-if="scopeText" class="scope-chip" :title="scopeText"><YbIcon name="pin" :size="11" />{{ scopeText }}</span>
          <input ref="inputEl" v-model="draft" :placeholder="composerPlaceholder" />
          <button class="mic-button" type="button" aria-label="语音输入"><YbIcon name="mic" :size="14" /></button>
          <button class="send-button" type="submit" :disabled="!draft.trim()" aria-label="发送"><YbIcon name="sparkle" :size="14" /></button>
        </div>
      </form>
    </main>

    <div v-if="surface === 'loading'" class="loading-toast" role="status">
      <YbIcon name="spinner" :size="14" spin />
      <span><strong>自媒体</strong>正在准备选题工作面</span>
    </div>
    <div v-else-if="restoredToast" class="loading-toast restored-toast" role="status">
      <YbIcon name="check" :size="14" />
      <span><strong>已恢复协作现场</strong>{{ timeline.length }} 条记录 · {{ topics.length }} 个选题</span>
    </div>
  </div>
</template>

<style scoped>
* { box-sizing: border-box; }
button, input { font: inherit; }
button { color: inherit; }

.prototype-shell {
  height: 100vh;
  min-height: 680px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  color: var(--yb-text);
  background: var(--yb-content-bg);
  font-family: var(--yb-font);
  font-size: var(--yb-fs-lg);
}

.topbar {
  height: 58px;
  flex-shrink: 0;
  display: grid;
  grid-template-columns: minmax(170px, 1fr) auto minmax(260px, 1fr);
  align-items: center;
  gap: var(--yb-space-4);
  padding: 0 var(--yb-space-4);
  border-bottom: 1px solid var(--yb-border-base);
  background: linear-gradient(180deg, rgba(var(--yb-c-sky-rgb), 0.045), transparent), var(--yb-content-bg);
  user-select: none;
}
.traffic-space { display: none; }
.brand { display: flex; align-items: center; gap: 8px; font-weight: var(--yb-fw-bold); }
.brand img { width: 22px; height: 22px; }
.nav { display: flex; gap: 2px; }
.nav-item, .icon-button {
  border: none;
  background: transparent;
  border-radius: var(--yb-radius-sm);
  cursor: pointer;
}
.nav-item { display: flex; align-items: center; gap: 6px; padding: 6px 13px; color: var(--yb-text-dim); }
.nav-item.active { background: var(--yb-segment-thumb); color: var(--yb-text); box-shadow: var(--yb-shadow-1); }
.nav-item.active :deep(svg) { color: var(--yb-accent); }
.top-actions { display: flex; justify-content: flex-end; align-items: center; gap: 4px; min-width: 0; }
.icon-button { width: 30px; height: 30px; display: grid; place-items: center; color: var(--yb-text-dim); }
.icon-button:hover, .nav-item:hover { background: var(--yb-row-hover); color: var(--yb-text); }
.activity-pill {
  min-width: 0;
  max-width: 230px;
  height: 30px;
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 3px 10px 3px 4px;
  border: 1px solid var(--yb-border-base);
  border-radius: var(--yb-radius-pill);
  background: var(--yb-surface-1);
  color: var(--yb-text-dim);
  cursor: pointer;
  transition: all var(--yb-dur) var(--yb-ease-out);
}
.activity-pill > span:nth-child(2) { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.activity-pill.active { border-color: rgba(var(--yb-c-sky-rgb), .3); color: var(--yb-text); box-shadow: var(--yb-shadow-1); }
.activity-mark { width: 22px; height: 22px; display: grid; place-items: center; border-radius: 50%; background: var(--yb-accent-soft); color: var(--yb-accent-deep); }
.activity-pill > i { width: 6px; height: 6px; border-radius: 50%; background: var(--yb-text-faint); }
.activity-pill > i.live { background: var(--yb-intent-ok); box-shadow: 0 0 0 3px rgba(62, 142, 90, .12); }

.workspace {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 268px minmax(400px, 1fr) 280px;
  grid-template-rows: minmax(0, 1fr) auto;
  position: relative;
  overflow: hidden;
  transition: grid-template-columns 320ms var(--yb-ease-out);
}
.brain-column {
  grid-column: 1;
  grid-row: 1 / 3;
  width: auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 18px 14px;
  border-right: 1px solid transparent;
  background: radial-gradient(100% 40% at 50% 0%, rgba(var(--yb-c-sky-rgb), .07), transparent 75%), var(--yb-content-bg);
  position: relative;
}
.brain-column::after { content: ""; position: absolute; right: 0; top: 10%; bottom: 10%; width: 1px; background: linear-gradient(transparent, rgba(var(--yb-c-sky-rgb), .14), transparent); }
.brain-head { display: flex; align-items: center; gap: 12px; padding: 8px 8px 20px; }
.avatar-glow { width: 58px; height: 58px; display: grid; place-items: center; border-radius: 50%; background: radial-gradient(circle, var(--yb-accent-soft), transparent 70%); }
.brain-head > div:last-child { display: flex; flex-direction: column; gap: 4px; }
.brain-head strong { font-size: var(--yb-fs-xl); }
.brain-head span { display: flex; align-items: center; gap: 5px; color: var(--yb-text-dim); font-size: var(--yb-fs-sm); }
.brain-head span i, .quiet-state i, .saved i, .context-head em i { width: 6px; height: 6px; border-radius: 50%; background: var(--yb-intent-ok); }
.brain-block { padding: 0 6px 18px; }
.brain-block h2, .context-section h2 { margin: 0 0 8px; color: var(--yb-text-faint); font-size: var(--yb-fs-xs); font-weight: var(--yb-fw-bold); letter-spacing: .06em; }
.brain-row { width: 100%; display: flex; align-items: center; gap: 8px; padding: 7px 8px; border: none; background: transparent; border-radius: var(--yb-radius-sm); text-align: left; color: var(--yb-text-dim); cursor: pointer; }
.brain-row :deep(svg) { color: var(--yb-accent); }
.sessions { min-height: 0; flex: 1; padding: 14px 6px 0; border-top: 1px solid var(--yb-line); }
.section-title { display: flex; justify-content: space-between; align-items: center; padding: 0 6px 8px; color: var(--yb-text-faint); font-size: var(--yb-fs-xs); font-weight: var(--yb-fw-bold); letter-spacing: .05em; }
.section-title button { border: none; background: transparent; color: var(--yb-accent-deep); cursor: pointer; }
.session { width: 100%; display: flex; flex-direction: column; gap: 3px; padding: 10px; border: none; border-radius: var(--yb-radius-sm); background: transparent; text-align: left; cursor: pointer; }
.session strong { font-weight: var(--yb-fw-medium); }
.session span { color: var(--yb-text-faint); font-size: var(--yb-fs-sm); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.session.active { background: var(--yb-row-selected); }
.brain-rail-actions { display: none; }

.conversation {
  grid-column: 2;
  grid-row: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: var(--yb-content-bg);
  transition: opacity 220ms var(--yb-ease-out), transform 320ms var(--yb-ease-out);
}
.conversation-head { height: 68px; flex-shrink: 0; display: flex; align-items: center; justify-content: space-between; padding: 0 24px; border-bottom: 1px solid var(--yb-line); }
.conversation-head > div { display: flex; flex-direction: column; gap: 3px; }
.conversation-head span { color: var(--yb-text-faint); font-size: var(--yb-fs-xs); }
.conversation-head strong { font-size: var(--yb-fs-xl); }
.quiet-state { display: flex; align-items: center; gap: 6px; padding: 4px 9px; border-radius: var(--yb-radius-pill); background: var(--yb-surface-2); }
.message-list { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 12px; padding: 26px 30px 10px; }
.message { max-width: min(78%, 650px); line-height: 1.62; }
.message.user { align-self: flex-end; padding: 9px 13px; border-radius: var(--yb-radius-md) var(--yb-radius-md) var(--yb-radius-xs) var(--yb-radius-md); background: var(--yb-bubble-user); }
.message-row { display: flex; align-items: flex-end; gap: 8px; }
.message.ai { color: var(--yb-text); }
.timeline-message { width: 100%; flex-shrink: 0; }
.timeline-message.user-wrap { display: flex; flex-direction: column; align-items: flex-end; gap: 3px; }
.message-copy { min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.message-time { flex-shrink: 0; color: var(--yb-text-faint); font-size: 10px; font-weight: var(--yb-fw-regular); }
.timeline-tools { flex-shrink: 0; display: flex; align-items: center; gap: 6px; padding: 10px 14px 0; }
.timeline-tabs { flex: 1; display: flex; gap: 2px; padding: 2px; border-radius: var(--yb-radius-sm); background: var(--yb-segment-track); }
.timeline-tabs button { flex: 1; display: flex; align-items: center; justify-content: center; gap: 4px; padding: 4px 5px; border: none; border-radius: 7px; background: transparent; color: var(--yb-text-dim); font-size: var(--yb-fs-xs); cursor: pointer; }
.timeline-tabs button.active { background: var(--yb-segment-thumb); color: var(--yb-text); box-shadow: var(--yb-shadow-1); }
.timeline-tabs button span { color: var(--yb-text-faint); font-size: 10px; }
.timeline-reset { width: 26px; height: 26px; display: grid; place-items: center; border: none; border-radius: 7px; background: transparent; color: var(--yb-text-faint); cursor: pointer; }
.timeline-reset:hover { background: var(--yb-row-hover); color: var(--yb-text); }
.timeline-empty { padding: 22px 8px; color: var(--yb-text-faint); font-size: var(--yb-fs-sm); text-align: center; }
.activity-entry {
  align-self: center;
  flex-shrink: 0;
  width: min(520px, 92%);
  border: 1px solid rgba(var(--yb-c-sky-rgb), .18);
  border-radius: var(--yb-radius-md);
  background: linear-gradient(135deg, rgba(var(--yb-c-sky-rgb), .045), rgba(255,255,255,.94));
  box-shadow: var(--yb-shadow-1);
  overflow: hidden;
  transition: all var(--yb-dur) var(--yb-ease-out);
}
.activity-entry:hover { border-color: rgba(var(--yb-c-sky-rgb), .32); box-shadow: var(--yb-shadow-2); }
.activity-entry.status-running { border-color: rgba(var(--yb-c-sky-rgb), .34); }
.activity-entry.status-failed { border-color: rgba(var(--yb-c-red-rgb), .24); }
.activity-summary { width: 100%; display: flex; align-items: center; gap: 9px; padding: 10px 11px; border: none; background: transparent; text-align: left; cursor: pointer; }
.capability-icon { width: 28px; height: 28px; flex-shrink: 0; display: grid; place-items: center; border-radius: 9px; background: var(--yb-surface-1); color: var(--yb-intent-ok); box-shadow: var(--yb-shadow-1); }
.status-running .capability-icon { color: var(--yb-accent); }
.status-failed .capability-icon { color: var(--yb-danger); }
.capability-copy { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.capability-copy strong { font-weight: var(--yb-fw-medium); }
.capability-copy small { color: var(--yb-text-faint); font-size: var(--yb-fs-sm); }
.capability-copy small span { color: var(--yb-accent-deep); }
.activity-detail { margin: 0 11px 9px 48px; padding-top: 8px; border-top: 1px solid var(--yb-line); color: var(--yb-text-dim); font-size: var(--yb-fs-xs); }
.activity-detail span { display: flex; align-items: center; gap: 4px; margin-bottom: 5px; color: var(--yb-accent-deep); }
.activity-detail p { margin: 0; line-height: 1.5; }
.activity-open { margin: 0 11px 10px 48px; padding: 4px 0; border: none; background: transparent; color: var(--yb-accent-deep); font-size: var(--yb-fs-sm); cursor: pointer; }
.context-track-intro { margin: 14px 14px 0; padding: 11px 12px; border: 1px solid rgba(var(--yb-c-sky-rgb), .16); border-radius: var(--yb-radius-md); background: var(--yb-accent-soft); }
.context-track-intro > span { display: flex; align-items: center; gap: 5px; color: var(--yb-accent-deep); font-size: var(--yb-fs-sm); font-weight: var(--yb-fw-medium); }
.context-track-intro p { margin: 5px 0 0; color: var(--yb-text-dim); font-size: var(--yb-fs-xs); line-height: 1.45; }

.composer-wrap { grid-column: 2; grid-row: 2; min-width: 0; padding: 10px 24px 18px; background: var(--yb-content-bg); transition: padding 320ms var(--yb-ease-out), box-shadow 320ms var(--yb-ease-out); }
.skill-row { height: 28px; display: flex; align-items: center; gap: 6px; overflow: hidden; color: var(--yb-text-faint); font-size: var(--yb-fs-xs); }
.skill-chip { display: inline-flex; align-items: center; gap: 4px; padding: 3px 8px; border: 1px solid transparent; border-radius: var(--yb-radius-pill); background: transparent; color: var(--yb-text-dim); cursor: pointer; }
.skill-chip:hover, .skill-chip.active { background: var(--yb-accent-soft); color: var(--yb-accent-deep); }
.composer { min-height: 44px; display: flex; align-items: center; gap: 6px; padding: 5px 5px 5px 12px; border: 1px solid var(--yb-surface-border); border-radius: 22px; background: var(--yb-glass); box-shadow: var(--yb-shadow-2); }
.composer:focus-within { border-color: var(--yb-accent); outline: 2px solid var(--yb-accent-soft); outline-offset: 1px; }
.scope-chip { max-width: 190px; display: flex; align-items: center; gap: 4px; padding: 4px 8px; border-radius: var(--yb-radius-pill); background: var(--yb-accent-soft); color: var(--yb-accent-deep); font-size: var(--yb-fs-xs); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.scope-chip :deep(svg) { flex-shrink: 0; }
.assistant-target { flex-shrink: 0; display: flex; align-items: center; gap: 6px; padding-right: 9px; border-right: 1px solid var(--yb-line); font-size: var(--yb-fs-sm); }
.assistant-target strong { font-weight: var(--yb-fw-medium); }
.command-hint { margin-left: auto; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.composer input { flex: 1; min-width: 50px; border: none; outline: none; background: transparent; color: var(--yb-text); }
.mic-button, .send-button { width: 32px; height: 32px; flex-shrink: 0; display: grid; place-items: center; border: none; border-radius: 50%; cursor: pointer; }
.mic-button { background: transparent; color: var(--yb-text-dim); }
.send-button { background: var(--yb-accent); color: var(--yb-text-on-accent); }
.send-button:disabled { opacity: .35; }

.context-panel { grid-column: 3; grid-row: 1 / 3; width: auto; min-height: 0; overflow-y: auto; padding: 18px 16px; border-left: 1px solid transparent; position: relative; background: var(--yb-content-bg); }
.context-panel::before { content: ""; position: absolute; left: 0; top: 8%; bottom: 8%; width: 1px; background: linear-gradient(transparent, rgba(var(--yb-c-sky-rgb), .14), transparent); }
.context-head { display: flex; flex-direction: column; gap: 4px; padding: 0 4px 16px; }
.context-head > span { color: var(--yb-text-faint); font-size: var(--yb-fs-xs); font-weight: var(--yb-fw-bold); letter-spacing: .07em; }
.context-head strong { font-size: var(--yb-fs-xl); }
.context-head p { margin: 0; color: var(--yb-text-dim); font-size: var(--yb-fs-md); line-height: 1.5; }
.context-head em { width: fit-content; display: flex; align-items: center; gap: 5px; margin-top: 5px; padding: 4px 8px; border-radius: var(--yb-radius-pill); background: var(--yb-surface-2); color: var(--yb-text-dim); font-size: var(--yb-fs-xs); font-style: normal; }
.context-section { padding: 15px 4px; border-top: 1px solid var(--yb-line); }
.context-section h2 { display: flex; gap: 5px; }
.context-row { width: 100%; display: flex; align-items: center; gap: 7px; padding: 7px 6px; border: none; border-radius: var(--yb-radius-sm); background: transparent; text-align: left; cursor: pointer; }
.context-row:hover { background: var(--yb-row-hover); }
.context-row > span:nth-last-child(2) { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.context-row small { color: var(--yb-text-faint); font-size: var(--yb-fs-xs); }
.kind { width: 22px; height: 22px; display: grid; place-items: center; border-radius: 7px; background: var(--yb-surface-2); color: var(--yb-text-dim); font-size: 10px; }
.related-capability { width: 100%; display: flex; align-items: center; gap: 8px; padding: 9px; border: 1px solid rgba(var(--yb-c-sky-rgb), .18); border-radius: var(--yb-radius-md); background: var(--yb-surface-1); box-shadow: var(--yb-shadow-1); text-align: left; cursor: pointer; }
.related-icon { width: 30px; height: 30px; flex-shrink: 0; display: grid; place-items: center; border-radius: 9px; background: var(--yb-accent-soft); color: var(--yb-accent-deep); }
.related-capability > span:nth-child(2) { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.related-capability strong { font-weight: var(--yb-fw-medium); }
.related-capability small { color: var(--yb-text-faint); font-size: var(--yb-fs-xs); line-height: 1.35; }
.related-action { color: var(--yb-accent-deep); font-size: var(--yb-fs-xs); }

.surface-zone { grid-column: 3; grid-row: 1; min-width: 0; min-height: 0; display: flex; border-left: 1px solid var(--yb-border-base); background: var(--yb-card-page-bg); box-shadow: -12px 0 32px rgba(var(--yb-c-slate-rgb), .07); transform-origin: right center; }
.stage { flex: 1; min-width: 0; min-height: 0; display: flex; flex-direction: column; }
.stage-head { height: 68px; flex-shrink: 0; display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 0 16px; border-bottom: 1px solid var(--yb-border-base); background: var(--yb-content-bg); }
.stage-title { display: flex; align-items: center; gap: 10px; min-width: 0; }
.app-glyph { width: 34px; height: 34px; flex-shrink: 0; display: grid; place-items: center; border-radius: 10px; background: var(--yb-icon-bg-1); color: var(--yb-icon-fg-1); font-weight: var(--yb-fw-bold); }
.stage-title > div { min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.stage-title small { color: var(--yb-text-faint); font-size: var(--yb-fs-xs); }
.stage-title strong { font-size: var(--yb-fs-xl); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.stage-actions { display: flex; align-items: center; gap: 5px; }
.stage-actions button, .add-button { display: flex; align-items: center; gap: 5px; padding: 6px 8px; border: 1px solid var(--yb-border-base); border-radius: var(--yb-radius-sm); background: var(--yb-surface-1); cursor: pointer; }
.stage-actions button:hover, .add-button:hover { background: var(--yb-accent-soft); border-color: rgba(var(--yb-c-sky-rgb), .3); color: var(--yb-accent-deep); }
.saved { display: flex; align-items: center; gap: 5px; margin-right: 3px; color: var(--yb-text-faint); font-size: var(--yb-fs-xs); }
.stage-toolbar { height: 52px; flex-shrink: 0; display: flex; align-items: center; gap: 12px; padding: 0 16px; border-bottom: 1px solid var(--yb-border-base); }
.stage-toolbar > span { flex: 1; color: var(--yb-text-faint); font-size: var(--yb-fs-xs); }
.segmented { display: flex; padding: 2px; border-radius: var(--yb-radius-sm); background: var(--yb-segment-track); }
.segmented button { padding: 4px 10px; border: none; border-radius: 8px; background: transparent; color: var(--yb-text-dim); cursor: pointer; }
.segmented button.active { background: var(--yb-segment-thumb); color: var(--yb-text); box-shadow: var(--yb-shadow-1); }
.board { flex: 1; min-height: 0; display: grid; grid-template-columns: repeat(3, minmax(150px, 1fr)); gap: 10px; padding: 12px; overflow: auto; }
.board-column { min-width: 0; display: flex; flex-direction: column; gap: 8px; }
.board-column > header { height: 28px; display: flex; align-items: center; justify-content: space-between; padding: 0 4px; color: var(--yb-text-dim); font-size: var(--yb-fs-sm); }
.board-column > header span { display: flex; align-items: center; gap: 6px; }
.board-column > header i { width: 7px; height: 7px; border-radius: 50%; background: var(--yb-text-faint); }
.board-column > header i.status-想法 { background: var(--yb-accent); }
.board-column > header i.status-待验证 { background: var(--yb-intent-pending); }
.board-column > header i.status-写作中 { background: var(--yb-intent-ok); }
.board-column > header em { min-width: 20px; height: 18px; display: grid; place-items: center; border-radius: var(--yb-radius-pill); background: var(--yb-btn-neutral); color: var(--yb-text-faint); font-size: var(--yb-fs-xs); font-style: normal; }
.cards { display: flex; flex-direction: column; gap: 8px; }
.topic-card { width: 100%; display: flex; flex-direction: column; gap: 6px; padding: 11px; border: 1px solid var(--yb-card-border); border-radius: var(--yb-radius-md); background: var(--yb-card-bg); box-shadow: var(--yb-shadow-1); text-align: left; cursor: pointer; transition: all var(--yb-dur-fast) var(--yb-ease-out); }
.topic-card:hover { transform: translateY(-1px); box-shadow: var(--yb-shadow-2); border-color: rgba(var(--yb-c-sky-rgb), .28); }
.topic-card.selected { border-color: var(--yb-accent); background: linear-gradient(135deg, var(--yb-accent-soft), var(--yb-surface-1)); box-shadow: 0 0 0 2px rgba(var(--yb-c-sky-rgb), .12), var(--yb-shadow-2); }
.topic-card strong { font-size: var(--yb-fs-md); line-height: 1.45; }
.topic-card > span { color: var(--yb-text-dim); font-size: var(--yb-fs-sm); line-height: 1.4; }
.topic-card small { display: flex; align-items: center; gap: 4px; color: var(--yb-text-faint); font-size: var(--yb-fs-xs); }
.topic-list { flex: 1; min-height: 0; overflow: auto; padding: 12px 16px; }
.topic-list-head, .topic-list > button { display: grid; grid-template-columns: minmax(0, 1fr) 110px 58px; align-items: center; gap: 12px; }
.topic-list-head { padding: 0 12px 8px; color: var(--yb-text-faint); font-size: var(--yb-fs-xs); }
.topic-list > button { width: 100%; padding: 11px 12px; border: none; border-top: 1px solid var(--yb-line); background: transparent; text-align: left; cursor: pointer; }
.topic-list > button:hover { background: var(--yb-row-hover); }
.topic-list > button.selected { background: var(--yb-row-selected); }
.topic-list > button > span { min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.topic-list > button strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: var(--yb-fw-medium); }
.topic-list > button span small, .topic-list > button > small { color: var(--yb-text-faint); font-size: var(--yb-fs-xs); }
.topic-list > button em { display: flex; align-items: center; gap: 5px; color: var(--yb-text-dim); font-size: var(--yb-fs-xs); font-style: normal; }
.topic-list > button em i { width: 7px; height: 7px; border-radius: 50%; background: var(--yb-text-faint); }
.topic-list > button em i.status-想法 { background: var(--yb-accent); }
.topic-list > button em i.status-待验证 { background: var(--yb-intent-pending); }
.topic-list > button em i.status-写作中 { background: var(--yb-intent-ok); }
.topic-list > button > small { display: flex; align-items: center; justify-content: flex-end; gap: 4px; }
.stage-foot { min-height: 44px; flex-shrink: 0; display: flex; align-items: center; gap: 8px; padding: 8px 16px; border-top: 1px solid var(--yb-border-base); background: var(--yb-content-bg); color: var(--yb-text-dim); font-size: var(--yb-fs-sm); }
.stage-foot > span { flex: 1; min-width: 0; display: flex; align-items: center; gap: 5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.stage-foot button { border: none; background: transparent; color: var(--yb-accent-deep); cursor: pointer; }

.surface-open .workspace { grid-template-columns: 72px minmax(288px, 320px) minmax(520px, 1fr); }
.surface-open .brain-column { padding: 10px 8px; align-items: center; border-right-color: var(--yb-border-base); background: var(--yb-content-bg); }
.surface-open .brain-column::after { display: none; }
.surface-open .brain-head { padding: 2px 0 12px; }
.surface-open .avatar-glow { width: 52px; height: 52px; }
.surface-open .brain-head > div:last-child,
.surface-open .brain-block,
.surface-open .sessions { display: none; }
.surface-open .brain-rail-actions { width: 100%; min-height: 0; flex: 1; display: flex; flex-direction: column; align-items: center; gap: 6px; padding-top: 8px; border-top: 1px solid var(--yb-line); }
.surface-open .brain-rail-actions button { width: 38px; height: 38px; display: grid; place-items: center; border: none; border-radius: var(--yb-radius-sm); background: transparent; color: var(--yb-text-dim); cursor: pointer; }
.surface-open .brain-rail-actions button:hover { background: var(--yb-row-hover); color: var(--yb-text); }
.surface-open .brain-rail-actions button.active { background: var(--yb-accent-soft); color: var(--yb-accent-deep); }
.surface-open .brain-rail-actions > span { flex: 1; }
.surface-open .conversation { border-right: 1px solid var(--yb-border-base); background: rgba(255,255,255,.72); }
.surface-open .conversation-head { height: 64px; padding: 0 15px; }
.surface-open .conversation-head strong { font-size: var(--yb-fs-lg); }
.surface-open .quiet-state { padding: 4px 7px; }
.surface-open .message-list { padding: 15px 14px 12px; gap: 11px; }
.surface-open .message { max-width: 100%; font-size: var(--yb-fs-sm); line-height: 1.5; }
.surface-open .message.user { padding: 8px 10px; }
.surface-open .message-row { align-items: flex-start; gap: 6px; }
.surface-open .activity-entry { width: 100%; }
.surface-open .activity-summary { padding: 9px; }
.surface-open .capability-copy small { white-space: normal; }
.surface-open .activity-detail { margin-left: 46px; }
.surface-open .composer-wrap { grid-column: 2 / 4; padding: 8px 24px 14px; border-top: 1px solid var(--yb-border-base); background: rgba(255,255,255,.94); box-shadow: 0 -12px 30px rgba(var(--yb-c-slate-rgb), .045); z-index: 5; }
.surface-open .composer { min-height: 48px; border-color: rgba(var(--yb-c-sky-rgb), .28); box-shadow: var(--yb-shadow-3); }
.surface-focus .workspace { grid-template-columns: 72px 0 minmax(0, 1fr); }
.surface-focus .conversation { visibility: hidden; opacity: 0; transform: translateX(-16px); pointer-events: none; overflow: hidden; }

.surface-enter-active, .surface-leave-active { transition: opacity var(--yb-dur-slow) var(--yb-ease-out), transform var(--yb-dur-slow) var(--yb-ease-out); }
.surface-enter-from, .surface-leave-to { opacity: 0; transform: translateX(28px) scale(.985); }
.loading-toast { position: fixed; right: 24px; bottom: 24px; z-index: 20; display: flex; align-items: center; gap: 9px; padding: 10px 13px; border: 1px solid var(--yb-border-base); border-radius: var(--yb-radius-md); background: var(--yb-glass); box-shadow: var(--yb-shadow-3); color: var(--yb-text-dim); }
.loading-toast :deep(svg) { color: var(--yb-accent); }
.loading-toast strong { color: var(--yb-text); margin-right: 4px; }
.restored-toast { right: 24px; bottom: 24px; }

@media (max-width: 1180px) {
  .workspace { grid-template-columns: minmax(0, 1fr) 280px; }
  .brain-column { display: none; }
  .conversation { grid-column: 1; }
  .context-panel { grid-column: 2; }
  .composer-wrap { grid-column: 1; }
  .surface-open .workspace { grid-template-columns: 64px minmax(276px, 300px) minmax(480px, 1fr); }
  .surface-open .brain-column { display: flex; grid-column: 1; }
  .surface-open .conversation { grid-column: 2; }
  .surface-open .surface-zone { grid-column: 3; }
  .surface-open .composer-wrap { grid-column: 2 / 4; }
}
@media (max-width: 980px) {
  .surface-open .workspace,
  .surface-focus .workspace { grid-template-columns: 64px 0 minmax(0, 1fr); }
  .surface-open .conversation { visibility: hidden; opacity: 0; transform: translateX(-16px); pointer-events: none; overflow: hidden; }
  .surface-open .surface-zone { grid-column: 3; }
  .surface-open .composer-wrap { grid-column: 2 / 4; padding-inline: 16px; }
}
@media (max-width: 860px) {
  .topbar { grid-template-columns: 1fr auto; }
  .nav { display: none; }
  .workspace { grid-template-columns: minmax(0, 1fr); }
  .context-panel { display: none; }
  .conversation { grid-column: 1; }
  .composer-wrap { grid-column: 1; }
  .surface-open .workspace,
  .surface-focus .workspace { grid-template-columns: 58px 0 minmax(0, 1fr); }
  .surface-open .brain-column { padding-inline: 5px; }
  .surface-open .composer-wrap { grid-column: 2 / 4; padding-inline: 12px; }
  .stage-actions .saved { display: none; }
  .stage-actions button { padding-inline: 7px; }
  .command-hint { display: none; }
}
</style>
