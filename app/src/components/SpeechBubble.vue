<script setup lang="ts">
// 说话态气泡：流式 chunk 由父组件拼到 text（天然打字机效果），streaming 时尾部闪光标。
// 短文本：最多 3 行折行静态展示；超 3 行 → 走马灯：单行不换行，内容持续左移滚到底
// （速度按全程 ≤12s 反推，长文也不会没头）。进走马灯发 busy（父组件暂停自动收起计时），
// 滚到底发 settled（父组件据此收尾）。仅展示；显隐/计时由父组件 App.vue 拥有。点整体 = 展开完整聊天窗。
import { computed, nextTick, onUnmounted, ref, watch } from "vue";

const props = defineProps<{ text: string; streaming?: boolean }>();
const emit = defineEmits<{
  (e: "expand"): void;
  (e: "busy"): void;
  (e: "settled"): void;
}>();

// 气泡是纯文本镜像：markdown 标记（** 加粗等）在这里只是噪音，显示前剥掉
const displayText = computed(() => props.text.replace(/\*\*/g, ""));

const marquee = ref(false); // 走马灯模式（超 3 行触发）
const offset = ref(0); // 左移量（px）：内容不断向左展现
const bodyRef = ref<HTMLElement | null>(null);
const txtRef = ref<HTMLElement | null>(null);
let raf: number | null = null;
let last = 0;
let holdUntil = 0; // 起跑前停顿，让人看清开头
let settledEmitted = false;
let busyEmitted = false;

function stopRaf() {
  if (raf !== null) {
    cancelAnimationFrame(raf);
    raf = null;
  }
}

function tick(now: number) {
  const body = bodyRef.value;
  const txt = txtRef.value;
  if (!body || !txt) {
    raf = null;
    return;
  }
  const max = Math.max(0, txt.scrollWidth - body.clientWidth);
  if (offset.value >= max) {
    raf = null;
    if (!settledEmitted) {
      settledEmitted = true;
      emit("settled");
    }
    return;
  }
  const dt = Math.min(0.05, (now - last) / 1000);
  last = now;
  if (now >= holdUntil) {
    // 速度按「全程 ≤12s」反推（60–400px/s）：短文慢滚，长文加速
    const speed = Math.min(400, Math.max(60, max / 12));
    offset.value = Math.min(max, offset.value + speed * dt);
  }
  raf = requestAnimationFrame(tick);
}

function startRaf() {
  if (raf !== null) return;
  last = performance.now();
  holdUntil = last + 600;
  raf = requestAnimationFrame(tick);
}

watch(
  () => props.text,
  (t, prev) => {
    void nextTick(() => {
      const body = bodyRef.value;
      const txt = txtRef.value;
      if (!body || !txt) return;
      // 新一轮回复（文本整体换掉，不是增量追加）：从头重新滚、重新上报 busy
      if (marquee.value && !t.startsWith(prev ?? "")) {
        offset.value = 0;
        settledEmitted = false;
        busyEmitted = false;
      }
      if (!marquee.value) {
        // 短文本静态折行；量出内容超 3 行（body 被 72px clamp）才切走马灯
        if (body.scrollHeight > body.clientHeight + 1) {
          marquee.value = true;
          settledEmitted = false;
        } else {
          return;
        }
      }
      // 走马灯（新进或流式追加变长）：追尾滚动；nextTick 等单行排版生效再量 scrollWidth
      if (!busyEmitted) {
        busyEmitted = true;
        emit("busy");
      }
      settledEmitted = false;
      void nextTick(startRaf);
    });
  },
  { immediate: true },
);

onUnmounted(stopRaf);
</script>

<template>
  <div class="sb" :class="{ marquee, streaming }" @click="$emit('expand')">
    <div class="who">译宝</div>
    <div class="body" ref="bodyRef">
      <span class="txt" ref="txtRef" :style="marquee ? { transform: `translateX(-${offset}px)` } : undefined">{{ displayText }}<span v-if="streaming" class="cur">▍</span></span>
    </div>
    <i class="tail" aria-hidden="true" />
  </div>
</template>

<style scoped>
.sb {
  position: relative;
  /* flex 子项自保：走马灯单行内容几千 px 宽，min-width:auto 会把气泡撑爆盖住团子 */
  min-width: 0;
  max-width: 100%;
  background: var(--yb-surface-solid);
  border: 1px solid var(--yb-surface-border);
  border-radius: var(--yb-radius-md);
  padding: 8px 11px;
  box-shadow: var(--yb-shadow);
  font-size: var(--yb-fs-md);
  line-height: 1.55;
  color: var(--yb-text);
  cursor: pointer;
  animation: rise 0.2s var(--yb-ease) both;
}
.who {
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-dim);
  font-weight: 600;
  margin-bottom: 2px;
}
/* 静态：最多 3 行；更长则切走马灯 */
.body {
  max-height: 72px;
  overflow: hidden;
  word-break: break-word;
  white-space: pre-wrap;
}
/* 走马灯：单行不换行，txt 由 JS translateX 持续左移 */
.sb.marquee .body {
  max-height: none;
  white-space: nowrap;
}
.sb.marquee .txt {
  display: inline-block;
  white-space: nowrap;
  will-change: transform;
}
/* 流式中右沿渐隐：示意「后面还有内容正赶来」（流完去掉，尾巴读得干净） */
.sb.marquee.streaming .body {
  mask-image: linear-gradient(90deg, #000 88%, transparent);
  -webkit-mask-image: linear-gradient(90deg, #000 88%, transparent);
}
.cur {
  display: inline-block;
  width: 0.55em;
  margin-left: 1px;
  color: var(--yb-accent);
  animation: blink 0.9s steps(2, start) infinite;
}
/* tail 指向右侧团子：定在气泡垂直中点（槽内居中后 = 团子脸的高度） */
.tail {
  position: absolute;
  right: -5px;
  top: 50%;
  margin-top: -5px;
  width: 10px;
  height: 10px;
  background: var(--yb-surface-solid);
  border-right: 1px solid var(--yb-surface-border);
  border-bottom: 1px solid var(--yb-surface-border);
  transform: rotate(-45deg);
}
@keyframes rise {
  from { opacity: 0; transform: translateY(5px); }
  to { opacity: 1; transform: none; }
}
@keyframes blink {
  0%, 50% { opacity: 1; }
  50.01%, 100% { opacity: 0; }
}
@media (prefers-reduced-motion: reduce) {
  .cur { animation: none; }
}
</style>
