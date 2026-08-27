<script setup lang="ts">
import { computed, inject } from "vue";
import Avatar from "../../components/pet/Avatar.vue";
import Bubble from "../../components/common/Bubble.vue";
import YbIcon from "../../components/common/YbIcon.vue";
import UsageBar from "../../components/common/UsageBar.vue";
import { HOME_CHAT_SESSION } from "../../lib/home/home-chat-session.ts";
import { isDeskPathOpenLine } from "../../lib/home/home-desk-presence.ts";
import { actionsOf, ACTION_DEFS, type MsgAction } from "../../lib/msg-actions";
import { runTailIndex } from "../../lib/work-thread";

const chat = inject(HOME_CHAT_SESSION);
if (!chat) throw new Error("HomeChatThread needs a chat session");
const {
  bubbles,
  thread,
  state,
  sessionTitle,
  processes,
  greeting,
  suggestChips,
  showTyping,
  streamingIdx,
  thinkNote,
  showJump,
  bubblesRef,
  threadKey,
  livePathLine,
  submit,
  fmtDay,
  openPanel,
  procOk,
  procErrSuffix,
  procText,
  runRefsOf,
  toggleRunRefs,
  runShowFooter,
  runMetricsOf,
  runHalted,
  copyRun,
  copyText,
  onFeedback,
  regenerate,
  onEditMessage,
  onBubblesScroll,
  scrollBubbles,
} = chat;

const latestRequest = computed(() => {
  const row = [...bubbles.value].reverse().find((bubble) => bubble.role === "user");
  return row?.text.trim() || "等待你给出下一步目标";
});
const activeProcesses = computed(() => processes.value.filter((item) => !item.done).slice(0, 2));
const doneProcesses = computed(() => processes.value.filter((item) => item.done && item.ok !== false).slice(-3).reverse());
const completedRuns = computed(() => thread.value.filter((item) => item.type === "run").length);
const missionState = computed(() => {
  if (state.value === "think") return "正在思考";
  if (state.value === "work") return "正在执行";
  if (state.value === "listen") return "正在接收";
  if (state.value === "say") return "正在整理结果";
  if (state.value === "error") return "需要留意";
  if (state.value === "success") return "刚刚完成";
  return "等待下一步";
});
const fallbackAction = computed(() => {
  if (state.value === "think") return "梳理问题与相关上下文";
  if (state.value === "work") return "连接能力并执行任务";
  if (state.value === "say") return "收束结果并准备回复";
  return "等待你的下一步指令";
});

// ---- 消息操作（策略层驱动）：按钮由 actionsOf(role) 决定，只做渲染与分发，不写条件 ----
/** run 组底部的操作：固定为 AI 回答的能力（复制/反馈/重写）。 */
const runActions = actionsOf("ai");
function actionLabel(a: MsgAction): string | undefined {
  return ACTION_DEFS[a].label;
}
function userActions(i: number): MsgAction[] {
  return actionsOf(bubbles.value[i]?.role ?? "sys");
}
function dispatchRunAction(a: MsgAction, indices: number[]) {
  switch (a) {
    case "copy":
      copyRun(indices);
      break;
    case "regenerate":
      regenerate(runTailIndex(bubbles.value, indices));
      break;
    case "edit": // 策略表当前不给 ai 配 edit，防御性兜底
    case "feedback":
      break;
  }
}
function dispatchUserAction(a: MsgAction, i: number) {
  switch (a) {
    case "copy":
      copyText(bubbles.value[i].text);
      break;
    case "edit":
      onEditMessage(i);
      break;
  }
}
</script>

