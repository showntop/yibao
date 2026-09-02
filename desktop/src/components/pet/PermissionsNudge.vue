<script setup lang="ts">
// 权限卡按需降级（9-01 P2-01 / 8-31 P0-7）：默认一行低干扰状态条，点击展开完整引导；
// 展开态可重新收起（偏好持久化，两窗共享）；demand = 电脑控制工具被调用而权限缺失，
// 能力真正需要权限时就地自动展开（会话级，不改用户偏好）。
import { computed, ref, watch } from "vue";
import PermissionsBanner from "./PermissionsBanner.vue";
import { permsNudgeCollapsed, setPermsNudgeCollapsed } from "../../lib/perms-nudge";
import type { BrainPermissions } from "../../lib/brain";

const props = withDefaults(defineProps<{ perms: BrainPermissions; demand?: boolean }>(), { demand: false });

// demand 是一次性信号（父窗置 true 后保持）：消费成会话级展开标记，用户可再收起
const demandOpen = ref(false);
watch(
  () => props.demand,
  (d) => { if (d) demandOpen.value = true; },
  { immediate: true },
);

const expanded = computed(() => demandOpen.value || !permsNudgeCollapsed.value);

// 与 PermissionsBanner 的 PERM_DEFS 同序（引导顺序）
const PERM_LABELS: Record<"ax" | "screen" | "input", string> = {
  ax: "辅助功能",
  input: "输入监控",
  screen: "屏幕录制",
};
const missingLabels = computed(() =>
  (Object.keys(PERM_LABELS) as ("ax" | "screen" | "input")[])
    .filter((k) => !props.perms[k])
    .map((k) => PERM_LABELS[k]),
);

function expand() {
  demandOpen.value = false;
  setPermsNudgeCollapsed(false);
}
function collapse() {
  demandOpen.value = false;
  setPermsNudgeCollapsed(true);
}
</script>

<template>
  <div v-if="expanded" class="nudge-open">
    <PermissionsBanner :perms="perms" />
    <div class="fold-row">
      <button class="fold" @click="collapse">收起权限引导</button>
    </div>
  </div>
  <button v-else class="nudge-bar" title="展开权限引导" @click="expand">
    <i class="dot" />
    <span class="msg">电脑控制未授权<template v-if="missingLabels.length">（{{ missingLabels.join("、") }}）</template></span>
    <span class="go">去开启</span>
  </button>
</template>

<style scoped>
/* 降级一行条：与 PermissionsBanner 同瓷白材质，单行高、低干扰 */
.nudge-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  box-sizing: border-box;
  padding: 7px 12px;
  border-radius: var(--yb-radius-lg);
  background: var(--yb-surface-solid);
  border: 1px solid var(--yb-glass-border);
  font-family: inherit;
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-dim);
  cursor: pointer;
  transition: border-color var(--yb-dur-fast) var(--yb-ease-out);
}
.nudge-bar:hover {
  border-color: var(--yb-accent);
}
.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex: none;
  background: var(--yb-danger);
}
.msg {
  flex: 1;
  min-width: 0;
  text-align: left;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.go {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 2px;
  color: var(--yb-accent);
  font-weight: var(--yb-fw-medium);
}
.nudge-open {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 4px;
}
.fold {
  border: none;
  background: none;
  padding: 0 4px;
  font-family: inherit;
  font-size: var(--yb-fs-xs);
  color: var(--yb-text-faint);
  cursor: pointer;
}
.fold:hover {
  color: var(--yb-text-dim);
}
</style>
