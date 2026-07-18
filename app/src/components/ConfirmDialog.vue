<script setup lang="ts">
defineProps<{ skill: string; desc: string }>();
const emit = defineEmits<{ (e: "approve"): void; (e: "deny"): void }>();
</script>

<template>
  <div class="dlg">
    <div class="title"><span class="icon">⚠️</span> 确认执行高风险操作</div>
    <p><span class="skill">{{ skill }}</span>{{ desc ? " · " + desc : "" }}</p>
    <div class="btns">
      <button class="deny" @click="emit('deny')">拒绝</button>
      <button class="ok" @click="emit('approve')">允许执行</button>
    </div>
  </div>
</template>

<style scoped>
.dlg {
  padding: var(--yb-space-3);
  border-radius: var(--yb-radius-lg);
  background: rgba(255, 255, 255, 0.96);
  border: 1px solid var(--yb-danger-soft);
  box-shadow: var(--yb-shadow);
}
.title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  font-size: 14px;
  color: var(--yb-danger);
}
.icon {
  font-size: 16px;
}
p {
  margin: var(--yb-space-2) 0 var(--yb-space-3);
  color: var(--yb-text);
  font-size: 13px;
  line-height: 1.4;
}
.skill {
  font-weight: 600;
}
.btns {
  display: flex;
  gap: var(--yb-space-2);
  justify-content: flex-end;
}
button {
  padding: 7px 16px;
  border-radius: var(--yb-radius-sm);
  border: none;
  cursor: pointer;
  font-size: var(--yb-fs-md);
  font-weight: 500;
  transition: filter 0.15s;
}
.ok {
  background: var(--yb-accent);
  color: #fff;
}
.deny {
  background: rgba(0, 0, 0, 0.06);
  color: var(--yb-text-dim);
}
.ok:hover,
.deny:hover {
  filter: brightness(0.96);
}
</style>
