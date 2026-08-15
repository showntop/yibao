<script setup lang="ts">
defineProps<{ state: "idle" | "connecting" | "open" | "error" }>();
const TEXT = { idle: "未连接", connecting: "连接中…", open: "已连接", error: "连接断开，重连中…" } as const;
const COLOR = { idle: "#999", connecting: "#e6a700", open: "#34c759", error: "#ff453a" } as const;
</script>

<template>
  <div class="bar"><span class="dot" :style="{ background: COLOR[state] }" />{{ TEXT[state] }}</div>
</template>

<style scoped>
.bar { display: flex; align-items: center; gap: 6px; padding: 6px 14px; font-size: 12px; opacity: 0.8; }
.dot { width: 8px; height: 8px; border-radius: 50%; }
</style>
