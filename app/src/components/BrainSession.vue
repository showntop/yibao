<script setup lang="ts">
/* BrainSession — 左侧状态、能力与会话复合栏。
 * 上部呈现译宝当下的记忆、感知、思考与行动，下部保留最近会话，
 * 两者共享同一片背景和纵向节奏，不再表现成两块独立功能导航。
 */
import { ref } from "vue";
import AgentBrain from "./AgentBrain.vue";
import SessionList from "./SessionList.vue";

type AgentState = "idle" | "listen" | "think" | "work" | "say" | "success" | "error";

defineProps<{ state: AgentState }>();
const emit = defineEmits<{
  chat: [draft: string];
  select: [id: string];
  newChat: [];
  active: [id: string];
}>();

const sessionRef = ref<InstanceType<typeof SessionList> | null>(null);

/** 供父组件转发：更新当前会话标题/预览。 */
function updateCurrent(partial: { title?: string; preview?: string }) {
  sessionRef.value?.updateCurrent(partial);
}
defineExpose({ updateCurrent });
</script>

<template>
  <aside class="brain-session">
    <!-- 上部：抽象心智，不重复完整 Avatar -->
    <div class="bs-core">
      <AgentBrain :state="state" compact @chat="(d) => emit('chat', d)" />
    </div>

    <div class="bs-bridge" aria-hidden="true"><i /></div>

    <!-- 下部：会话历史（滚动） -->
    <div class="bs-session">
      <SessionList
        ref="sessionRef"
        @select="(id) => emit('select', id)"
        @new-chat="emit('newChat')"
        @active="(id) => emit('active', id)"
      />
    </div>
  </aside>
</template>

<style scoped>
.brain-session {
  width: 304px;
  max-width: 100%;
  box-sizing: border-box;
  /* 关键：必须撑满父级（col-left stretch 满高）——flex column 容器 auto 高度
   * = 内容高度和，会话区 flex:1 分不到"剩余空间"，echo-list 的滚动会失效 */
  height: 100%;
  min-height: 0;
  overflow: visible;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  position: relative;
  background:
    radial-gradient(100% 46% at 50% 4%, rgba(var(--yb-c-sky-rgb), 0.065), transparent 74%),
    radial-gradient(82% 30% at 50% 100%, rgba(var(--yb-c-sky-rgb), 0.045), transparent 72%),
    var(--yb-content-bg);
  user-select: none;
}
/* 右边界渐变 hairline（与右栏同语言） */
.brain-session::after {
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

.bs-core {
  flex-shrink: 0;
  position: relative;
  overflow: visible;
}
.bs-core::after {
  content: "";
  position: absolute;
  left: 8%;
  right: 8%;
  bottom: -16px;
  height: 34px;
  background: radial-gradient(58% 100% at 50% 0%, rgba(var(--yb-c-sky-rgb), 0.12), transparent 72%);
  pointer-events: none;
  z-index: 3;
}

/* 上下区域共享同一条纵向线索，避免硬分割。 */
.bs-bridge {
  flex-shrink: 0;
  position: relative;
  height: 13px;
  margin: 0 14px;
}
.bs-bridge::before {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  top: 4px;
  height: 1px;
  background: linear-gradient(
    90deg,
    transparent,
    rgba(var(--yb-c-sky-rgb), 0.09) 30%,
    rgba(var(--yb-c-sky-rgb), 0.09) 70%,
    transparent
  );
}
.bs-bridge i {
  position: absolute;
  left: 24px;
  top: 2px;
  transform: translate(-50%, -50%);
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--yb-accent);
  box-shadow: 0 0 0 5px rgba(var(--yb-c-sky-rgb), 0.07), 0 0 12px rgba(var(--yb-c-sky-rgb), 0.32);
}

/* 下部会话区：占满剩余高度，列表自身滚动 */
.bs-session {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
</style>
