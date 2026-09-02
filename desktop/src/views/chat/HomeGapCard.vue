<script setup lang="ts">
// 能力边界卡（对话流内联）：project.create 立项时 preflight 检出 enforce 缺口。
// 信息卡而非审批卡——没有要裁决的事，不发明按钮；装齐 provider 后边界自然消失。
import { computed } from "vue";
import YbIcon from "../../components/common/YbIcon.vue";
import { capabilityGapTitle, type CapabilityGap } from "../../lib/home/capability-gap.ts";

const props = defineProps<{ gap: CapabilityGap }>();
const title = computed(() => capabilityGapTitle(props.gap));
</script>

<template>
  <div class="gap-card" role="note" :aria-label="title">
    <div class="gap-head">
      <YbIcon class="gap-ic" name="info" :size="13" />
      <strong>{{ title }}</strong>
    </div>
    <ul class="gap-stages">
      <li v-for="s in gap.available" :key="`ok-${s}`" class="gap-stage ok">{{ s }}</li>
      <li v-for="s in gap.missing" :key="`miss-${s}`" class="gap-stage miss">{{ s }}</li>
    </ul>
    <p v-if="gap.note" class="gap-note">{{ gap.note }}</p>
  </div>
</template>

<style scoped>
/* 与按印卡同一琥珀语言（待你处置），器型是信息卡：无边框告警、无按钮 */
.gap-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  align-self: flex-start;
  max-width: min(100%, 36em);
  margin: var(--yb-space-2) var(--yb-space-3);
  padding: 10px 14px;
  border: 1px solid rgba(var(--yb-c-amber-rgb), 0.28);
  border-radius: var(--yb-radius-md);
  background: var(--yb-intent-pending-soft);
  box-shadow: var(--yb-shadow-1);
  line-height: var(--yb-lh-ui);
}
.gap-head {
  display: flex;
  align-items: center;
  gap: var(--yb-space-1);
  color: var(--yb-text);
  font-size: var(--yb-fs-md);
}
.gap-ic { color: var(--yb-intent-pending-ink); }
.gap-stages {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.gap-stage {
  padding: 1px 8px;
  border-radius: var(--yb-radius-pill);
  font-size: var(--yb-fs-sm);
}
/* 缺能力段为主体（琥珀虚边）；可达段弱化（灰），只交代边界位置 */
.gap-stage.miss {
  border: 1px dashed rgba(var(--yb-c-amber-rgb), 0.55);
  color: var(--yb-intent-pending-ink);
}
.gap-stage.ok {
  background: var(--yb-c-slate-100);
  color: var(--yb-text-faint);
}
.gap-note {
  margin: 0;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-sm);
}
</style>
