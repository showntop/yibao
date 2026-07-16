<script setup lang="ts">
import { ref } from "vue";

const emit = defineEmits<{ (e: "submit", text: string): void }>();
const text = ref("");

function send() {
  const t = text.value.trim();
  if (t) {
    emit("submit", t);
    text.value = "";
  }
}
</script>

<template>
  <form @submit.prevent="send">
    <input v-model="text" placeholder="对译宝说点什么…" autofocus />
  </form>
</template>

<style scoped>
input {
  width: 100%;
  box-sizing: border-box;
  padding: 9px 10px;
  border-radius: 9px;
  border: 1px solid rgba(0, 0, 0, 0.15);
  background: rgba(255, 255, 255, 0.9);
  font-size: 14px;
  outline: none;
}
input:focus {
  border-color: #6aa6ff;
}
</style>
