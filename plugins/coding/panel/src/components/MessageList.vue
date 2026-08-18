<script setup lang="ts">
// 消息流(#log):渲染 store 的 RenderItem 序列。行为对齐 chat.html:
// 用户贴底才自动跟随滚动(:939-947,距底 <60px 视为贴底);气泡内链接一律拦下(:948-952,
// sandbox 无导航出口,点开只会替换面板);pill 可见时底部让位(body.pill-on → #log.pill-on)。
// user 气泡的 ⏪ 回滚锚在 Task 7 接入(RenderItem.uuid 已备)。
import { nextTick, ref, watch } from "vue";
import type { RenderItem } from "../stores/session";
import AssistantBubble from "./AssistantBubble.vue";
import ToolCard from "./ToolCard.vue";
import FileEditCard from "./FileEditCard.vue";
import PermCard from "./PermCard.vue";
import MarkerLine from "./MarkerLine.vue";

const props = defineProps<{ items: RenderItem[]; padForPill?: boolean }>();

const logEl = ref<HTMLElement | null>(null);
const atBottom = ref(true); // 用户是否贴底(true 才自动跟随滚动)

function onScroll() {
  const el = logEl.value;
  if (!el) return;
  atBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
}

function scrollIfAtBottom() {
  const el = logEl.value;
  if (el && atBottom.value) el.scrollTop = el.scrollHeight;
}

// 流式 text_delta 是就地改末条气泡的 raw(数组长度不变),必须 deep 才能捕获;渲染后贴底跟随
watch(() => props.items, () => { void nextTick(scrollIfAtBottom); }, { deep: true });

// 链接拦截:一律 preventDefault(选中复制 URL 仍可用)
function onClick(e: MouseEvent) {
  const a = (e.target as HTMLElement | null)?.closest?.("a");
  if (a) e.preventDefault();
}
</script>

<template>
  <main id="log" ref="logEl" :class="{ 'pill-on': padForPill }" @scroll="onScroll" @click="onClick">
    <template v-for="(it, i) in items" :key="i">
      <div v-if="it.type === 'user'" class="row user">
        <div class="bubble">{{ it.text }}</div>
      </div>
      <AssistantBubble v-else-if="it.type === 'assistant'" :item="it" />
      <ToolCard v-else-if="it.type === 'tool'" :item="it" />
      <FileEditCard v-else-if="it.type === 'fileedit'" :item="it" />
      <PermCard v-else-if="it.type === 'perm'" :item="it" />
      <MarkerLine v-else-if="it.type === 'marker'" :text="it.text" :err="it.err" />
      <!-- error 项:store 已映射进 errbar(state.error),消息流不渲染 -->
    </template>
  </main>
</template>
