<!-- app/src/components/SurfaceLine.vue -->
<script setup lang="ts">
// 带表面属性的行（Phase 1.5）：可点时是入口，失活后是历史。
// 小窗不做卡——卡不承载撤销，多提供的信息接近于零，却在 360px 里顶满一整块。
// 不显示 ✓：失败结果本就不带表面建议，「有表面」已蕴含「成功了」。
import type { SurfaceAttr } from "../lib/pet-surface";

const props = defineProps<{ attr: SurfaceAttr }>();
const emit = defineEmits<{ open: [] }>();

function onOpen(): void {
  if (props.attr.live) emit("open");
}
</script>

<template>
  <div
    :class="['s-line', attr.live ? 'is-live' : 'is-past']"
    :role="attr.live ? 'button' : undefined"
    :tabindex="attr.live ? 0 : undefined"
    @click="onOpen"
    @keydown.enter="onOpen"
  >
    <span>{{ attr.title }}<template v-if="attr.count !== null"> · {{ attr.count }} 条</template></span>
    <span v-if="attr.live" class="sl-ar" aria-hidden="true">›</span>
  </div>
</template>

<style scoped>
.s-line {
  align-self: flex-start;
  display: flex;
  align-items: center;
  gap: var(--yb-space-1);
  padding: 2px 2px;
  font-size: var(--yb-fs-sm);
  /* 无边无底：行不该有卡感 */
  background: transparent;
  border: none;
}
.is-live {
  color: var(--yb-accent);
  cursor: pointer;
}
.is-live:hover {
  text-decoration: underline;
}
/* 失活：只剩「发生过什么」，不承担导航——回插件视图才是导航入口 */
.is-past {
  color: var(--yb-text-faint);
  cursor: default;
}
.sl-ar {
  color: var(--yb-text-faint);
}
</style>
