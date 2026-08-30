<script setup lang="ts">
// 日题：field 家态对话区的"空间题字"（design §8）。数据在 lib/home/day-title.ts；
// 陪伴天数读 sidecar 落盘的 shell.first_seen（拿不到就只显日期，不造假）。
import { onMounted, onUnmounted, ref } from "vue";
import { dayTitleFace } from "../lib/home/day-title.ts";
import { getSettingsOnce } from "../services/brainClient";

const face = ref(dayTitleFace());
let timer: ReturnType<typeof setInterval> | null = null;

function tick() {
  face.value = dayTitleFace(new Date(), firstSeen.value ?? undefined);
}

const firstSeen = ref<number | null>(null);

onMounted(async () => {
  tick();
  timer = setInterval(tick, 30_000); // 日题以天为单位，30s 轮询只为跨零点换字
  try {
    const values = await getSettingsOnce();
    const raw = values?.["shell.first_seen"];
    const ts = raw ? new Date(String(raw)).getTime() : NaN;
    if (Number.isFinite(ts)) firstSeen.value = ts;
    tick();
  } catch {
    /* 设置不可得：日题只显日期 */
  }
});

onUnmounted(() => {
  if (timer) clearInterval(timer);
});
</script>

<template>
  <div class="day-title-wrap">
    <h1 class="day-title">{{ face.main }}</h1>
    <p v-if="face.sub" class="day-sub">{{ face.sub }}</p>
  </div>
</template>

<style scoped>
.day-title-wrap {
  min-width: 0;
}
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
.day-sub {
  margin: 2px 0 0;
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-faint);
  user-select: none;
}
</style>
