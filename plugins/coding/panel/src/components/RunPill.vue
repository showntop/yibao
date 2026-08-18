<script setup lang="ts">
// 运行状态悬浮 pill(对齐 chat.html:897-937 + :2669-2677):fixed 底部居中,浮于 footer 上方
// (bottom 由 App 按 footer/errbar 高度现算,垂直堆叠不重叠),不占消息流;仅 sending/streaming 可见。
// sending 期「提交中…」(Stop 禁用——会话 id 未回填是死点击);streaming 起跑秒表「prefix · Ns」
// 并解锁 Stop;终态由 App 条件渲染收起。完成行「✓ 完成 · Ns · tok · $」在 footer 状态行(见 App)。
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { fmtTok } from "../lib/format";

const props = defineProps<{
  bottom: number;      // 距底偏移(px):footer 高 + 10 + (errbar 可见时)errbar 高,App 现算
  sending: boolean;
  streaming: boolean;
  prefix: string;      // 秒表前缀「会话 <sid> 启动|接续」(store.runPrefix)
  tok: number;         // 会话累计 token(与顶栏成本聚合同账)
  onStop: () => Promise<boolean>; // 受理 true / 失败 false——失败重新解锁 Stop(对齐原 catch 路径)
}>();

const visible = computed(() => props.sending || props.streaming);
const elapsed = ref(0);
const stopping = ref(false);
let timer: ReturnType<typeof setInterval> | null = null;
let tickStart = 0;

// 运行秒表:streaming 起跑(立即「prefix · 0s」,每秒 +1);终态(streaming 落)停表并复位 Stop 锁
watch(() => props.streaming, (s) => {
  if (timer !== null) { clearInterval(timer); timer = null; }
  if (s) {
    tickStart = Date.now();
    elapsed.value = 0;
    timer = setInterval(() => { elapsed.value = Math.floor((Date.now() - tickStart) / 1000); }, 1000);
  } else {
    stopping.value = false;
  }
}, { immediate: true });

onBeforeUnmount(() => { if (timer !== null) clearInterval(timer); });

const label = computed(() => (props.streaming ? `${props.prefix} · ${elapsed.value}s` : "提交中…"));
const tokText = computed(() => (props.tok > 0 ? fmtTok(props.tok) + " tok" : ""));
// rp-stop 仅在 streaming 后解锁;点击后锁到终态(stop 失败由 onStop=false 解锁)
const stopDisabled = computed(() => !props.streaming || stopping.value);

function clickStop() {
  if (stopDisabled.value) return;
  stopping.value = true;
  Promise.resolve(props.onStop())
    .then((ok) => { if (!ok) stopping.value = false; })
    .catch(() => { stopping.value = false; });
}
</script>

<template>
  <div v-if="visible" id="runpill" :style="{ bottom: bottom + 'px' }">
    <span class="spin"></span>
    <span id="rp-label">{{ label }}</span>
    <span id="rp-tok" class="rp-tok">{{ tokText }}</span>
    <button id="rp-stop" type="button" class="rp-stop" title="中断当前会话" :disabled="stopDisabled" @click="clickStop">Stop</button>
  </div>
</template>
