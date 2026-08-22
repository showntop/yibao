<script setup lang="ts">
import { inject } from "vue";
import Bubble from "../../components/common/Bubble.vue";
import HomePaper from "../HomePaper.vue";
import YbIcon from "../../components/common/YbIcon.vue";
import { HOME_CHAT_SESSION } from "../../lib/home/home-chat-session.ts";
import { isDeskPathOpenLine } from "../../lib/home/home-desk-presence.ts";
import { runTailIndex } from "../../lib/work-thread";

const chat = inject(HOME_CHAT_SESSION);
if (!chat) throw new Error("HomeChatPaper needs a chat session");
const {
  bubbles,
  greeting,
  suggestChips,
  showTyping,
  streamingIdx,
  thinkNote,
  showJump,
  bubblesRef,
  pages,
  pageIndex,
  page,
  paperEmpty,
  paperDuty,
  paperTitle,
  paperLabel,
  stampLabels,
  peekOpen,
  livePathLine,
  submit,
  openPanel,
  procOk,
  procErrSuffix,
  procText,
  paperShowProc,
  runRefsOf,
  toggleRunRefs,
  runShowFooter,
  runHalted,
  copyText,
  regenerate,
  onEditMessage,
  onBubblesScroll,
  scrollBubbles,
  flipPage,
  noticeFor,
} = chat;

/** 整页文本：用户的话 + 一轮工作 + 杂项，按序拼接。 */
function pageText(): string {
  const p = page.value;
  if (!p) return "";
  const parts: string[] = [];
  const push = (i: number) => {
    const t = bubbles.value[i]?.text;
    if (t) parts.push(t);
  };
  if (p.userIndex !== null) push(p.userIndex);
  for (const i of p.runIndices) push(i);
  for (const i of p.miscIndices) push(i);
  return parts.join("\n\n");
}

/** 复制这一页（用户问题 + 回答）。 */
function copyPage() {
  copyText(pageText());
}

/** 重发这一页的用户问题（走正常提交，会截断其后内容由大脑重跑）。 */
function resendPage() {
  const p = page.value;
  if (!p || p.userIndex === null) return;
  const text = bubbles.value[p.userIndex]?.text;
  if (text) void submit(text);
}
</script>

