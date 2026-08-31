<script setup lang="ts">
// 按印闸门卡（对话流内联）：L3 确认是正式对话回合——agent 汇报 → 人按印/否决。
// 不再只落小窗输入槽/死徽章；裁决经 sendConfirmBatch 回大脑（乐观出队），
// 后续 agent 消息进流即为记录（地平线 echo 同步留痕）。
import YbIcon from "../../components/common/YbIcon.vue";
import { rememberLabelForTool, type PendingConfirm } from "../../lib/brain";

defineProps<{
  pending: PendingConfirm;
  /** 队列长度（>1 时切换批量行） */
  count: number;
  busy?: boolean;
  error?: string;
  canRemember: boolean;
  remember: boolean;
  /** toast 定位脉冲（父级信号触发，短暂高亮边框） */
  flash?: boolean;
}>();

const emit = defineEmits<{
  "update:remember": [value: boolean];
  decide: [approved: boolean];
  decideAll: [approved: boolean];
}>();
</script>

<template>
  <div class="gate-card" :class="{ flash }" role="alertdialog" aria-label="待你按印">
    <template v-if="count > 1">
      <div class="gate-copy">
        <strong><YbIcon class="gate-ic" name="alert" :size="14" />{{ count }} 项待你按印</strong>
        <span>逐条核对更安全；确认都是同一类操作再整批复。</span>
      </div>
      <div class="gate-actions">
        <button type="button" class="gate-deny" :disabled="busy" @click="emit('decideAll', false)">全部否决</button>
        <button type="button" class="gate-allow" :disabled="busy" @click="emit('decideAll', true)">全部按印</button>
      </div>
    </template>
    <template v-else>
      <div class="gate-copy">
        <strong><YbIcon class="gate-ic" name="alert" :size="14" />{{ pending.label || pending.tool_id }}</strong>
        <span v-if="pending.desc">{{ pending.desc }}</span>
        <span v-if="error" class="gate-err">{{ error }}</span>
      </div>
      <label v-if="canRemember" class="gate-remember">
        <input
          type="checkbox"
          :checked="remember"
          @change="emit('update:remember', ($event.target as HTMLInputElement).checked)"
        />
        {{ rememberLabelForTool(pending.tool_id) }}
      </label>
      <div class="gate-actions">
        <button type="button" class="gate-deny" :disabled="busy" @click="emit('decide', false)">否决</button>
        <button type="button" class="gate-allow" :disabled="busy" @click="emit('decide', true)">按印</button>
      </div>
    </template>
  </div>
</template>

<style scoped>
/* 与 PendingConfirmCard 同一琥珀语言（待你定），器型是大窗瓷片 */
.gate-card {
  display: flex;
  align-items: center;
  gap: var(--yb-space-3);
  margin: var(--yb-space-2) var(--yb-space-3);
  padding: 10px 14px;
  border: 1px solid rgba(var(--yb-c-amber-rgb), 0.4);
  border-radius: var(--yb-radius-md);
  background: var(--yb-intent-pending-soft);
  box-shadow: var(--yb-shadow-1);
  transition: box-shadow 0.3s ease, border-color 0.3s ease;
}
/* toast 定位脉冲：边框与阴影短暂加粗，告诉眼睛"在这" */
.gate-card.flash {
  border-color: var(--yb-intent-pending-ink);
  box-shadow: 0 0 0 3px rgba(var(--yb-c-amber-rgb), 0.35), var(--yb-shadow-2);
}
.gate-copy {
  min-width: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
  line-height: var(--yb-lh-ui);
}
.gate-copy strong {
  overflow: hidden;
  color: var(--yb-text);
  font-size: var(--yb-fs-md);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.gate-ic {
  margin-right: var(--yb-space-1);
  color: var(--yb-intent-pending-ink);
  vertical-align: -2px;
}
.gate-copy span {
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-sm);
}
.gate-err {
  color: var(--yb-intent-danger-ink, #b3403c);
}
.gate-remember {
  display: flex;
  align-items: center;
  gap: 4px;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-sm);
  white-space: nowrap;
}
.gate-remember input {
  margin: 0;
  accent-color: var(--yb-accent);
}
.gate-actions {
  display: flex;
  gap: 6px;
}
.gate-actions button {
  min-height: 32px;
  padding: 6px 14px;
  border: 0;
  border-radius: var(--yb-radius-sm);
  cursor: pointer;
  font: inherit;
  white-space: nowrap;
}
.gate-actions button:disabled {
  cursor: default;
  opacity: 0.6;
}
.gate-deny {
  color: var(--yb-text-dim);
  background: var(--yb-btn-neutral);
}
.gate-allow {
  color: var(--yb-text-on-accent);
  background: var(--yb-accent);
}
.gate-actions button:focus-visible {
  outline: none;
  box-shadow: var(--yb-focus-ring);
}
</style>
