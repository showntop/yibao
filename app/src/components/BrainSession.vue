<script setup lang="ts">
import AgentBrain from "./AgentBrain.vue";
import HomeGlance from "./HomeGlance.vue";
import HomePluginGlance from "./HomePluginGlance.vue";

type AgentState = "idle" | "listen" | "think" | "work" | "say" | "success" | "error";

defineProps<{ state: AgentState }>();
const emit = defineEmits<{
  chat: [draft: string];
}>();
</script>

<template>
  <AgentBrain :state="state" compact @chat="(d) => emit('chat', d)" />
  <HomeGlance @chat="(d) => emit('chat', d)" />
  <HomePluginGlance />
</template>
