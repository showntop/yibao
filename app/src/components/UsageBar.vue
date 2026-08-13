<script setup lang="ts">
import { computed } from "vue";
import type { RunMetrics } from "../state/types";
import YbIcon from "./YbIcon.vue";

const props = defineProps<{ metrics: RunMetrics }>();

/** 耗时格式化：<1s 显示 ms，否则 x.xs */
const elapsedText = computed(() => {
  const ms = props.metrics.elapsed_ms;
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
});

/** token 总量（含缓存命中） */
const totalTokens = computed(() => props.metrics.total_tokens);

/** 费用：null（未知模型）显示 ——，否则保留 4 位小数（<0.0001 显示 <0.0001） */
const costText = computed(() => {
  const c = props.metrics.cost;
  if (c === null || c === undefined) return "—";
  if (c < 0.0001) return "<0.0001";
  return c.toFixed(4);
});

/** 缓存命中 token（详情 popover 显示） */
const cachedTokens = computed(() => props.metrics.cached_tokens);
/** 未命中输入 = prompt - cached */
const uncachedTokens = computed(() =>
  Math.max(0, props.metrics.prompt_tokens - props.metrics.cached_tokens)
);

/** token 千分位 */
function fmt(n: number): string {
  return n.toLocaleString("en-US");
}
</script>

<template>
  <!-- AI 回复下的 indicator bar：hover 显示明细 popover（token 拆分 / 费用 / 耗时 / 模型） -->
  <div class="usage-bar">
    <span class="ub-item" title="Token 用量">
      <YbIcon name="token" :size="12" />
      {{ fmt(totalTokens) }} tokens
    </span>
    <span class="ub-item" title="本次费用">
      <YbIcon name="coin" :size="12" />
      {{ costText }}
    </span>
    <span class="ub-item" title="耗时">
      <YbIcon name="timer" :size="12" />
      {{ elapsedText }}
    </span>

    <div class="ub-pop" role="tooltip">
      <div class="ub-pop-title">本次运行</div>
      <div class="ub-pop-grid">
        <span>Token 总量</span><strong>{{ fmt(totalTokens) }}</strong>
        <span>输入（未命中）</span><strong>{{ fmt(uncachedTokens) }}</strong>
        <span>输入（缓存命中）</span><strong>{{ fmt(cachedTokens) }}</strong>
        <span>输出</span><strong>{{ fmt(metrics.completion_tokens) }}</strong>
        <span>费用</span><strong>{{ costText }}</strong>
        <span>耗时</span><strong>{{ elapsedText }}</strong>
        <span v-if="metrics.model">模型</span><strong v-if="metrics.model">{{ metrics.model }}</strong>
      </div>
    </div>
  </div>
</template>

<style scoped>
.usage-bar {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 14px;
  margin: 6px 0 2px;
  padding: 3px 10px;
  border-radius: 8px;
  /* 跟随主题：浅色用 surface-2，深色自动覆盖 */
  background: var(--yb-surface-2);
  font-size: 11px;
  color: var(--yb-text-dim);
  user-select: none;
}
.ub-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
}
.ub-item svg {
  opacity: 0.6;
  flex-shrink: 0;
}
/* hover 明细 popover：跟随主题，浅色 = 浅浮卡，深色 = 深浮卡 */
.ub-pop {
  position: absolute;
  left: 0;
  top: calc(100% + 6px);
  min-width: 220px;
  padding: 10px 12px;
  border-radius: 10px;
  background: var(--yb-surface-1);
  border: 1px solid var(--yb-surface-3);
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.12);
  opacity: 0;
  pointer-events: none;
  transform: translateY(-4px);
  transition: opacity 0.15s ease, transform 0.15s ease;
  z-index: 30;
  font-size: 12px;
  color: var(--yb-text);
}
.usage-bar:hover .ub-pop {
  opacity: 1;
  pointer-events: auto;
  transform: translateY(0);
}
.ub-pop-title {
  font-size: 11px;
  color: var(--yb-text-dim);
  margin-bottom: 6px;
}
.ub-pop-grid {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 4px 16px;
}
.ub-pop-grid span {
  color: var(--yb-text-dim);
}
.ub-pop-grid strong {
  font-weight: 600;
  text-align: right;
  font-variant-numeric: tabular-nums;
}
</style>
