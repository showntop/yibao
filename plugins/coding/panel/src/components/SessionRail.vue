<script lang="ts">
// 行类型与活体文案映射从本文件 export 供壳(T6 App.vue)用——<script setup> 不允许值导出,走普通脚本块。
import type { RailLive, Station } from "../stores/stations";

export interface RailRow {
  id: string; title: string; subtitle: string; agent: string;
  live: RailLive; boundStationId: number | null;
}

export const LIVE_TEXT = { waiting: "等待审批", running: "运行中", idle: "空闲" } as const;
</script>

<script setup lang="ts">
// 左栏会话列表(R4 阶段三 T5,纯展示):工位徽标 + 会话行(加入工位/聚焦/停止) + 新工位入口。
// 数据编排(coding.sessions 拉取/派生合并/防抖)在壳 App.vue;本组件只渲染 props 与转发 emits,零 invoke、零 store。
withDefaults(defineProps<{
  rows: RailRow[];      // 会话行(壳合并 coding.sessions 结果与派生状态)
  stations: Station[];  // 工位徽标行(已绑会话归属显示)
  focusId: number;
  drawer: boolean;      // 窄窗抽屉模式(壳传;宽窗忽略)
  addDisabled?: boolean; // 满 3 工位时壳禁用「+ 新工位」(T6)
}>(), { addDisabled: false });
const emit = defineEmits<{
  join: [sid: string, agent: string]; // 点未绑行 = 加入工位
  stop: [sid: string];                // 行内「停止」
  "new-session": [];                  // 顶部「+ 新工位」(满 3 时壳侧禁用)
  "focus-station": [id: number];      // 点已绑行 = 聚焦对应工位
  "close-drawer": [];                 // 抽屉模式:点罩层/选中后收抽屉
}>();

// 行点击按绑定态分流:已绑 → 聚焦工位;未绑 → 加入工位;两种都顺手收抽屉(宽窗壳侧忽略)
function onRow(r: RailRow) {
  if (r.boundStationId !== null) { emit("focus-station", r.boundStationId); emit("close-drawer"); }
  else { emit("join", r.id, r.agent); emit("close-drawer"); }
}
</script>

<template>
  <aside v-if="!drawer" class="rail">
    <div class="rail-head">
      <span class="rail-title">会话</span>
      <button type="button" class="rail-add" :disabled="addDisabled" @click="emit('new-session')">+ 新工位</button>
    </div>
    <div class="rail-rows">
      <div
        v-for="r in rows" :key="r.id" class="rail-row" :class="{ bound: r.boundStationId !== null }"
        @click="onRow(r)"
      >
        <div class="rail-row-title">{{ r.title }}</div>
        <div class="rail-row-sub">
          <span v-if="r.boundStationId !== null" class="rail-badge">工位 {{ r.boundStationId }}</span>
          {{ r.subtitle }}
        </div>
        <button
          v-if="r.live !== 'idle'" type="button" class="rail-stop"
          @click.stop="emit('stop', r.id)"
        >停止</button>
      </div>
      <div v-if="!rows.length" class="rail-empty">暂无会话</div>
    </div>
  </aside>
  <template v-else>
    <!-- 抽屉模式:罩层 + 左滑出面板;aside 内部结构与上方双写一致(不抽子组件) -->
    <div class="rail-mask" @click="emit('close-drawer')"></div>
    <aside class="rail rail-drawer">
      <div class="rail-head">
        <span class="rail-title">会话</span>
        <button type="button" class="rail-add" :disabled="addDisabled" @click="emit('new-session')">+ 新工位</button>
      </div>
      <div class="rail-rows">
        <div
          v-for="r in rows" :key="r.id" class="rail-row" :class="{ bound: r.boundStationId !== null }"
          @click="onRow(r)"
        >
          <div class="rail-row-title">{{ r.title }}</div>
          <div class="rail-row-sub">
            <span v-if="r.boundStationId !== null" class="rail-badge">工位 {{ r.boundStationId }}</span>
            {{ r.subtitle }}
          </div>
          <button
            v-if="r.live !== 'idle'" type="button" class="rail-stop"
            @click.stop="emit('stop', r.id)"
          >停止</button>
        </div>
        <div v-if="!rows.length" class="rail-empty">暂无会话</div>
      </div>
    </aside>
  </template>
</template>
