<script setup lang="ts">
// 主屏「需要你决定」收件箱（自包含）：待批准队列的多选 + 顶部一键批/拒 + 每条独立 remember。
// 批量为主，单条快批为辅。共享样式在 assets/home-feed.css（.decide-card/.ap-*）。
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import YbIcon from "../../components/common/YbIcon.vue";
import {
  onPendingConfirms,
  sendConfirmBatch,
  canRememberTool,
  rememberLabelForTool,
  type PendingConfirm,
} from "../../lib/brain";

const approvals = ref<PendingConfirm[]>([]);
// 多选集合：N>1 时默认全选（鼓励批量）；N<=1 留空走单条快批按钮。
const selectedApprovals = ref<Set<string>>(new Set());
// 每条独立 remember（默认 false；勾选后该技能会话内不再询问——大脑侧会话级记忆）
const rememberMap = ref<Record<string, boolean>>({});

const selectedCount = computed(() => selectedApprovals.value.size);

/** brain.ts sendConfirmBatch 内部乐观出队后校正默认选择：
 *  N>1 全选鼓励批量；N<=1 清空走单条快批。同时清理 rememberMap 陈旧项。 */
watch(
  () => approvals.value,
  (l) => {
    if (l.length > 1) {
      selectedApprovals.value = new Set(l.map((p) => p.id));
    } else {
      selectedApprovals.value = new Set();
    }
    const live = new Set(l.map((p) => p.id));
    for (const k of Object.keys(rememberMap.value)) {
      if (!live.has(k)) delete rememberMap.value[k];
    }
  },
);

function isSelected(id: string): boolean {
  return selectedApprovals.value.has(id);
}

function onToggleSelect(id: string, e: Event) {
  const checked = (e.target as HTMLInputElement).checked;
  const next = new Set(selectedApprovals.value);
  if (checked) next.add(id);
  else next.delete(id);
  selectedApprovals.value = next;
}

function rememberOf(id: string): boolean {
  const approval = approvals.value.find((item) => item.id === id);
  return approval && canRememberTool(approval.tool_id) ? rememberMap.value[id] ?? false : false;
}

function onToggleRemember(id: string, e: Event) {
  rememberMap.value = { ...rememberMap.value, [id]: (e.target as HTMLInputElement).checked };
}

/** 单条快批/拒：调 sendConfirmBatch 单条（remember 按本条勾选）。
 *  乐观出队与失败回滚由 brain.ts 的共享队列统一处理；这里保留局部兜底。 */
async function decideApproval(p: PendingConfirm, approved: boolean) {
  const remember = rememberOf(p.id);
  try {
    await sendConfirmBatch([{ id: p.id, approved, remember }]);
  } catch {
    if (!approvals.value.some((x) => x.id === p.id)) {
      approvals.value = [...approvals.value, p];
    }
  }
}

/** 全部批准/拒绝：对选中的项调 sendConfirmBatch（按各条 remember 勾选）。 */
async function batchDecide(approved: boolean) {
  const targets = approvals.value.filter((p) => selectedApprovals.value.has(p.id));
  if (!targets.length) return;
  const list = targets.map((p) => ({
    id: p.id,
    approved,
    remember: rememberOf(p.id),
  }));
  // 局部快照：共享队列会回滚；此处兜底避免订阅链异常时卡片消失。
  const snapshot = targets.slice();
  try {
    await sendConfirmBatch(list);
    targets.forEach((p) => delete rememberMap.value[p.id]);
  } catch {
    const existing = new Set(approvals.value.map((p) => p.id));
    const restore = snapshot.filter((p) => !existing.has(p.id));
    if (restore.length) approvals.value = [...approvals.value, ...restore];
  }
}

let unApprovals: (() => void) | null = null;
onMounted(() => {
  unApprovals = onPendingConfirms((l) => (approvals.value = l));
});
onUnmounted(() => {
  unApprovals?.();
});
</script>

<template>
  <!-- 需要你决定：唯一必须你动手的事（琥珀强调，有才显） -->
  <section v-if="approvals.length" class="decide-card">
    <div class="decide-title">
      <YbIcon name="lock" :size="12" />需要你决定
      <span class="count yb-num">{{ approvals.length }}</span>
    </div>
    <div class="decide-body">
      <div
        v-for="p in approvals"
        :key="p.id"
        class="ap-card"
        :class="{ selected: approvals.length > 1 && isSelected(p.id) }"
      >
        <div class="ap-top">
          <label v-if="approvals.length > 1" class="ap-check" title="选中后可一键批量">
            <input type="checkbox" :checked="isSelected(p.id)" @change="onToggleSelect(p.id, $event)" />
          </label>
          <div class="ap-info">
            <strong class="ap-label">{{ p.label || p.tool_id }}</strong>
            <span class="ap-desc">{{ p.desc || p.tool_id }}</span>
          </div>
        </div>
        <label v-if="canRememberTool(p.tool_id)" class="ap-remember" :title="rememberLabelForTool(p.tool_id)">
          <input type="checkbox" :checked="rememberOf(p.id)" @change="onToggleRemember(p.id, $event)" />
          <span>{{ rememberLabelForTool(p.tool_id) }}</span>
        </label>
        <div class="ap-btns">
          <button class="btn-ghost" @click="decideApproval(p, false)">拒绝</button>
          <button class="btn-primary" @click="decideApproval(p, true)">批准</button>
        </div>
      </div>
      <div v-if="approvals.length > 1" class="ap-batch">
        <button class="btn-ghost" :disabled="selectedCount === 0" @click="batchDecide(false)">
          拒绝选中{{ selectedCount ? ` (${selectedCount})` : "" }}
        </button>
        <button class="btn-primary" :disabled="selectedCount === 0" @click="batchDecide(true)">
          批准选中{{ selectedCount ? ` (${selectedCount})` : "" }}
        </button>
      </div>
    </div>
  </section>
</template>
