<script setup lang="ts">
// 会话文件改动浮层(2026-09 交互重构):本会话全部 file_edit 项聚合回看。复用 FileEditCard
// 渲染(同一 RenderItem 对象,diff 体直接展开);罩层 absolute 相对工位根(同接续浮层定位模式),
// 点罩层/「完成」关闭。
import type { RenderItem } from "../stores/session";
import FileEditCard from "./FileEditCard.vue";

defineProps<{ items: Array<Extract<RenderItem, { type: "fileedit" }>> }>();
const emit = defineEmits<{ close: [] }>();
</script>

<template>
  <div id="changes-overlay">
    <div class="changes-mask" @click="emit('close')"></div>
    <div class="changes-panel" role="dialog" aria-label="本会话文件改动">
      <div class="changes-head">
        <span class="changes-title">本会话文件改动 <span class="changes-count">{{ items.length }}</span></span>
        <button type="button" class="changes-close" @click="emit('close')">完成</button>
      </div>
      <div class="changes-body">
        <p v-if="!items.length" class="changes-empty">本会话还没有文件改动</p>
        <div v-for="(it, i) in items" :key="i" class="changes-item">
          <FileEditCard :item="it" />
        </div>
      </div>
    </div>
  </div>
</template>
