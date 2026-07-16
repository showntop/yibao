<script setup lang="ts">
defineProps<{ role: "user" | "ai"; text: string }>();
</script>

<template>
  <div :class="['bubble', role]">{{ text }}</div>
</template>

<style scoped>
.bubble {
  padding: 7px 11px;
  border-radius: 11px;
  max-width: 92%;
  font-size: 14px;
  line-height: 1.4;
  word-break: break-word;
}
.ai {
  background: rgba(238, 238, 255, 0.92);
  align-self: flex-start;
}
.user {
  background: rgba(223, 238, 221, 0.92);
  align-self: flex-end;
}
</style>
