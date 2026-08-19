<script setup lang="ts">
// @ 引用 chips 行(移植 chat.html:2687-2704 renderAtRefs):basename 显示、title 悬停全路径、
// × 删除、空整行隐藏。数据由 Composer 持有,本组件纯渲染 + remove 事件上抛。
import { basename } from "../lib/refs";

defineProps<{ refs: string[] }>();
const emit = defineEmits<{ remove: [index: number] }>();
</script>

<template>
  <div v-if="refs.length" id="at-refs" class="at-refs">
    <span v-for="(rel, i) in refs" :key="rel" class="at-ref" :title="rel">
      <span>{{ basename(rel) }}</span>
      <button type="button" title="移除引用" @click="emit('remove', i)">×</button>
    </span>
  </div>
</template>