<template>
  <div class="paper-wrap">
    <HomePaper
      :empty="paperEmpty"
      :duty="paperDuty"
      :title="paperTitle"
      :page-label="paperLabel"
      :can-prev="pageIndex > 0"
      :can-next="pageIndex < pages.length - 1"
      :peek-open="peekOpen"
      :greeting="greeting"
      @prev="flipPage(-1)"
      @next="flipPage(1)"
      @toggle-peek="peekOpen = !peekOpen"
    >
      <template #chips>
        <button v-for="c in suggestChips" :key="c.text" class="chip" @click="submit(c.text)">
          <YbIcon :name="c.icon" :size="12" />{{ c.text }}
        </button>
      </template>
      <template v-if="page && page.userIndex !== null" #title-actions>
        <div class="msg-actions on">
          <button @click="copyText(paperTitle)">复制</button>
          <button @click="onEditMessage(page.userIndex)">编辑</button>
        </div>
      </template>
      <template v-if="stampLabels.length" #stamps>
        <span v-for="(label, si) in stampLabels" :key="si">{{ label }}</span>
      </template>
      <template v-if="page" #foot-actions>
        <button type="button" @click="copyPage">复制</button>
        <button v-if="page.userIndex !== null" type="button" @click="resendPage">重发</button>
        <button
          v-if="page.runIndices.length && runShowFooter(page.runIndices)"
          type="button"
          @click="regenerate(runTailIndex(bubbles, page.runIndices))"
        >
          {{ runHalted(page.runIndices) ? "重试" : "重写" }}
        </button>
      </template>

      <div v-if="!paperEmpty || showTyping" class="paper-stream" ref="bubblesRef" @scroll="onBubblesScroll">
        <div v-if="page && page.runIndices.length" class="work-run">
          <div class="work-run-body">
            <template v-for="i in page.runIndices" :key="i">
              <div v-if="bubbles[i].proc && paperShowProc(bubbles[i].proc)" class="work-proc">
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
              <template v-else-if="bubbles[i].halted">
                <Bubble
                  v-if="bubbles[i].text && bubbles[i].text !== '已打断'"
                  :role="bubbles[i].role"
                  :text="bubbles[i].text"
                  plain
                  :streaming="i === streamingIdx"
                />
                <p class="paper-halted">已打断</p>
              </template>
              <details v-else-if="noticeFor(bubbles[i])" class="paper-notice">
                <summary>{{ noticeFor(bubbles[i])?.summary }}</summary>
                <pre>{{ noticeFor(bubbles[i])?.detail }}</pre>
              </details>
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

            <div v-if="runRefsOf(page.runIndices)" class="refs">
              <button class="refs-toggle" @click="toggleRunRefs(page.runIndices)">
                <span>参考了 {{ runRefsOf(page.runIndices)?.refs?.length }} 项</span>
                <i :class="{ open: runRefsOf(page.runIndices)?.refsOpen }" />
              </button>
              <Transition name="refs-fade">
                <ul v-if="runRefsOf(page.runIndices)?.refsOpen" class="refs-list">
                  <li v-for="(r, ri) in runRefsOf(page.runIndices)?.refs" :key="ri" :class="{ fail: !r.ok }">
                    <YbIcon :name="r.ok ? 'check' : 'x'" :size="10" />
                    <span class="refs-label">{{ r.label }}</span>
                    <span class="refs-detail">{{ r.detail }}</span>
                  </li>
                </ul>
              </Transition>
            </div>
          </div>
        </div>

        <template v-for="i in page?.miscIndices ?? []" :key="`misc-${i}`">
          <button
            v-if="isDeskPathOpenLine(bubbles[i].text)"
            class="path-print"
            :class="{ live: bubbles[i].text === livePathLine }"
            @click="openPanel"
          >
            {{ bubbles[i].text }}
          </button>
          <button v-else-if="bubbles[i].panelLink" class="assoc" @click="openPanel">
            {{ bubbles[i].text }}<span class="assoc-arrow">展开 ›</span>
          </button>
          <template v-else-if="bubbles[i].halted">
            <Bubble
              v-if="bubbles[i].text && bubbles[i].text !== '已打断'"
              :role="bubbles[i].role"
              :text="bubbles[i].text"
              plain
              :streaming="i === streamingIdx"
            />
            <p class="paper-halted">已打断</p>
          </template>
          <details v-else-if="noticeFor(bubbles[i])" class="paper-notice">
            <summary>{{ noticeFor(bubbles[i])?.summary }}</summary>
            <pre>{{ noticeFor(bubbles[i])?.detail }}</pre>
          </details>
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

        <template v-if="showTyping && (paperEmpty || pageIndex === pages.length - 1)">
          <p class="paper-halted">{{ thinkNote }}</p>
        </template>
      </div>
    </HomePaper>
    <button v-show="showJump" class="jump-new" @click="scrollBubbles(true)">↓ 最新</button>
  </div>
</template>

<style scoped src="./home-chat-faces.css"></style>
<style scoped>
.paper-wrap {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  position: relative;
  overflow: hidden;
}
/* 纸张撑满工位高度：纵向布局下 flex:1 为主轴（高度），不再依赖 height:100% 的百分比解析。
   内容超高在 .paper-stream 内部滚动，footer 永远是纸张底部最后一个 flex 子，不会被顶出。 */
.paper-wrap > :deep(.sheet) {
  flex: 1;
  min-height: 0;
  width: auto;
  height: auto;
  overflow: hidden;
}
.paper-stream {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 0;
  overflow: auto;
  scrollbar-width: thin;
}
.paper-stream :deep(.bubble) {
  max-width: 100%;
}
.paper-stream :deep(.bubble.icon-clock) {
  align-self: center;
  max-width: min(100%, 36em);
}
.paper-stream :deep(.bubble.plain) {
  max-width: 100%;
}
.paper-halted {
  margin: 0;
  color: var(--yb-text-faint);
  font-size: 13px;
}
.paper-notice {
  margin: 0;
  color: var(--yb-text);
  font-size: 13px;
}
.paper-notice summary {
  cursor: pointer;
  color: var(--yb-text);
}
.paper-notice pre {
  margin: 8px 0 0;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--yb-text-faint);
  font: inherit;
  font-size: 12px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
}
.paper-stream .work-run {
  display: block;
}
.paper-stream .work-proc-row {
  color: var(--yb-text-faint);
  font-size: 12px;
}
.paper-stream .proc-toggle {
  opacity: 0;
}
.paper-stream .work-proc-row:hover .proc-toggle,
.paper-stream .work-proc-row:focus-visible .proc-toggle {
  opacity: 1;
}
.paper-stream :deep(.bubble.plain) {
  max-width: min(100%, 42em);
  font-size: 14px;
  line-height: 1.75;
  width: 100%;
  animation: none;
}
</style>