<template>
  <div class="surface-thread">
    <div class="bubbles" ref="bubblesRef" @scroll="onBubblesScroll">
      <section v-if="bubbles.length || showTyping" class="mission-board" aria-labelledby="mission-title">
        <header class="mission-head">
          <div>
            <span class="mission-kicker">本次任务</span>
            <h2 id="mission-title">{{ sessionTitle || "新对话" }}</h2>
          </div>
          <span class="mission-state" :class="`state-${state}`"><i />{{ missionState }}</span>
        </header>
        <p class="mission-request">{{ latestRequest }}</p>
        <div class="mission-columns">
          <section>
            <h3>正在做</h3>
            <ul>
              <li v-for="item in activeProcesses" :key="item.label"><YbIcon name="spinner" :size="11" spin />{{ item.label }}</li>
              <li v-if="!activeProcesses.length"><i class="mission-node" />{{ fallbackAction }}</li>
            </ul>
          </section>
          <section>
            <h3>已完成</h3>
            <ul>
              <li v-for="item in doneProcesses" :key="item.label"><YbIcon name="check" :size="11" />{{ item.label }}</li>
              <li v-if="!doneProcesses.length"><YbIcon name="check" :size="11" />已完成 {{ completedRuns }} 轮协作</li>
            </ul>
          </section>
        </div>
      </section>
      <div v-if="!bubbles.length && !showTyping" class="empty-hint">
        <div class="eh-glow"><Avatar :state="state" :size="64" /></div>
        <p class="eh-title">{{ greeting }}</p>
        <p class="eh-sub">输入一条，或从下面开始</p>
        <div class="chips">
          <button v-for="c in suggestChips" :key="c.text" class="chip" @click="submit(c.text)">
            <YbIcon :name="c.icon" :size="12" />{{ c.text }}
          </button>
        </div>
      </div>
      <div v-if="bubbles.length || showTyping" class="record-head"><span>协作记录</span><small>{{ bubbles.length }} 条</small></div>
      <template v-for="item in thread" :key="threadKey(item)">
        <div v-if="item.type === 'day'" class="date-divider"><span>{{ fmtDay(bubbles[item.index].ts) }}</span></div>

        <button
          v-else-if="item.type === 'misc' && isDeskPathOpenLine(bubbles[item.index].text)"
          class="path-print"
          :class="{ live: bubbles[item.index].text === livePathLine }"
          @click="openPanel"
        >
          {{ bubbles[item.index].text }}
        </button>
        <button v-else-if="item.type === 'misc' && bubbles[item.index].panelLink" class="assoc" @click="openPanel">
          {{ bubbles[item.index].text }}<span class="assoc-arrow">展开 ›</span>
        </button>

        <div v-else-if="item.type === 'run'" class="work-run">
          <Avatar class="work-run-ava" :state="state" :size="22" compact />
          <div class="work-run-body">
            <template v-for="i in item.indices" :key="i">
              <div v-if="bubbles[i].proc" class="work-proc">
                <button
                  type="button"
                  class="work-proc-row"
                  :class="{ done: bubbles[i].proc.done && procOk(bubbles[i].proc), fail: bubbles[i].proc.done && !procOk(bubbles[i].proc) }"
                  :aria-expanded="bubbles[i].proc.expanded"
                  @click="bubbles[i].proc && (bubbles[i].proc.expanded = !bubbles[i].proc.expanded)"
                >
                  <YbIcon
                    class="proc-ic"
                    :name="bubbles[i].proc.done ? (procOk(bubbles[i].proc) ? 'check' : 'x') : 'spinner'"
                    :spin="!bubbles[i].proc.done"
                    :size="12"
                  />
                  <span class="proc-label">{{ bubbles[i].proc.label }}{{ bubbles[i].proc.done ? procErrSuffix(bubbles[i].proc) : "" }}</span>
                  <span class="proc-toggle">{{ bubbles[i].proc.expanded ? "收起" : "详情" }}</span>
                </button>
                <span v-if="!bubbles[i].proc.done" class="proc-track"><i /></span>
                <pre v-if="bubbles[i].proc.expanded" class="proc-detail">{{ procText(bubbles[i].proc) }}</pre>
              </div>
              <Bubble
                v-else
                :role="bubbles[i].role"
                :text="bubbles[i].text"
                plain
                :streaming="i === streamingIdx"
                :halted="bubbles[i].halted"
                :icon="bubbles[i].icon"
              />
            </template>

            <div v-if="runRefsOf(item.indices)" class="refs">
              <button class="refs-toggle" @click="toggleRunRefs(item.indices)">
                <span>参考了 {{ runRefsOf(item.indices)?.refs?.length }} 项</span>
                <i :class="{ open: runRefsOf(item.indices)?.refsOpen }" />
              </button>
              <Transition name="refs-fade">
                <ul v-if="runRefsOf(item.indices)?.refsOpen" class="refs-list">
                  <li v-for="(r, ri) in runRefsOf(item.indices)?.refs" :key="ri" :class="{ fail: !r.ok }">
                    <YbIcon :name="r.ok ? 'check' : 'x'" :size="10" />
                    <span class="refs-label">{{ r.label }}</span>
                    <span class="refs-detail">{{ r.detail }}</span>
                  </li>
                </ul>
              </Transition>
            </div>

            <div v-if="runShowFooter(item.indices)" class="msg-meta">
              <UsageBar v-if="runMetricsOf(item.indices)" :metrics="runMetricsOf(item.indices)!" />
              <i v-if="runMetricsOf(item.indices)" class="msg-meta-rule" aria-hidden="true" />
              <div v-if="runActions.length" class="msg-actions">
                <template v-for="a in runActions" :key="a">
                  <template v-if="a === 'feedback'">
                    <button title="有帮助" @click="onFeedback(true)"><YbIcon name="thumb-up" :size="12" /></button>
                    <button title="没帮助" @click="onFeedback(false)"><YbIcon name="thumb-down" :size="12" /></button>
                  </template>
                  <button v-else @click="dispatchRunAction(a, item.indices)">
                    {{ a === "regenerate" && runHalted(item.indices) ? "重试" : actionLabel(a) }}
                  </button>
                </template>
              </div>
            </div>
          </div>
        </div>

        <div v-else-if="item.type === 'user'" class="msg-row user-msg">
          <Bubble :role="bubbles[item.index].role" :text="bubbles[item.index].text" :streaming="item.index === streamingIdx" :halted="bubbles[item.index].halted" :icon="bubbles[item.index].icon" />
          <div v-if="userActions(item.index).length" class="msg-actions">
            <button v-for="a in userActions(item.index)" :key="a" @click="dispatchUserAction(a, item.index)">
              {{ actionLabel(a) }}
            </button>
          </div>
        </div>

        <Bubble
          v-else-if="item.type === 'misc'"
          :role="bubbles[item.index].role"
          :text="bubbles[item.index].text"
          :streaming="item.index === streamingIdx"
          :halted="bubbles[item.index].halted"
          :icon="bubbles[item.index].icon"
        />
      </template>

      <template v-if="showTyping">
        <div class="ai-line">
          <Avatar class="ai-ava" :state="state" :size="22" compact />
          <Bubble role="ai" text="" typing />
          <span class="think-note">{{ thinkNote }}</span>
        </div>
      </template>
    </div>
    <button v-show="showJump" class="jump-new" @click="scrollBubbles(true)">↓ 最新</button>
  </div>
