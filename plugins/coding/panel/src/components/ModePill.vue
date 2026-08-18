<script setup lang="ts">
// 权限模式 pill(R4 阶段二 T6,对齐 chat.html:1856-1864):两态切换(自动改文件 ⇄ 只读规划),
// plan 态强调色(.plan)。翻转与有活动会话时的 coding.mode 通知(catch 静默)在 App。
defineProps<{ mode: string }>();
const emit = defineEmits<{ toggle: [] }>();
</script>

<template>
  <button
    id="mode-pill"
    type="button"
    class="pill"
    :class="{ plan: mode === 'plan' }"
    title="点击切换权限模式"
    @click="emit('toggle')"
  >{{ mode === "plan" ? "只读规划" : "自动改文件" }}</button>
</template>
