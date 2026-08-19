<script setup lang="ts">
// 引擎 chip + picker(R4 阶段二 T6,对齐 chat.html:1758-1833):
//   chip 三态——徽标态(有会话,.ro 显示 curSessAgent)/ 跨引擎待定(.sw 虚线显 switchAgent)/
//   无会话(显 curAgent);codex 不可用且无会话整颗 disabled;四种 title 文案对齐 :1769-1775。
//   picker = 弹层(absolute 锚 .ctx-row 向上展开,阶段四 T5 修:原 fixed 页右会盖相邻工位)
//   + backdrop(fixed 全窗,点击即关);两项(CC 全能力恒可选 / Codex 按可用),当前项 ✓
//   (有会话 = switchAgent||curSessAgent)。pick 语义(同引擎清待定/异引擎置待定/无会话设
//   curAgent)与 streaming/sending 点击拦截都在 App——本组件只上抛意图。
// 互收由 App 的单一 openLayer 兑现(开任一浮层即关其他);esc 优先级链也在 App。
import { computed } from "vue";
import { agentLabel } from "../stores/drivers";

const props = defineProps<{
  curAgent: string;              // 新会话引擎选择(drivers store)
  curSessAgent: string;          // 当前活动会话引擎(session store)
  switchAgent: string | null;    // 跨引擎切换待定(App)
  hasSession: boolean;
  codexAvailable: boolean | null; // null=未探测/探测失败(按可用呈现)
  open: boolean;
}>();
const emit = defineEmits<{
  toggle: [];          // chip 点击(streaming/sending 拦截在 App,带状态行提示)
  pick: [agent: string]; // picker 选中(仅可选项可点)
  close: [];           // backdrop 点击 → 关 picker
}>();

const ro = computed(() => props.hasSession);
const pending = computed(() => !!(ro.value && props.switchAgent && props.switchAgent !== props.curSessAgent));
const shown = computed(() => (pending.value ? props.switchAgent! : ro.value ? props.curSessAgent : props.curAgent));
// codex 不可用 → 无会话态整颗灰显禁用(picker 无可切换对象);徽标态不用 disabled(避免徽标变灰)
const unavailable = computed(() => !ro.value && props.codexAvailable === false);
const title = computed(() =>
  pending.value
    ? "将以 " + agentLabel(shown.value) + " 继续：发送时自动生成交接摘要（点击重新选择）"
    : ro.value
      ? "当前会话引擎：" + agentLabel(shown.value) + "（点击切换引擎，发送时自动交接摘要移植）"
      : unavailable.value
        ? "未检测到 codex CLI"
        : "引擎：" + agentLabel(shown.value) + "（点击选择引擎）");

// picker 两项:CC 全能力恒可选;Codex 按可用(不可用灰显禁选)
const engines = computed(() => [
  { id: "claude-code", label: "CC", desc: "Claude Code（全能力）", ok: true },
  {
    id: "codex",
    label: "Codex",
    desc: props.codexAvailable === false ? "未检测到 codex CLI" : "codex CLI 驱动",
    ok: props.codexAvailable !== false,
  },
]);
const current = computed(() => (props.hasSession ? props.switchAgent || props.curSessAgent : props.curAgent));
</script>

<template>
  <button
    id="agent-chip"
    type="button"
    class="pill"
    :class="{ ro, sw: pending }"
    :disabled="unavailable"
    :title="title"
    @click.stop="emit('toggle')"
  >{{ agentLabel(shown) }}</button>
  <!-- 引擎 picker:absolute 弹层(锚 .ctx-row 向上展开) + fixed backdrop(点击即关) -->
  <div v-if="open" id="agent-backdrop" @click="emit('close')"></div>
  <div v-if="open" id="agent-picker" @click.stop>
    <div
      v-for="e in engines"
      :key="e.id"
      class="pick-item"
      :class="{ disabled: !e.ok }"
      @click="e.ok && emit('pick', e.id)"
    >
      <span class="ag">{{ e.label }}{{ e.id === current ? " ✓" : "" }}</span>
      <span class="ds">{{ e.desc }}</span>
    </div>
  </div>
</template>
