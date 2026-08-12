<script setup lang="ts">
import { ref } from "vue";
import YbIcon from "./YbIcon.vue";
import { sessionStore } from "../state/store";
import type { ConversationMeta } from "../state/types";

export interface SessionMeta {
  id: string;
  title: string;
  preview: string;
  updatedAt: number;
}

// 会话列表/活动会话：权威在 SessionStore.conversation，本组件只做 UI 投影
const sessions = ref<SessionMeta[]>([]);
const activeId = ref(sessionStore.conversation.getActiveConversationId() ?? "");

const emit = defineEmits<{
  select: [id: string];
  newChat: [];
  active: [id: string];
}>();

/** 从 domain 拉取列表投影（updatedAt 倒序）；会话变化后由 sync 刷新 */
function sync(): void {
  sessions.value = sessionStore.conversation.listConversations().map((m: ConversationMeta) => ({
    id: m.id,
    title: m.title,
    preview: m.preview,
    updatedAt: m.updatedAt,
  }));
  activeId.value = sessionStore.conversation.getActiveConversationId() ?? "";
}

// 挂载时 hydrate + 首次投影（domain 由 HomeChat/App 的 restore 触发；这里兜底再拉一次）
void sessionStore.restore().catch(() => {}).then(sync);

function updateCurrent(partial: { title?: string; preview?: string }) {
  const id = activeId.value;
  if (!id) return;
  if (partial.title !== undefined) sessionStore.conversation.updateMetaTitle(id, partial.title);
  sync();
}

async function newChat() {
  const meta = await sessionStore.conversation.createConversation();
  await sessionStore.conversation.setActiveConversationId(meta.id);
  sync();
  emit("newChat");
  emit("active", meta.id);
}

function select(session: SessionMeta) {
  if (session.id === activeId.value) return;
  activeId.value = session.id;
  void sessionStore.conversation.setActiveConversationId(session.id);
  emit("select", session.id);
  emit("active", session.id);
}

async function remove(id: string) {
  const wasActive = activeId.value === id;
  await sessionStore.conversation.removeConversation(id);
  sync();
  if (wasActive) {
    const next = sessions.value[0];
    if (next) {
      activeId.value = next.id;
      void sessionStore.conversation.setActiveConversationId(next.id);
    }
    emit("active", activeId.value);
  }
}

defineExpose({ updateCurrent, newChat, sessions, sync });


function fmtTime(ts: number): string {
  const date = new Date(ts);
  const now = new Date();
  const pad = (value: number) => String(value).padStart(2, "0");
  if (date.toDateString() === now.toDateString()) return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (date.toDateString() === yesterday.toDateString()) return "昨天";
  if (date.getFullYear() === now.getFullYear()) return `${date.getMonth() + 1}/${date.getDate()}`;
  return `${date.getFullYear()}/${date.getMonth() + 1}/${date.getDate()}`;
}
</script>

<template>
  <aside class="session">
    <header class="echo-head">
      <div>
        <span class="echo-kicker">会话</span>
        <span class="echo-count">{{ sessions.length ? `${sessions.length} 条` : "暂无" }}</span>
      </div>
      <button class="echo-new" type="button" title="开始一段新对话" aria-label="开始一段新对话" @click="newChat">
        <span>新对话</span>
      </button>
    </header>

    <div class="echo-list" role="list" aria-label="最近会话">
      <div v-for="session in sessions" :key="session.id" class="echo-row" :class="{ active: session.id === activeId }" role="listitem">
        <button class="echo-main" type="button" :title="session.title" @click="select(session)">
          <span class="echo-line1">
            <strong>{{ session.title }}</strong>
            <time>{{ fmtTime(session.updatedAt) }}</time>
          </span>
          <span v-if="session.preview" class="echo-preview">{{ session.preview }}</span>
          <span v-else class="echo-preview quiet">暂无内容</span>
        </button>
        <button class="echo-delete" type="button" title="删除这段会话" aria-label="删除这段会话" @click="remove(session.id)">
          <YbIcon name="x" :size="10" />
        </button>
      </div>

      <button v-if="!sessions.length" class="echo-empty" type="button" @click="newChat">
        <span class="empty-ripple"><i /></span>
        <strong>暂无会话</strong>
        <span>开始一段新的对话</span>
      </button>
    </div>
  </aside>
</template>

<style scoped>
.session {
  width: 100%;
  box-sizing: border-box;
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  position: relative;
  /* 整栏滚动，echo-head sticky 才能压住下方列表 */
  overflow-y: auto;
  scrollbar-width: thin;
}

button { font: inherit; }

