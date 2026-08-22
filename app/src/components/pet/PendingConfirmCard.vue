<script setup lang="ts">
// 待批准确认卡（宠物窗输入槽区域的琥珀条）：单条批准/拒绝（可选记住选择）或批量快批。
// remember 状态归父级（usePetApproval 域），经 v-model:remember 双向绑定；
// 决策动作（decide/decideAllPending）是父级 composable 的事，经事件上抛。
import YbIcon from "../YbIcon.vue";
import { rememberLabelForSkill, type PendingConfirm } from "../../lib/brain";

defineProps<{
  pending: PendingConfirm;
  /** 队列长度（>1 时切换为批量快批条视图）。 */
  count: number;
  canRemember: boolean;
  remember: boolean;
}>();

const emit = defineEmits<{
  "update:remember": [value: boolean];
  decide: [approved: boolean, remember?: boolean];
  "decide-all": [approved: boolean];
  "open-home": [];
}>();
</script>

<template>
  <!-- 批量快批条：多确认聚合为一条（1 个取消/全部批准/全部拒绝），逐项核对去收件箱 -->
  <div v-if="count > 1" class="batch-confirm-notice">
    <div class="batch-copy">
      <strong>{{ count }} 项待批准</strong>
      <span>逐项核对或分别记住选择，请打开收件箱。</span>
    </div>
    <div class="batch-actions">
      <button class="quick-deny" @click="emit('decide-all', false)">全部拒绝</button>
      <button class="quick-allow" @click="emit('decide-all', true)">全部批准</button>
      <button class="confirm-open" @click="emit('open-home')">打开收件箱</button>
    </div>
  </div>
  <!-- 单条确认卡：技能确认（安全委派）+ 撤回 -->
  <div v-else class="quick-confirm">
    <div class="quick-copy">
      <strong><YbIcon class="qc-ic" name="alert" :size="14" />{{ pending.label || pending.skill }}</strong>
      <span v-if="pending.desc">{{ pending.desc }}</span>
    </div>
    <label v-if="canRemember" class="quick-remember">
      <input
        type="checkbox"
        :checked="remember"
        @change="emit('update:remember', ($event.target as HTMLInputElement).checked)"
      />
      {{ rememberLabelForSkill(pending.skill) }}
    </label>
    <div class="quick-actions">
      <button class="quick-deny" @click="emit('decide', false)">拒绝</button>
      <button class="quick-allow" @click="emit('decide', true, remember)">允许</button>
    </div>
  </div>
</template>

<style scoped>
.quick-confirm,
.batch-confirm-notice {
  display: flex;
  align-items: center;
  gap: var(--yb-space-2);
  padding: 9px 10px;
  margin: 0 2px 10px;
  border: 1px solid rgba(var(--yb-c-amber-rgb), 0.32);
  border-radius: var(--yb-radius-md);
  background: var(--yb-intent-pending-soft);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.7);
}
.quick-copy,
.batch-copy {
  min-width: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
  line-height: var(--yb-lh-ui);
}
.quick-copy strong,
.batch-confirm-notice strong {
  overflow: hidden;
  color: var(--yb-text);
  font-size: var(--yb-fs-md);
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 快批条行首图标：待批准意图琥珀，与收件箱同语言 */
.qc-ic {
  color: var(--yb-intent-pending-ink);
  margin-right: var(--yb-space-1);
}
.quick-copy span,
.batch-confirm-notice span {
  overflow: hidden;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-sm);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.quick-remember {
  display: flex;
  align-items: center;
  gap: 4px;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-sm);
  white-space: nowrap;
}
.quick-remember input {
  margin: 0;
  accent-color: var(--yb-accent);
}
.quick-actions,
.batch-actions {
  display: flex;
  gap: 5px;
}
.quick-actions button,
.batch-actions button,
.confirm-open {
  min-height: 32px;
  padding: 6px 10px;
  border: 0;
  border-radius: var(--yb-radius-sm);
  cursor: pointer;
  font: inherit;
  white-space: nowrap;
}
.quick-deny {
  color: var(--yb-text-dim);
  background: var(--yb-btn-neutral);
}
.quick-allow,
.confirm-open {
  color: var(--yb-text-on-accent);
  background: var(--yb-accent);
}
.quick-actions button:focus-visible,
.batch-actions button:focus-visible,
.confirm-open:focus-visible {
  outline: none;
  box-shadow: var(--yb-focus-ring);
}
</style>
