<script lang="ts">
// TabInfo 由壳装配:projectId 取工位 cwd basename;state 是工位 store 的响应式状态对象
// (模板里直读 waiting/streaming 字段,响应式追踪在读取点生效,壳无需轮询)。
import type { RailLive } from "../stores/stations";

export interface TabInfo {
  id: number;
  projectId: string;
  live: RailLive;
}
</script>

<script setup lang="ts">
// 工位 tab 条(2026-09 交互重构):工位升一等公民——编号 + 项目名 + 活体点一排看全,
// 点击聚焦、✕ 关工位(语义显式:「关这个工位」,解绑会话/移除空工位由壳决定,与旧头部
// 裸 ✕ 同壳语义但落点更可预期)。☰ 会话抽屉与 + 新工位同排收编,stations 区顶让出
// 36px padding。窄窗同样可见可切换——修复「窄窗下空工位不可达」死区(旧 rail 只列会话)。
defineProps<{
  tabs: TabInfo[];
  focusId: number;
  addDisabled: boolean;
}>();
const emit = defineEmits<{
  focus: [id: number];
  close: [id: number];
  add: [];
  "open-drawer": [];
}>();
</script>

<template>
  <div class="tabs">
    <button type="button" class="tab-btn" title="会话列表" aria-label="会话列表" @click="emit('open-drawer')">
      <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
        <path fill="currentColor" d="M2.5 3.75h11v1.25h-11zm0 3.5h11v1.25h-11zm0 3.5h11v1.25h-11z"/>
      </svg>
    </button>
    <div class="tab-scroll" role="tablist" aria-label="工位">
      <div
        v-for="t in tabs"
        :key="t.id"
        class="tab"
        role="tab"
        :aria-selected="t.id === focusId"
        :title="t.projectId ? '工位 ' + t.id + ' · ' + t.projectId : '工位 ' + t.id + '（未选项目目录）'"
        @click="emit('focus', t.id)"
      >
        <span class="tab-no">{{ t.id }}</span>
        <span class="tab-name">{{ t.projectId || "未选项目" }}</span>
        <span v-if="t.live === 'waiting'" class="dot-waiting" title="等待审批"></span>
        <span v-else-if="t.live === 'running'" class="dot-running" title="运行中"></span>
        <button
          type="button"
          class="tab-close"
          :title="'关闭工位 ' + t.id + '（其中的会话被解绑，不停止运行）'"
          :aria-label="'关闭工位 ' + t.id"
          @click.stop="emit('close', t.id)"
        >✕</button>
      </div>
    </div>
    <button
      type="button"
      class="tab-btn"
      title="新工位（最多 3 个）"
      :disabled="addDisabled"
      @click="emit('add')"
    >+</button>
  </div>
</template>