.echo-head {
  flex: none;
  position: sticky;
  top: 0;
  z-index: 2;
  padding: 8px 10px 6px 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  background: var(--yb-paper-sticky);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
}

.echo-head > div {
  min-width: 0;
  display: flex;
  align-items: baseline;
  gap: 7px;
}

.echo-kicker {
  font-size: 11px;
  font-weight: var(--yb-fw-bold);
  color: var(--yb-paper-ink-dim);
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.echo-count {
  font-size: 10px;
  color: var(--yb-paper-ink-dim);
  opacity: 0.75;
}

.echo-new {
  flex: none;
  height: 24px;
  padding: 0 9px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: 0;
  border-radius: var(--yb-radius-pill);
  background: var(--yb-accent);
  color: var(--yb-text-on-accent);
  font-size: 10px;
  font-weight: var(--yb-fw-medium);
  cursor: pointer;
  box-shadow: 0 1px 4px rgba(var(--yb-c-sky-rgb), 0.22);
  transition: transform 160ms var(--yb-ease-out), box-shadow 160ms var(--yb-ease-out), filter 160ms var(--yb-ease-out);
}

.echo-new:hover {
  transform: translateY(-1px);
  filter: brightness(1.03);
  box-shadow: 0 3px 10px rgba(var(--yb-c-sky-rgb), 0.3);
}

.echo-list {
  flex: none;
  padding: 2px 8px 12px;
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.echo-list::before { display: none; }

.echo-row {
  position: relative;
  display: flex;
  align-items: stretch;
}

.echo-main {
  min-width: 0;
  flex: 1;
  margin: 0;
  padding: 7px 28px 7px 10px;
  border: 1px solid var(--yb-note-border);
  border-radius: var(--yb-radius-sm);
  background: var(--yb-note-soft);
  color: var(--yb-paper-ink);
  text-align: left;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 2px;
  box-shadow: 0 1px 4px rgba(var(--yb-paper-shade-rgb), 0.04);
  transition:
    transform 160ms var(--yb-ease-out),
    box-shadow 160ms var(--yb-ease-out),
    background 160ms var(--yb-ease-out),
    border-color 160ms var(--yb-ease-out);
}

.echo-row:hover .echo-main {
  background: var(--yb-note-bg);
  box-shadow: 0 4px 12px rgba(var(--yb-paper-shade-rgb), 0.08);
}

.echo-row.active .echo-main {
  background: var(--yb-note-accent);
  border-color: rgba(var(--yb-c-sky-rgb), 0.18);
  box-shadow: 0 4px 14px rgba(var(--yb-c-sky-rgb), 0.12);
}

/* 两行：标题+时间 / 预览 */
.echo-line1 {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  min-width: 0;
}

.echo-line1 strong {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  font-weight: var(--yb-fw-medium);
  color: var(--yb-paper-ink);
  line-height: 1.3;
}

.echo-line1 time {
  flex: none;
  font-size: 10px;
  color: var(--yb-paper-ink-dim);
  font-variant-numeric: tabular-nums;
}

.echo-preview {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 10px;
  line-height: 1.3;
  color: var(--yb-paper-ink-dim);
}

.echo-preview.quiet { opacity: 0.72; }

.echo-delete {
  position: absolute;
  right: 6px;
  top: 50%;
  transform: translateY(-50%);
  z-index: 4;
  width: 20px;
  height: 20px;
  padding: 0;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--yb-paper-ink-dim);
  opacity: 0;
  cursor: pointer;
  transition: opacity 140ms var(--yb-ease-out), color 140ms var(--yb-ease-out), background 140ms var(--yb-ease-out);
}

.echo-row:hover .echo-delete,
.echo-delete:focus-visible { opacity: 1; }
.echo-delete:hover { color: var(--yb-danger); background: var(--yb-danger-soft); }

.echo-empty {
  width: 100%;
  margin: 4px 0 0;
  padding: 14px 12px;
  border: 1px solid var(--yb-note-border);
  border-radius: var(--yb-radius-sm);
  background: var(--yb-note-soft);
  box-shadow: none;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  color: var(--yb-paper-ink-dim);
  cursor: pointer;
}

.empty-ripple {
  width: 28px;
  height: 28px;
  margin-bottom: 2px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  border: 1px solid var(--yb-note-border);
  background: var(--yb-note-bg);
  box-shadow: 0 1px 3px rgba(var(--yb-paper-shade-rgb), 0.06);
}

.empty-ripple i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--yb-accent);
}

.echo-empty strong { font-size: 12px; color: var(--yb-text-dim); }
.echo-empty > span:last-child { font-size: 10px; }

@media (prefers-reduced-motion: reduce) {
  .echo-new,
  .echo-main,
  .echo-delete { transition: none; }
}
</style>
