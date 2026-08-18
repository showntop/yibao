<script setup lang="ts">
// footer 状态行(R4 阶段二 T6):灰小字单行,spinner 可选,err 红态。
// 文本来源在 App(store 状态行与 Composer/头部控件瞬时提示共一位,后写赢)。
defineProps<{ text: string; spin: boolean; err: boolean }>();
</script>

<template>
  <span id="status" :class="{ err }"><span v-if="spin" class="spin"></span>{{ text }}</span>
</template>
