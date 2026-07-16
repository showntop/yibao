<script setup lang="ts">
defineProps<{ role: "user" | "ai"; text: string }>();
</script>

<template>
  <div :class="['bubble', role]">{{ text }}</div>
</template>

<style scoped>
.bubble {
  padding: 8px 12px;
  border-radius: 14px;
  max-width: 88%;
  font-size: 13.5px;
  line-height: 1.45;
  word-break: break-word;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
  animation: pop 0.18s ease-out;
}
.ai {
  background: var(--yb-bubble-ai);
  color: var(--yb-text);
  align-self: flex-start;
  border-bottom-left-radius: 4px;
}
.user {
  background: var(--yb-bubble-user);
  color: var(--yb-text);
  align-self: flex-end;
  border-bottom-right-radius: 4px;
}
@keyframes pop {
  from {
    opacity: 0;
    transform: translateY(4px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}
</style>
