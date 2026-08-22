<script setup lang="ts">
import { computed, ref } from "vue";
import { canRememberSkill, rememberLabelForSkill } from "../../lib/brain";
import YbIcon from "./YbIcon.vue";

const props = defineProps<{ skill: string; desc: string }>();
const emit = defineEmits<{ (e: "approve", remember: boolean): void; (e: "deny"): void }>();
const remember = ref(false);
const canRemember = computed(() => canRememberSkill(props.skill));
</script>

<template>
  <div class="dlg">
    <div class="title"><YbIcon name="alert" :size="16" /> 确认执行高风险操作</div>
    <p><span class="skill">{{ skill }}</span>{{ desc ? " · " + desc : "" }}</p>
    <label v-if="canRemember" class="remember">
      <input type="checkbox" v-model="remember" />
      {{ rememberLabelForSkill(skill) }}
    </label>
    <div class="btns">
      <button class="deny" @click="emit('deny')">拒绝</button>
      <button class="ok" @click="emit('approve', canRemember && remember)">允许执行</button>
    </div>
  </div>
</template>

<style scoped>
.dlg {
  padding: var(--yb-space-3);
  border-radius: var(--yb-radius-lg);
  background: var(--yb-surface-solid);
  border: 1px solid var(--yb-danger-soft);
  box-shadow: var(--yb-shadow);
}
.title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: var(--yb-fw-bold);
  font-size: var(--yb-fs-lg);
  color: var(--yb-danger);
}
p {
  margin: var(--yb-space-2) 0 var(--yb-space-3);
  color: var(--yb-text);
  font-size: var(--yb-fs-lg);
  line-height: var(--yb-lh-ui);
}
.skill {
  font-weight: var(--yb-fw-bold);
}
.remember {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 var(--yb-space-3);
  font-size: var(--yb-fs-md);
  color: var(--yb-text-dim);
  cursor: pointer;
  user-select: none;
}
.remember input {
  accent-color: var(--yb-accent);
  margin: 0;
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
  font-weight: var(--yb-fw-medium);
  transition: filter var(--yb-dur-fast);
}
.ok {
  background: var(--yb-accent);
  color: var(--yb-text-on-accent);
}
.deny {
  background: var(--yb-btn-neutral);
  color: var(--yb-text-dim);
}
.ok:hover,
.deny:hover {
  filter: brightness(0.96);
}
</style>
