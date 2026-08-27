<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import YbIcon from "../../components/common/YbIcon.vue";
import { spineCaption, spineVisible, useHomeChrome, useLiveAssembly } from "../../lib/home/home-chrome.ts";
import { fmtClockToday } from "../../lib/time";
import { faceOf, spineLimitOf } from "../../lib/home/home-assembly.ts";
import { sessionStore } from "../../state/store";
import type { ConversationMeta } from "../../state/types";

export interface SessionMeta {
  id: string;
  title: string;
  preview: string;
  updatedAt: number;
}

// 会话列表/活动会话：权威在 SessionStore.conversation，本组件只做 UI 投影
const sessions = ref<SessionMeta[]>([]);
const activeId = ref(sessionStore.conversation.getActiveConversationId() ?? "");

const props = defineProps<{ variant?: "list" | "spine" | "cards" }>();
const assembly = useLiveAssembly();
const { id: presetId } = useHomeChrome();
const face = computed(() =>
  (props.variant ?? faceOf(assembly.value, "sessions", "list")) as "list" | "spine" | "cards",
);
const shown = computed(() =>
  face.value === "spine"
    ? spineVisible(sessions.value, activeId.value, spineLimitOf(assembly.value))
    : face.value === "cards"
      ? sessions.value.slice(0, 8)
      : sessions.value,
);
const listEl = ref<HTMLElement | null>(null);

watch([activeId, face], async () => {
  if (face.value !== "spine") return;
  await nextTick();
  listEl.value?.querySelector<HTMLElement>(".echo-row.active")?.scrollIntoView({
    block: "nearest",
    inline: "nearest",
  });
});

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



</script>

<template>
  <aside class="session" :class="[face, { 'rail-list': presetId === 'rails' }]">
    <h2 v-if="face === 'list'" class="yb-widget-head">
      <span>会话 <span class="yb-widget-meta">{{ sessions.length ? `${sessions.length} 条` : "暂无" }}</span></span>
      <button class="echo-new" type="button" title="开始一段新对话" aria-label="开始一段新对话" @click="newChat">新对话</button>
    </h2>

    <div ref="listEl" class="echo-list" role="list" :aria-label="face === 'cards' ? '到过的人' : '会话'">
      <div v-for="session in shown" :key="session.id" class="echo-row" :class="{ active: session.id === activeId }" role="listitem">
        <button class="echo-main" type="button" :title="session.title" @click="select(session)">
          <template v-if="face === 'spine'">{{ spineCaption(session.title) }}</template>
          <template v-else>
            <span class="echo-line1">
              <strong>{{ face === "list" ? session.title : spineCaption(session.title, 4) }}</strong>
              <time v-if="face === 'list'">{{ fmtClockToday(session.updatedAt) }}</time>
            </span>
            <span v-if="face === 'list' && session.preview" class="echo-preview">{{ session.preview }}</span>
            <span v-else-if="face === 'list'" class="echo-preview quiet">暂无内容</span>
          </template>
        </button>
        <button v-if="face === 'list'" class="echo-delete" type="button" title="删除这段会话" aria-label="删除这段会话" @click="remove(session.id)">
          <YbIcon name="x" :size="10" />
        </button>
      </div>

      <button v-if="!sessions.length && face === 'list'" class="echo-empty" type="button" @click="newChat">
        <span class="empty-ripple"><i /></span>
        <strong>暂无会话</strong>
        <span>开始一段新的对话</span>
      </button>
      <button v-if="face === 'cards'" class="echo-new card-new" type="button" title="新的来访" aria-label="新的来访" @click="newChat">
        <span>+</span>
      </button>
    </div>

    <button v-if="face === 'spine'" class="echo-new" type="button" title="新的一页" aria-label="新的一页" @click="newChat">
      <span>新页</span>
    </button>
  </aside>
</template>

<style scoped>
.session {
  width: 100%;
  box-sizing: border-box;
  height: 100%;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow-x: hidden;
}

button { font: inherit; }

