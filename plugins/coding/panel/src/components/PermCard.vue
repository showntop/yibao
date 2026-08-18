<script setup lang="ts">
// 权限审批卡·只读镜像(对齐 chat.html:1536-1563 + :1662-1674):无按钮——裁决统一走 L2 确认体系
// (PanelApp 顶部确认条 / HomeFeed 收件箱),卡面只提示去向。等待态琥珀脉冲边框;
// permission_done 后收敛灰小字单行「✓ 已允许 <verb>」/「✗ 已拒绝」(60s 超时后端默认拒绝逻辑不动)。
import { computed } from "vue";
import type { RenderItem } from "../stores/session";
import { permVerb } from "../lib/tools";

type PermItem = Extract<RenderItem, { type: "perm" }>;
const props = defineProps<{ item: PermItem }>();

const verb = computed(() => permVerb(props.item.tool));

// input JSON 摘要:>300 字截断
const inputStr = computed(() => {
  let s: string;
  try { s = JSON.stringify(props.item.input ?? {}); }
  catch { s = String(props.item.input); }
  return s.length > 300 ? s.slice(0, 300) + "…" : s;
});

// 收敛单行:verb 为默认「允许」时不重复缀(「✓ 已允许」而非「✓ 已允许 允许」)
const doneText = computed(() =>
  props.item.state === "allowed"
    ? "✓ 已允许" + (verb.value !== "允许" ? " " + verb.value : "")
    : "✗ 已拒绝",
);
</script>

<template>
  <!-- 直挂消息带(无 .row 包裹,对齐 appendPermission):等待态整卡,裁决后原地收敛单行 -->
  <div class="card perm-card" :class="{ 'perm-done': item.state !== 'waiting' }">
    <template v-if="item.state === 'waiting'">
      <div class="perm-head">🔐 「{{ item.tool }}」需要许可</div>
      <pre class="perm-input">{{ inputStr }}</pre>
      <div class="perm-state">⏳ 等待审批…在顶部确认条或主屏收件箱处理</div>
    </template>
    <template v-else>{{ doneText }}</template>
  </div>
</template>
