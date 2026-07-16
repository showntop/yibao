<script setup lang="ts">
defineProps<{ state: "idle" | "listen" | "think" | "work" }>();
const faces: Record<string, string> = {
  idle: "😌",
  listen: "👂",
  think: "🤔",
  work: "⚙️",
};
</script>

<template>
  <div class="av" :class="state">{{ faces[state] }}</div>
</template>

<style scoped>
.av {
  width: 72px;
  height: 72px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font-size: 38px;
  background: rgba(255, 255, 255, 0.75);
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.18);
  transition: transform 0.2s;
}
.av.think,
.av.work {
  animation: pulse 1s infinite alternate;
}
@keyframes pulse {
  from {
    opacity: 0.6;
    transform: scale(0.96);
  }
  to {
    opacity: 1;
    transform: scale(1.04);
  }
}
</style>
