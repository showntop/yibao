<script setup lang="ts">
/* BrainSession — 左侧状态、能力与会话复合栏。
 * 上：认知浮层；下：会话浮层。两块独立 widget，中间留缝，不再共一张底卡。
 */
import { ref } from "vue";
import AgentBrain from "./AgentBrain.vue";
import SessionList from "./SessionList.vue";

type AgentState = "idle" | "listen" | "think" | "work" | "say" | "success" | "error";

defineProps<{ state: AgentState }>();
const emit = defineEmits<{
  chat: [draft: string];
  toggle: [];
  select: [id: string];
  newChat: [];
  active: [id: string];
}>();

const sessionRef = ref<InstanceType<typeof SessionList> | null>(null);

/** 供父组件转发：更新当前会话标题/预览。 */
function updateCurrent(partial: { title?: string; preview?: string }) {
  sessionRef.value?.updateCurrent(partial);
}
/** 供父组件转发：从 domain 重拉会话列表（提交首条消息自动建会话后同步列表）。 */
function syncSessions() {
  sessionRef.value?.sync();
}
defineExpose({ updateCurrent, syncSessions });
</script>

<template>
  <aside class="brain-session">
    <!-- 上：大脑 / 状态与能力 -->
    <section class="bs-sheet bs-sheet-brain">
      <AgentBrain :state="state" compact @chat="(d) => emit('chat', d)" @toggle="emit('toggle')" />
    </section>

    <!-- 下：会话列表（独立浮层） -->
    <section class="bs-sheet bs-sheet-sessions">
      <SessionList
        ref="sessionRef"
        @select="(id) => emit('select', id)"
        @new-chat="emit('newChat')"
        @active="(id) => emit('active', id)"
      />
    </section>
  </aside>
</template>

<style scoped>
.brain-session {
  width: 304px;
  max-width: 100%;
  box-sizing: border-box;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 8px 10px 10px;
  /* 与中栏同底，不再铺一层灰蓝「侧栏色」 */
  background: transparent;
  user-select: none;
}

.bs-sheet {
  position: relative;
  border-radius: var(--yb-note-radius);
  background: var(--yb-note-bg);
  border: 1px solid var(--yb-note-border);
  box-shadow: var(--yb-note-shadow);
  overflow: hidden;
}

.bs-sheet-brain {
  flex-shrink: 0;
}

.bs-sheet-sessions {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.bs-sheet-sessions :deep(.session) {
  height: 100%;
}
</style>
