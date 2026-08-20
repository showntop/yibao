<script setup lang="ts">
// 终止标/提示 marker:发丝线分隔(文本居中、两侧横线),err 红态。
defineProps<{ text: string; err: boolean }>();
</script>

<template>
  <div class="marker" :class="{ err }">{{ text }}</div>
</template>
