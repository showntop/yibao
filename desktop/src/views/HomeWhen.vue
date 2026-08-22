<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import HomeWidget from "./HomeWidget.vue";
import { whenFace, whenWeek } from "../lib/home/home-when-face.ts";

const face = ref(whenFace());
const week = ref(whenWeek());
let timer: ReturnType<typeof setInterval> | null = null;

function tick() {
  const now = new Date();
  face.value = whenFace(now);
  week.value = whenWeek(now);
}

onMounted(() => {
  tick();
  timer = setInterval(tick, 15_000);
});

onUnmounted(() => {
  if (timer) clearInterval(timer);
});
</script>

<template>
  <aside class="when">
    <HomeWidget id="when" aria-label="此刻">
      <p class="stamp">
        <time class="clock">{{ face.clock }}</time>
        <span>{{ face.weekday }} {{ face.date }}</span>
      </p>
      <ol class="week">
        <li v-for="day in week" :key="`${day.week}-${day.date}`" :data-today="day.today ? '1' : '0'">
          <small>{{ day.week }}</small>
          <b>{{ day.date }}</b>
        </li>
      </ol>
    </HomeWidget>
  </aside>
</template>

<style scoped>
.when { display: contents; }

.stamp {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin: 10px 12px 8px;
  color: var(--yb-paper-ink-dim);
  font-size: 11px;
}

.clock {
  color: var(--yb-paper-ink);
  font-size: 22px;
  font-weight: var(--yb-fw-medium);
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.04em;
  line-height: 1.15;
}

.week {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: 2px;
  margin: 0 8px 10px;
  padding: 0;
  list-style: none;
}

.week li {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1px;
  min-width: 0;
  padding: 3px 0 4px;
  border-radius: 6px;
  color: var(--yb-paper-ink-dim);
  font-variant-numeric: tabular-nums;
}

.week small {
  font-size: 9px;
  letter-spacing: 0.04em;
}

.week b {
  font-size: 11px;
  font-weight: var(--yb-fw-medium);
  line-height: 1.2;
}

.week li[data-today="1"] {
  background: var(--yb-note-mute);
  color: var(--yb-paper-ink);
}
</style>
