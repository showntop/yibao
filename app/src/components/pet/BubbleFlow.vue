<script setup lang="ts">
// 气泡流视图（宠物窗主体）：空态欢迎（Avatar + 建议 chips）/ 气泡列表（流式/回顾入口）/ 过程展示。
// 纯展示 + 事件上抛（submit / recap-click / surface-open）；滚动容器元素经 expose 暴露，
// 父级桥接给 usePetBubbles 的 bubblesRef（scrollBubbles/restoreBubbleScroll 在父级 composable 里操作它）。
import { ref } from "vue";
import Avatar from "../Avatar.vue";
import Bubble from "../Bubble.vue";
import SurfaceLine from "../SurfaceLine.vue";
import type { BubbleMsg } from "../../composables/usePetBubbles";
import type { PetAvatarState } from "../../composables/usePetState";

defineProps<{
  bubbles: BubbleMsg[];
  streamingIdx: number | null;
  showTyping: boolean;
  petState: PetAvatarState;
  suggestions: string[];
}>();

const emit = defineEmits<{
  submit: [text: string];
  "recap-click": [day?: string];
  "surface-open": [];
}>();

/** 滚动容器（expose 给父级桥接 usePetBubbles.bubblesRef）。 */
const el = ref<HTMLElement | null>(null);
defineExpose({ el });
</script>

<template>
  <div class="bubbles" ref="el">
    <div v-if="!bubbles.length && !showTyping" class="empty-hint">
      <Avatar :state="petState" :size="56" />
      <p>说一件事</p>
      <div class="chips">
        <button v-for="c in suggestions" :key="c" class="chip" @click="emit('submit', c)">{{ c }}</button>
      </div>
    </div>
    <template v-for="(b, i) in bubbles" :key="i">
      <SurfaceLine v-if="b.surface" :attr="b.surface" @open="emit('surface-open')" />
      <Bubble
        v-else
        :role="b.role"
        :text="b.text"
        :streaming="i === streamingIdx"
        :pstate="b.pstate"
        :halted="b.halted"
        :icon="b.icon"
        :class="{ 'recap-clickable': !!b.recap }"
        @click="emit('recap-click', b.recap)"
      />
    </template>
    <Bubble v-if="showTyping" role="ai" text="" typing />
  </div>
</template>

<style scoped>
.bubbles {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: var(--yb-space-2);
  overflow-y: auto;
  padding: 4px 2px 0;
  scrollbar-width: thin;
  /* 顶部渐隐：滚出视口的消息柔和淡出。原先 mask 在 macOS WKWebView luminance 模式
   * 下可能误渲染为深色伪影（与 ::selection 叠加形成"深蓝条"），先关掉。 */
  /* mask-image: linear-gradient(180deg, transparent, #000 14px);
  -webkit-mask-image: linear-gradient(180deg, transparent, #000 14px); */
}
.bubbles :deep(.bubble.ai) {
  background: var(--yb-bubble-ai);
  border-color: rgba(var(--yb-c-slate-rgb), 0.15);
  box-shadow: none;
}
/* morning_recap 气泡可点击 deep-link 到回顾（class 经 fallthrough 落到 Bubble 根 div） */
.recap-clickable {
  cursor: pointer;
  transition: filter var(--yb-dur-fast) var(--yb-ease-out);
}
.recap-clickable:hover {
  filter: brightness(0.96);
}
/* 空状态：气泡区占位引导（小号团子 + 一句招呼 + 建议 chip） */
.empty-hint {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-lg);
}
.empty-hint p {
  margin: 0 0 2px;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: var(--yb-space-2);
}
.chip {
  padding: 5px 12px;
  border: 1px solid var(--yb-surface-border);
  border-radius: var(--yb-radius-pill);
  background: var(--yb-surface-solid);
  color: var(--yb-accent-deep);
  font-size: var(--yb-fs-lg);
  cursor: pointer;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.chip:hover {
  background: var(--yb-accent-soft);
  border-color: var(--yb-accent);
  color: var(--yb-accent-deep);
}
</style>
