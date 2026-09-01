<script setup lang="ts">
// 消息流(#log):渲染 store 的 RenderItem 序列。行为对齐 chat.html:
// 用户贴底才自动跟随滚动(:939-947,距底 <60px 视为贴底);气泡内链接一律拦下(:948-952,
// sandbox 无导航出口,点开只会替换面板)。
// T7:user 气泡带 uuid 挂 ⏪ 回滚锚(:957-987,防重禁用经 rewindPending;点击上抛 rewind,
// invoke 与状态行在 App);Codex→CC 交接卡(handoff 项)渲染 HandoffCard 并透传其事件。
import { nextTick, ref, watch } from "vue";
import type { RenderItem } from "../stores/session";
import AssistantBubble from "./AssistantBubble.vue";
import ToolCard from "./ToolCard.vue";
import FileEditCard from "./FileEditCard.vue";
import MarkerLine from "./MarkerLine.vue";
import HandoffCard from "./HandoffCard.vue";

const props = defineProps<{
  items: RenderItem[];
  streaming: boolean;                 // HandoffCard「用它开始」的运行中拦截
  rewindPending: ReadonlySet<string>; // 回滚请求在飞的 uuid 集(按钮防重禁用)
}>();
const emit = defineEmits<{
  rewind: [uuid: string];
  "handoff-cancel": [item: RenderItem];
  "handoff-start": [item: RenderItem, text: string];
  "open-changes": [];
  status: [text: string, err: boolean];
}>();

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

// 回底 pill(2026-09 交互重构):用户离底阅读时浮出,点击回底并恢复自动跟随
function scrollToBottom() {
  const el = logEl.value;
  if (!el) return;
  atBottom.value = true;
  el.scrollTop = el.scrollHeight;
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
  <div class="log-wrap">
    <main id="log" ref="logEl" @scroll="onScroll" @click="onClick">
      <template v-for="(it, i) in items" :key="it.type === 'handoff' ? 'h:' + it.seq : i">
        <div v-if="it.type === 'user'" class="row user">
          <div class="bubble">{{ it.text }}<button
            v-if="it.uuid"
            type="button"
            class="rewind-btn"
            title="回滚到这条消息时的文件状态"
            :disabled="rewindPending.has(it.uuid)"
            @click.stop="emit('rewind', it.uuid)"
          >⏪</button></div>
        </div>
        <AssistantBubble v-else-if="it.type === 'assistant'" :item="it" />
        <ToolCard v-else-if="it.type === 'tool'" :item="it" />
        <FileEditCard v-else-if="it.type === 'fileedit'" :item="it" />
        <MarkerLine v-else-if="it.type === 'marker'" :text="it.text" :err="it.err" />
        <!-- 轮级改动汇总 chip:点击开本会话改动清单浮层 -->
        <div v-else-if="it.type === 'editsummary'" class="row ai">
          <button type="button" class="edit-summary" title="查看本会话全部文件改动" @click="emit('open-changes')">
            <span class="ic">✎</span> 本轮改动 {{ it.count }} 个文件<span class="more"> · 点看全部</span>
          </button>
        </div>
        <HandoffCard
          v-else-if="it.type === 'handoff'"
          :item="it"
          :streaming="streaming"
          @cancel="emit('handoff-cancel', it)"
          @start="(text: string) => emit('handoff-start', it, text)"
          @status="(t: string, e: boolean) => emit('status', t, e)"
        />
        <!-- error 项:store 已映射进 errbar(state.error),消息流不渲染 -->
      </template>
    </main>
    <!-- 回底 pill:离底阅读时浮出,不挡内容(pointer-events 只在钮上) -->
    <button v-if="!atBottom" type="button" class="jump-bottom" title="滚动到最新消息" @click="scrollToBottom">↓ 回到底部</button>
  </div>
</template>
