<script setup lang="ts">
import { inject } from "vue";
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
.bubbles :deep(.bubble) {
  max-width: min(88%, var(--yb-bubble-max, 720px)); /* 装配按摊法下传行长上限（field=420） */
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
