<script setup lang="ts">
/** 溪场窄屏的器物架投影：顺序必须与宽屏 shelf 一致。 */
import HomeProject from "./HomeProject.vue";
import HomeRemindCard from "./HomeRemindCard.vue";
import HomeShelfStats from "./HomeShelfStats.vue";

defineProps<{ sessionId?: string }>();
defineEmits<{ chat: [draft: string] }>();
</script>

<template>
  <HomeProject :session-id="sessionId" @chat="$emit('chat', $event)" />
  <HomeRemindCard force />
  <HomeShelfStats />
</template>
