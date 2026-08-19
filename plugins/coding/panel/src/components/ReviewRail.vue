<script setup lang="ts">
// 右栏统一 review 栏(R4 阶段四 T4,纯展示):按会话分组的待批列表——组头(label + 计数 + 全批)、
// 卡片(tool 徽标 + summary + 允许/拒绝)。数据聚合在 review store(T2)、裁决回写在壳 App.vue(T5);
// 本组件只渲染 props 与转发 emits,零 invoke、零 store。
import type { ReviewItem } from "../stores/review";

interface ReviewGroup {
  sid: string;
  label: string;                 // 壳算好传入(stationId !== null → 工位 N;否则 sid 前 8 位)
  stationId: number | null;
  items: ReviewItem[];
}

defineProps<{
  groups: ReviewGroup[];
  drawer: boolean;  // 窄窗抽屉模式(壳传;宽窗忽略)
}>();
const emit = defineEmits<{
  decide: [rid: string, allow: boolean];      // 单条「允许/拒绝」
  "decide-group": [sid: string, allow: boolean]; // 组头「全批」
  "close-drawer": [];                         // 抽屉模式:点罩层收抽屉(裁决后是否收合由壳决定)
}>();

// params 细节不进卡面,拼成 title 悬停全文
function paramsTitle(it: ReviewItem) {
  return Object.entries(it.params).map(([k, v]) => `${k}: ${v}`).join("\n");
}
</script>

<template>
  <aside v-if="!drawer" class="review-rail">
    <div class="review-head">待批</div>
    <template v-for="g in groups" :key="g.sid">
      <!-- 空组不渲染(壳已保证不传,此处再守一道) -->
      <div v-if="g.items.length" class="review-group">
        <div class="review-group-head">
          <span>{{ g.label }} · {{ g.items.length }}</span>
          <button type="button" class="review-approve-all" @click="emit('decide-group', g.sid, true)">全批</button>
        </div>
        <div v-for="it in g.items" :key="it.rid" class="review-card" :title="paramsTitle(it)">
          <span class="review-card-tool">{{ it.tool }}</span>
          <div class="review-card-summary">{{ it.summary }}</div>
          <button type="button" class="review-allow" @click="emit('decide', it.rid, true)">允许</button>
          <button type="button" class="review-deny" @click="emit('decide', it.rid, false)">拒绝</button>
        </div>
      </div>
    </template>
  </aside>
  <template v-else>
    <!-- 抽屉模式:罩层 + 右滑出面板;aside 内部结构与上方双写一致(不抽子组件) -->
    <div class="review-mask" @click="emit('close-drawer')"></div>
    <aside class="review-rail review-drawer">
      <div class="review-head">待批</div>
      <template v-for="g in groups" :key="g.sid">
        <div v-if="g.items.length" class="review-group">
          <div class="review-group-head">
            <span>{{ g.label }} · {{ g.items.length }}</span>
            <button type="button" class="review-approve-all" @click="emit('decide-group', g.sid, true)">全批</button>
          </div>
          <div v-for="it in g.items" :key="it.rid" class="review-card" :title="paramsTitle(it)">
            <span class="review-card-tool">{{ it.tool }}</span>
            <div class="review-card-summary">{{ it.summary }}</div>
            <button type="button" class="review-allow" @click="emit('decide', it.rid, true)">允许</button>
            <button type="button" class="review-deny" @click="emit('decide', it.rid, false)">拒绝</button>
          </div>
        </div>
      </template>
    </aside>
  </template>
</template>
