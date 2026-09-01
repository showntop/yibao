<script lang="ts">
// TabInfo 由壳装配:projectId 取工位 cwd basename;state 是工位 store 的响应式状态对象
// (模板里直读 waiting/streaming 字段,响应式追踪在读取点生效,壳无需轮询)。
import type { RailLive } from "../stores/stations";

export interface TabInfo {
  id: number;
  projectId: string;
  live: RailLive;
}

// 聚焦工位的会话信息(壳从工位 expose 的 store state 派生;空工位传 null → 右侧不渲染)
export interface SessionChipInfo {
  summary: string;      // 任务摘要(首条用户消息前 24 字;"")
  agent: string;        // 引擎展示名(CC/Codex)
  cost: string;         // 成本聚合文案("" = 无数据)
  hasSession: boolean;
  busy: boolean;        // sending/streaming → 新对话禁用
}
</script>

<script setup lang="ts">
// 工位 tab 条(2026-09 走查校准:一行 chrome)——左边工位(tabs:编号+项目名+活体点,
// 点击聚焦、✕ 关工位),右边聚焦工位的当前会话(摘要 · 引擎 · 成本 · 新对话)。
// 工位头整行 retired:项目名归 tab,会话身份/成本/新对话收进本行右端。
// ☰ 会话抽屉与 + 新工位同排;窄窗同样可见可切(修复窄窗空工位不可达死区)。
defineProps<{
  tabs: TabInfo[];
  focusId: number;
  addDisabled: boolean;
  session: SessionChipInfo | null;
}>();
const emit = defineEmits<{
  focus: [id: number];
  close: [id: number];
  add: [];
  "open-drawer": [];
  "new-chat": [];
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
    <!-- 聚焦工位的当前会话(空工位不渲染):摘要 · 引擎 · 成本 · 新对话 -->
    <div v-if="session" class="tab-session">
      <span
        class="tab-session-sum"
        :title="session.summary || '新会话（发送即开）'"
      >{{ session.summary || "新会话" }}</span>
      <span class="tab-session-agent" title="当前会话引擎">{{ session.agent }}</span>
      <span v-if="session.cost" class="tab-session-cost" title="本会话累计 token 与成本">{{ session.cost }}</span>
      <button
        type="button"
        class="tab-session-new"
        title="清空当前对话，开新会话（下次发送走 coding.start）"
        :disabled="session.busy || !session.hasSession"
        @click="emit('new-chat')"
      >新对话</button>
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
