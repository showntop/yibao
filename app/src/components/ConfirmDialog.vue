<script setup lang="ts">
defineProps<{ skill: string; desc: string }>();
const emit = defineEmits<{ (e: "approve"): void; (e: "deny"): void }>();
</script>

<template>
  <div class="dlg">
    <b>⚠️ 确认执行</b>
    <p><span class="skill">{{ skill }}</span>{{ desc ? "：" + desc : "" }}</p>
    <div class="btns">
      <button class="deny" @click="emit('deny')">拒绝</button>
      <button class="ok" @click="emit('approve')">允许</button>
    </div>
  </div>
</template>

<style scoped>
.dlg {
  padding: 12px;
  border-radius: 10px;
  border: 1px solid rgba(204, 51, 51, 0.5);
  background: rgba(255, 255, 255, 0.96);
  font-size: 14px;
}
b {
  color: #c33;
}
p {
  margin: 6px 0 10px;
  color: #333;
}
.skill {
  font-weight: 600;
}
.btns {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}
button {
  padding: 6px 14px;
  border-radius: 7px;
  border: none;
  cursor: pointer;
  font-size: 13px;
}
.ok {
  background: #2f7;
  color: #063;
}
.deny {
  background: #eee;
  color: #333;
}
</style>