.session.list .echo-list {
  margin: 2px 8px 8px;
  padding: 4px;
  border-radius: calc(var(--yb-widget-radius) - 6px);
  background: var(--yb-note-mute);
  box-shadow: var(--yb-press);
}
.session.rail-list .yb-widget-head {
  min-height: 32px;
  padding: 12px 10px 4px;
  letter-spacing: 0.08em;
}
.session.rail-list .echo-list {
  margin: 2px 4px 6px;
  padding: 2px 0;
  border-radius: 0;
  background: transparent;
  box-shadow: none;
}
.session.rail-list .echo-main {
  min-height: 48px;
  padding: 9px 30px 9px 10px;
  border-radius: 10px;
}
.session.rail-list .echo-row + .echo-row {
  margin-top: 2px;
}
.session.rail-list .echo-row.active .echo-main {
  background: color-mix(in srgb, var(--yb-note-accent) 82%, transparent);
  box-shadow: inset 3px 0 0 var(--yb-accent), var(--yb-glaze-hi);
}
.session.rail-list .echo-line1 strong {
  font-size: 12px;
}
.session.rail-list .echo-preview {
  margin-top: 2px;
  font-size: 10px;
}

.session.list .echo-new {
  height: auto;
  padding: 0;
  border-radius: 0;
  background: transparent;
  color: var(--yb-paper-ink-dim);
  font-size: 10px;
  font-weight: var(--yb-fw-medium);
  letter-spacing: var(--yb-kicker-track);
  box-shadow: none;
}

.session.list .echo-new:hover {
  transform: none;
  filter: none;
  color: var(--yb-paper-ink);
}

.session.list .echo-row.active .echo-main {
  background: var(--yb-widget-bg);
  box-shadow: var(--yb-glaze-hi);
}

.session.list .echo-empty {
  margin: 0;
  background: transparent;
  box-shadow: none;
}

.echo-new {
  flex: none;
  height: 22px;
  padding: 0 8px;
  display: inline-flex;
  align-items: center;
  border: 0;
  border-radius: var(--yb-radius-pill);
  background: var(--yb-accent);
  color: var(--yb-text-on-accent);
  font-size: 10px;
  font-weight: var(--yb-fw-medium);
  cursor: pointer;
  box-shadow: var(--yb-glaze-hi), 0 1px 3px rgba(var(--yb-c-sky-rgb), 0.22);
  transition: transform 160ms var(--yb-ease-out), filter 160ms var(--yb-ease-out);
}

.echo-new:hover {
  transform: translateY(-1px);
  filter: brightness(1.03);
}

.echo-list {
  flex: 1;
  min-width: 0;
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
  padding: 2px 6px 8px;
  display: flex;
  flex-direction: column;
  scrollbar-width: thin;
}

.echo-row {
  position: relative;
  min-width: 0;
  max-width: 100%;
  display: flex;
  align-items: stretch;
}

.echo-main {
  min-width: 0;
  flex: 1;
  margin: 0;
  padding: 8px 28px 8px 8px;
  border: 0;
  border-radius: calc(var(--yb-widget-radius) - 6px);
  background: transparent;
  color: var(--yb-paper-ink);
  text-align: left;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 2px;
  transition: background 160ms var(--yb-ease-out);
}

.echo-row:hover .echo-main {
  background: color-mix(in srgb, var(--yb-widget-bg) 70%, transparent);
}

