<script setup lang="ts">
import { ref } from "vue";
import YbIcon from "./YbIcon.vue";

export interface SessionMeta {
  id: string;
  title: string;
  preview: string;
  updatedAt: number;
}

const STORAGE_KEY = "yb-sessions";
const ACTIVE_KEY = "yb-active-session";

const sessions = ref<SessionMeta[]>(load());
const activeId = ref(localStorage.getItem(ACTIVE_KEY) ?? sessions.value[0]?.id ?? "");

const emit = defineEmits<{
  select: [id: string];
  newChat: [];
  active: [id: string];
}>();

function load(): SessionMeta[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const list = raw ? JSON.parse(raw) : [];
    return Array.isArray(list) ? list.filter((item) => item && typeof item.id === "string") : [];
  } catch {
    return [];
  }
}

function persist() {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions.value.slice(0, 60))); } catch { /* storage unavailable */ }
}

function updateCurrent(partial: { title?: string; preview?: string }) {
  const session = sessions.value.find((item) => item.id === activeId.value);
  if (!session) return;
  if (partial.title !== undefined) session.title = partial.title;
  if (partial.preview !== undefined) session.preview = partial.preview;
  session.updatedAt = Date.now();
  const index = sessions.value.indexOf(session);
  if (index > 0) {
    sessions.value.splice(index, 1);
    sessions.value.unshift(session);
  }
  persist();
}

function newChat() {
  const id = Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  sessions.value.unshift({ id, title: "新对话", preview: "", updatedAt: Date.now() });
  activeId.value = id;
  localStorage.setItem(ACTIVE_KEY, id);
  persist();
  emit("newChat");
  emit("active", id);
}

function select(session: SessionMeta) {
  if (session.id === activeId.value) return;
  activeId.value = session.id;
  localStorage.setItem(ACTIVE_KEY, session.id);
  emit("select", session.id);
  emit("active", session.id);
}

function remove(id: string) {
  sessions.value = sessions.value.filter((item) => item.id !== id);
  persist();
  if (activeId.value === id) {
    activeId.value = sessions.value[0]?.id ?? "";
    localStorage.setItem(ACTIVE_KEY, activeId.value);
    emit("active", activeId.value);
  }
}

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

defineExpose({ updateCurrent, newChat, sessions });
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
      <div v-for="(session, index) in sessions" :key="session.id" class="echo-row" :class="{ active: session.id === activeId }" role="listitem">
        <span class="echo-node" aria-hidden="true"><i /></span>
        <button class="echo-main" type="button" :title="session.title" @click="select(session)">
          <span class="echo-meta">
            <span>{{ session.id === activeId ? "当前" : index === 0 ? "最近" : "会话" }}</span>
            <time>{{ fmtTime(session.updatedAt) }}</time>
          </span>
          <strong>{{ session.title }}</strong>
          <span v-if="session.preview" class="echo-preview">{{ session.preview }}</span>
          <span v-else class="echo-preview quiet">这段对话还没有留下内容</span>
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
}

button { font: inherit; }

.echo-head {
  flex: none;
  padding: 10px 14px 9px 18px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.echo-head > div {
  min-width: 0;
  display: flex;
  align-items: baseline;
  gap: 7px;
}

.echo-kicker {
  font-size: 12px;
  font-weight: var(--yb-fw-bold);
  color: var(--yb-text-strong);
  letter-spacing: 0.03em;
}

.echo-count {
  font-size: 10px;
  color: var(--yb-text-faint);
}

.echo-new {
  flex: none;
  height: 28px;
  padding: 0 9px;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  border: 1px solid rgba(var(--yb-c-sky-rgb), 0.15);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.62);
  color: var(--yb-accent-deep);
  font-size: 10px;
  cursor: pointer;
  transition: transform 160ms var(--yb-ease-out), background 160ms var(--yb-ease-out), border-color 160ms var(--yb-ease-out);
}

.echo-new:hover {
  transform: translateY(-1px);
  border-color: rgba(var(--yb-c-sky-rgb), 0.34);
  background: rgba(255, 255, 255, 0.94);
}

