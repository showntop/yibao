<script setup lang="ts">
/* SessionList — 会话列表（左一栏）：AI 原生工作台的"历史侧栏"。
 *
 * 会话 = 前端元数据（id/title/preview/updatedAt），localStorage 持久化。
 * 新建 / 切换 / 删除，当前会话高亮；标题由首条用户消息自动生成。
 * 气泡内容持久化（load_session）属 Phase 4（需后端扩展），本栏只管元数据流。
 */
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
  select: [id: string];   // 选中会话
  newChat: [];            // 新建
  active: [id: string];   // 当前会话 id 变化
}>();

function load(): SessionMeta[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const list = raw ? JSON.parse(raw) : [];
    return Array.isArray(list) ? list.filter((s) => s && typeof s.id === "string") : [];
  } catch {
    return [];
  }
}
function persist() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions.value.slice(0, 60)));
  } catch { /* 存储满/隐私模式忽略 */ }
}

/** 供父组件调用：更新当前会话的标题/预览（首条消息生成标题，回复更新预览）。 */
function updateCurrent(partial: { title?: string; preview?: string }) {
  const s = sessions.value.find((x) => x.id === activeId.value);
  if (!s) return;
  if (partial.title !== undefined) s.title = partial.title;
  if (partial.preview !== undefined) s.preview = partial.preview;
  s.updatedAt = Date.now();
  persist();
  // 移到列表顶部（最近会话置顶）
  const idx = sessions.value.indexOf(s);
  if (idx > 0) {
    sessions.value.splice(idx, 1);
    sessions.value.unshift(s);
  }
}

/** 新对话：创建会话 + 置当前 + 清空气泡（父组件处理）。 */
function newChat() {
  const id = Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  sessions.value.unshift({ id, title: "新对话", preview: "", updatedAt: Date.now() });
  activeId.value = id;
  localStorage.setItem(ACTIVE_KEY, id);
  persist();
  emit("newChat");
  emit("active", id);
}

function select(s: SessionMeta) {
  if (s.id === activeId.value) return;
  activeId.value = s.id;
  localStorage.setItem(ACTIVE_KEY, s.id);
  emit("select", s.id);
}

function remove(id: string) {
  sessions.value = sessions.value.filter((x) => x.id !== id);
  persist();
  if (activeId.value === id) {
    activeId.value = sessions.value[0]?.id ?? "";
    localStorage.setItem(ACTIVE_KEY, activeId.value);
    emit("active", activeId.value);
  }
}

function fmtTime(ts: number): string {
  const d = new Date(ts);
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  if (d.toDateString() === now.toDateString()) return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  if (d.getFullYear() === now.getFullYear()) return `${d.getMonth() + 1}/${d.getDate()}`;
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`;
}

defineExpose({ updateCurrent, newChat });
</script>

<template>
  <aside class="session">
    <div class="s-head">
      <span class="s-head-title">会话</span>
      <button class="s-new" title="新对话" @click="newChat">
        <YbIcon name="chat" :size="13" />
        <span>新对话</span>
      </button>
    </div>

    <div class="s-list">
      <button
        v-for="s in sessions"
        :key="s.id"
        class="s-item"
        :class="{ on: s.id === activeId }"
        :title="s.title"
        @click="select(s)"
      >
        <span class="s-ico"><YbIcon name="chat" :size="12" /></span>
        <span class="s-main">
          <span class="s-title">{{ s.title }}</span>
          <span v-if="s.preview" class="s-preview">{{ s.preview }}</span>
        </span>
        <span class="s-time">{{ fmtTime(s.updatedAt) }}</span>
        <span class="s-del" title="删除会话" @click.stop="remove(s.id)">
          <YbIcon name="x" :size="10" />
        </span>
      </button>

      <div v-if="!sessions.length" class="s-empty">
        <YbIcon name="chat" :size="18" :stroke="1.4" />
        <p>还没有会话</p>
        <span>点「新对话」开始和译宝聊</span>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.session {
  width: 200px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  border-right: none;
  background:
    radial-gradient(80% 40% at 0% 0%, rgba(var(--yb-c-sky-rgb), 0.04), transparent 70%),
    var(--yb-content-bg);
  min-height: 0;
  position: relative;
}
/* 右边界渐变 hairline（与智能体栏同语言） */
.session::after {
  content: "";
  position: absolute;
  right: 0;
  top: 12%;
  bottom: 12%;
  width: 1px;
  background: linear-gradient(
    180deg,
    transparent,
    rgba(var(--yb-c-sky-rgb), 0.14) 50%,
    transparent
  );
  pointer-events: none;
}

/* 头部：标题 + 新对话 */
.s-head {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  padding: 10px 12px 8px;
}
.s-head-title {
  font-size: var(--yb-fs-sm);
  font-weight: var(--yb-fw-bold);
  color: var(--yb-text-faint);
  letter-spacing: 0.04em;
}
.s-new {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 9px;
  border: 1px solid var(--yb-border-strong);
  border-radius: var(--yb-radius-sm);
  background: var(--yb-surface-solid);
  color: var(--yb-accent-deep);
  font-size: var(--yb-fs-sm);
  font-family: inherit;
  cursor: pointer;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.s-new:hover {
  border-color: var(--yb-accent);
  background: var(--yb-accent-soft);
  transform: translateY(-1px);
  box-shadow: var(--yb-shadow-1);
}

/* 列表 */
.s-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  scrollbar-width: thin;
  padding: 0 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.s-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 7px;
  width: 100%;
  padding: 6px 8px;
  border: none;
  border-radius: var(--yb-radius-sm);
  background: transparent;
  color: var(--yb-text);
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  transition: background var(--yb-dur-fast) var(--yb-ease-out);
}
.s-item:hover {
  background: var(--yb-row-hover);
}
.s-item.on {
  background: var(--yb-accent-soft);
}
.s-item.on::before {
  content: "";
  position: absolute;
  left: 0;
  top: 20%;
  bottom: 20%;
  width: 3px;
  border-radius: var(--yb-radius-pill);
  background: var(--yb-accent);
}
.s-ico {
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  display: grid;
  place-items: center;
  border-radius: 6px;
  background: var(--yb-surface-2);
  color: var(--yb-accent);
}
.s-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.s-title {
  font-size: var(--yb-fs-md);
  font-weight: var(--yb-fw-medium);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.s-preview {
  font-size: var(--yb-fs-xs);
  color: var(--yb-text-faint);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.s-time {
  flex-shrink: 0;
  font-size: var(--yb-fs-xs);
  color: var(--yb-text-faint);
}
.s-del {
  flex-shrink: 0;
  width: 18px;
  height: 18px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  color: var(--yb-text-faint);
  opacity: 0;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.s-item:hover .s-del {
  opacity: 1;
}
.s-del:hover {
  background: var(--yb-danger-soft);
  color: var(--yb-danger);
}

/* 空态 */
.s-empty {
  margin-top: 20px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  color: var(--yb-text-faint);
  text-align: center;
  font-size: var(--yb-fs-sm);
  line-height: 1.4;
}
.s-empty p {
  margin: 2px 0 0;
  font-weight: var(--yb-fw-medium);
  color: var(--yb-text-dim);
}
.s-empty span {
  font-size: var(--yb-fs-xs);
}
</style>
