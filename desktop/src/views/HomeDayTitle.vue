<script setup lang="ts">
// 日题：field 家态对话区的"空间题字"（design §8）。数据在 lib/home/day-title.ts。
import { onMounted, onUnmounted, ref } from "vue";
import { dayTitleFace } from "../lib/home/day-title.ts";

const main = ref(dayTitleFace().main);
let timer: ReturnType<typeof setInterval> | null = null;

function tick() {
  main.value = dayTitleFace(new Date()).main;
}

onMounted(() => {
  tick();
  timer = setInterval(tick, 30_000); // 日题以天为单位，30s 轮询只为跨零点换字
});

onUnmounted(() => {
  if (timer) clearInterval(timer);
});
</script>

<template>
  <h1 class="day-title">{{ main }}</h1>
</template>

<style scoped>
.day-title {
  margin: 0;
  font-family: var(--yb-font-serif);
  font-size: var(--yb-display-2);
  font-weight: var(--yb-fw-normal);
  line-height: 1.25;
  color: var(--yb-text-strong);
  letter-spacing: 0.02em;
  user-select: none;
}
</style>