.echo-row.active .echo-main {
  background: var(--yb-note-accent);
  box-shadow: var(--yb-press);
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
  display: block;
  min-width: 0;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11px;
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
  max-width: 100%;
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
  padding: 18px 12px;
  border: 0;
  border-radius: calc(var(--yb-widget-radius) - 6px);
  background: var(--yb-note-mute);
  box-shadow: var(--yb-press);
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

.session.spine {
  position: relative;
  z-index: 1;
  width: 100%;
  height: 100%;
  justify-content: stretch;
  gap: 8px;
  padding: 10px 0 8px;
  overflow: visible;
}
.session.spine::before {
  content: "";
  position: absolute;
  inset: 0;
  z-index: 0;
  pointer-events: none;
  border: 1px solid var(--yb-widget-border);
  border-right: 0;
  border-radius: var(--yb-widget-radius) 0 0 var(--yb-widget-radius);
  background: var(--yb-spine-board);
}
.session.spine .echo-new,
.session.spine .echo-main {
  position: relative;
  box-sizing: border-box;
  overflow: hidden;
  writing-mode: vertical-rl;
  letter-spacing: 0.14em;
  border: 1px solid var(--yb-widget-border);
  border-right: 0;
  border-radius: 8px 0 0 8px;
}
.session.spine .echo-new {
  flex: none;
  z-index: 1;
  align-self: stretch;
  height: 52px;
  margin: 0 0 0 7px;
  padding: 0;
  justify-content: center;
  background: var(--yb-spine-leaf);
  color: var(--yb-accent-deep);
  box-shadow: none;
  font-size: 11px;
  font-weight: var(--yb-fw-medium);
}
.session.spine .echo-new:hover {
  transform: translateX(2px);
  filter: none;
  color: var(--yb-paper-ink);
  background: var(--yb-widget-bg);
}
.session.spine .echo-list {
  position: relative;
  z-index: 1;
  flex: 1;
  min-height: 0;
  padding: 0;
  justify-content: flex-start;
  align-items: stretch;
  gap: 0;
  overflow-x: hidden;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-width: none;
}
.session.spine .echo-list::-webkit-scrollbar { display: none; }
.session.spine .echo-row {
  flex: 0 0 52px;
  min-height: 0;
  overflow: hidden;
}
.session.spine .echo-row + .echo-row {
  margin-top: -1px;
}
.session.spine .echo-main {
  flex: none;
  width: calc(100% - 7px);
  height: 52px;
  min-height: 0;
  max-height: 52px;
  margin: 0 0 0 7px;
  padding: 0;
  background: var(--yb-spine-leaf);
  box-shadow: none;
  color: var(--yb-paper-ink);
  justify-content: center;
  font-size: 12px;
  font-weight: var(--yb-fw-medium);
  transition:
    transform var(--yb-dur) var(--yb-ease-spring),
    color var(--yb-dur) var(--yb-ease-out),
    background var(--yb-dur) var(--yb-ease-out);
}
.session.spine .echo-row:hover .echo-main {
  transform: translateX(3px);
  background: var(--yb-widget-bg);
}
.session.spine .echo-row.active .echo-main {
  z-index: 2;
  transform: translateX(5px);
  background: var(--yb-widget-bg);
  color: var(--yb-paper-ink);
  box-shadow: none;
}

.session.cards {
  flex-direction: column;
  align-items: stretch;
  justify-content: flex-start;
  gap: 0;
  width: 100%;
  height: auto;
  padding: 0;
}
.session.cards .echo-list {
  flex: none;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(4.5rem, 1fr));
  width: 100%;
  overflow: hidden;
  padding: 0;
  gap: 8px;
}
.session.cards .echo-row {
  width: auto;
  min-width: 0;
  margin: 0;
  transform: none;
}
.session.cards .echo-row:hover .echo-main,
.session.cards .echo-row.active .echo-main {
  transform: translateY(-2px);
}
.session.cards .echo-main {
  width: 100%;
  height: 72px;
  padding: 10px 6px;
  border-radius: 12px;
  background:
    var(--yb-widget-glaze),
    var(--yb-widget-bg);
  box-shadow: var(--yb-widget-shadow);
  border: 1px solid var(--yb-widget-border);
  justify-content: center;
  align-items: center;
  text-align: center;
  transition: transform 160ms var(--yb-ease-out), background 160ms var(--yb-ease-out);
}
.session.cards .echo-row.active .echo-main {
  background: var(--yb-note-accent);
  box-shadow: var(--yb-press);
}
.session.cards .echo-line1 {
  display: block;
  width: 100%;
  text-align: center;
}
.session.cards .echo-line1 strong {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  letter-spacing: 0.04em;
}
.session.cards .card-new {
  flex: none;
  align-self: stretch;
  width: auto;
  min-width: 0;
  height: 72px;
  margin: 0;
  padding: 0;
  justify-content: center;
  border-radius: 12px;
  background: color-mix(in srgb, var(--yb-widget-bg) 40%, transparent);
  color: var(--yb-text-faint);
  box-shadow: none;
  font-size: 22px;
  font-weight: var(--yb-fw-medium);
  letter-spacing: 0;
  writing-mode: horizontal-tb;
}
.session.cards .card-new:hover {
  transform: translateY(-2px);
  color: var(--yb-paper-ink);
}

@media (prefers-reduced-motion: reduce) {
  .echo-new,
  .echo-main,
  .echo-delete { transition: none; }
  .session.spine .echo-row .echo-main,
  .session.spine .echo-new:hover { transform: none; }
}
</style>
