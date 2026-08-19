<script setup lang="ts">
/* BrainSession — 左侧桌面：身份 / 认知 / 今日 / 会话，各自是独立 OS 控件。 */
import { ref } from "vue";
import AgentBrain from "./AgentBrain.vue";
import SessionList from "./SessionList.vue";
import HomeWidget from "./HomeWidget.vue";

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
  <aside class="brain-session yb-desk">
    <AgentBrain :state="state" compact @chat="(d) => emit('chat', d)" @toggle="emit('toggle')" />
    <HomeWidget id="sessions" fill>
      <SessionList
        ref="sessionRef"
        @select="(id) => emit('select', id)"
        @new-chat="emit('newChat')"
        @active="(id) => emit('active', id)"
      />
    </HomeWidget>
  </aside>
</template>

<style scoped>
.brain-session {
  width: 280px;
  max-width: 100%;
  flex-shrink: 0;
  user-select: none;
}

.yb-widget-fill :deep(.session),
[data-widget="sessions"] :deep(.session) {
  height: 100%;
}
</style>