</template>

<style scoped src="./home-chat-faces.css"></style>
<style scoped>
.surface-thread {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  position: relative;
}
.bubbles {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: var(--yb-space-3);
  overflow-y: auto;
  padding: var(--yb-space-3) var(--yb-space-5) var(--yb-space-4);
  scrollbar-width: thin;
  mask-image: linear-gradient(180deg, transparent, #000 14px);
  -webkit-mask-image: linear-gradient(180deg, transparent, #000 14px);
}
.mission-board {
  box-sizing: border-box;
  width: min(100%, 780px);
  align-self: center;
  padding: 18px 20px 16px;
  border: 1px solid var(--yb-widget-border);
  border-radius: calc(var(--yb-widget-radius) + 2px);
  background: var(--yb-widget-bg);
  box-shadow: var(--yb-glaze-hi), var(--yb-shadow-1);
}
.mission-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.mission-kicker,
.mission-columns h3,
.record-head {
  color: var(--yb-text-faint);
  font-size: var(--yb-fs-xs);
  font-weight: var(--yb-fw-medium);
  letter-spacing: var(--yb-kicker-track);
}
.mission-head h2 {
  margin: 4px 0 0;
  color: var(--yb-text-strong);
  font-size: 17px;
  font-weight: var(--yb-fw-bold);
  line-height: 1.35;
}
.mission-state {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 24px;
  padding: 0 9px;
  border: 1px solid var(--yb-widget-border);
  border-radius: var(--yb-radius-pill);
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-xs);
}
.mission-state i,
.mission-node {
  width: 6px;
  height: 6px;
  flex: none;
  border-radius: 50%;
  background: var(--yb-state-idle);
}
.mission-state.state-think i,
.mission-state.state-work i,
.mission-state.state-say i { background: var(--yb-accent); }
.mission-state.state-error i { background: var(--yb-danger); }
.mission-state.state-success i { background: var(--yb-intent-ok); }
.mission-request {
  margin: 12px 0 14px;
  color: var(--yb-text);
  font-size: var(--yb-fs-md);
  line-height: 1.55;
  overflow-wrap: anywhere;
}
.mission-columns {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  padding-top: 13px;
  border-top: 1px solid var(--yb-line);
}
.mission-columns section { min-width: 0; }
.mission-columns h3 { margin: 0 0 7px; }
.mission-columns ul {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.mission-columns li {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 7px;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-sm);
  line-height: 1.4;
}
.mission-columns li :deep(svg) { flex: none; color: var(--yb-intent-ok); }
.record-head {
  width: min(100%, 780px);
  align-self: center;
  display: flex;
  justify-content: space-between;
  padding: 4px 2px 0;
}
.record-head small {
  font: inherit;
  letter-spacing: normal;
}
@media (max-width: 760px) {
  .mission-board { padding: 15px; }
  .mission-columns { grid-template-columns: minmax(0, 1fr); }
}
.bubbles :deep(.bubble) {
  max-width: min(88%, 720px);
}
.bubbles :deep(.bubble.icon-clock) {
  align-self: center;
  max-width: min(100%, 36em);
}
.bubbles :deep(.bubble.plain) {
  max-width: min(100%, 760px);
}
.bubbles .work-run {
  max-width: min(100%, 760px);
}
.empty-hint {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-lg);
}
.eh-glow {
  width: 132px;
  height: 132px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: radial-gradient(75% 75% at 50% 35%, var(--yb-accent-soft), rgba(var(--yb-c-sky-rgb), 0) 72%);
  margin-bottom: 8px;
}
.eh-title {
  margin: 0;
  font-size: 22px;
  font-weight: var(--yb-fw-bold);
  letter-spacing: -0.01em;
  color: var(--yb-text-strong);
}
.eh-sub {
  margin: 0 0 8px;
  font-size: var(--yb-fs-md);
  color: var(--yb-text-dim);
}
.date-divider {
  align-self: center;
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 6px 0 2px;
  color: var(--yb-text-faint);
  font-size: var(--yb-fs-xs);
}
.date-divider::before,
.date-divider::after {
  content: "";
  width: 36px;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--yb-line));
}
.date-divider::after {
  background: linear-gradient(90deg, var(--yb-line), transparent);
}
.msg-row {
  position: relative;
  max-width: 100%;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
}
.user-msg {
  align-self: flex-end;
  align-items: flex-end;
}
.msg-row:hover .msg-actions,
.msg-row:focus-within .msg-actions {
  opacity: 1;
  pointer-events: auto;
  transform: none;
}
.user-msg .msg-actions {
  margin-left: 0;
}
.ai-line {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  align-self: flex-start;
  max-width: 100%;
}
.ai-ava {
  flex-shrink: 0;
  margin-bottom: 2px;
  opacity: 0.92;
}
</style>
