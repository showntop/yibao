<script setup lang="ts">
// 终止标/提示 marker(对齐 chat.html:1154-1160):居中灰小字 pill,err 红态。
defineProps<{ text: string; err: boolean }>();
</script>

<template>
  <div class="marker" :class="{ err }">{{ text }}</div>
</template>