.echo-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  scrollbar-width: thin;
  padding: 2px 10px 16px 14px;
  position: relative;
}

.echo-list::before {
  content: "";
  position: absolute;
  left: 24px;
  top: 10px;
  bottom: 24px;
  width: 1px;
  background: linear-gradient(180deg, rgba(var(--yb-c-sky-rgb), 0.28), rgba(var(--yb-c-sky-rgb), 0.04));
}

.echo-row {
  min-height: 56px;
  position: relative;
  padding-left: 22px;
  display: flex;
  align-items: stretch;
}

.echo-node {
  position: absolute;
  left: 6px;
  top: 18px;
  width: 9px;
  height: 9px;
  z-index: 2;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.96);
  border: 1px solid rgba(var(--yb-c-sky-rgb), 0.28);
}

.echo-node i {
  width: 3px;
  height: 3px;
  border-radius: 50%;
  background: rgba(var(--yb-c-sky-rgb), 0.45);
}

.echo-main {
  min-width: 0;
  flex: 1;
  margin: 2px 0;
  padding: 8px 31px 8px 10px;
  border: 1px solid transparent;
  border-radius: 12px;
  background: transparent;
  color: var(--yb-text);
  text-align: left;
  cursor: pointer;
  transition: background 170ms var(--yb-ease-out), border-color 170ms var(--yb-ease-out), transform 170ms var(--yb-ease-out), box-shadow 170ms var(--yb-ease-out);
}

.echo-row:hover .echo-main {
  background: rgba(255, 255, 255, 0.52);
  border-color: rgba(var(--yb-c-sky-rgb), 0.08);
}

.echo-row.active .echo-main {
  padding-top: 9px;
  padding-bottom: 9px;
  background: rgba(255, 255, 255, 0.76);
  border-color: rgba(var(--yb-c-sky-rgb), 0.16);
  box-shadow: 0 10px 26px rgba(var(--yb-c-sky-rgb), 0.09), inset 0 1px 0 rgba(255, 255, 255, 0.92);
}

.echo-row.active .echo-node {
  top: 17px;
  width: 11px;
  height: 11px;
  left: 5px;
  border-color: rgba(var(--yb-c-sky-rgb), 0.55);
  box-shadow: 0 0 0 5px rgba(var(--yb-c-sky-rgb), 0.08), 0 0 12px rgba(var(--yb-c-sky-rgb), 0.28);
}

.echo-row.active .echo-node i {
  width: 5px;
  height: 5px;
  background: var(--yb-accent);
}

.echo-meta {
  margin-bottom: 2px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 9px;
  color: var(--yb-text-faint);
  letter-spacing: 0.03em;
}

.echo-main strong {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  font-weight: var(--yb-fw-medium);
  color: var(--yb-text-strong);
}

.echo-preview {
  display: block;
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 10px;
  color: var(--yb-text-faint);
}

.echo-preview.quiet { opacity: 0.72; }

.echo-delete {
  position: absolute;
  right: 7px;
  top: 19px;
  z-index: 4;
  width: 22px;
  height: 22px;
  padding: 0;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: var(--yb-text-faint);
  opacity: 0;
  cursor: pointer;
  transition: opacity 140ms var(--yb-ease-out), color 140ms var(--yb-ease-out), background 140ms var(--yb-ease-out);
}

.echo-row:hover .echo-delete,
.echo-delete:focus-visible { opacity: 1; }
.echo-delete:hover { color: var(--yb-danger); background: var(--yb-danger-soft); }

.echo-empty {
  width: calc(100% - 22px);
  margin: 22px 0 0 22px;
  padding: 18px 12px;
  border: 0;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.34);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 5px;
  color: var(--yb-text-faint);
  cursor: pointer;
}

.empty-ripple {
  width: 34px;
  height: 34px;
  margin-bottom: 3px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  border: 1px solid rgba(var(--yb-c-sky-rgb), 0.18);
  background: rgba(255, 255, 255, 0.62);
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
