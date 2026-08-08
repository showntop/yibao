<script setup lang="ts">
import { computed } from "vue";
import { renderMarkdownLite } from "../lib/markdown";
import YbIcon from "./YbIcon.vue";

// typing = 「正在输入」占位（三点呼吸，无文本）；streaming = 流式进行中（尾部闪光标）
// pstate：过程行状态（图标随态：run 转圈 / ok / fail）；halted：被打断（行尾中止图标）；
// icon：行首语义图标（clock=提醒 / alert=告警）——文案保持纯净，图标由状态渲染
const props = defineProps<{
  role: "user" | "ai" | "sys";
  text: string;
  typing?: boolean;
  streaming?: boolean;
  pstate?: "run" | "ok" | "fail";
  halted?: boolean;
  icon?: "clock" | "alert" | "doc";
}>();
// 用户消息原样纯文本；AI 消息走 markdown-lite（转义在前，安全）；sys 是轻提示（插件展开等）
const html = computed(() => (props.role === "ai" && !props.typing ? renderMarkdownLite(props.text) : null));
</script>

<template>
  <div v-if="typing" class="bubble ai typing" aria-label="正在输入"><i /><i /><i /></div>
  <div v-else-if="html !== null" :class="['bubble', role, icon && `icon-${icon}`]">
    <YbIcon v-if="icon" class="b-lead" :name="icon" :size="13" />
    <span v-html="html"></span><YbIcon v-if="halted" class="b-tail" name="stop" :size="13" title="已中止" /><span v-if="streaming" class="cur">▍</span>
  </div>
  <div v-else :class="['bubble', role, pstate && `is-${pstate}`, icon && `icon-${icon}`]">
    <YbIcon
      v-if="pstate"
      class="b-ic"
      :name="pstate === 'run' ? 'spinner' : pstate === 'ok' ? 'check' : 'x'"
      :spin="pstate === 'run'"
      :size="12"
    />
    <YbIcon v-else-if="icon" class="b-lead" :name="icon" :size="12" />
    <span>{{ text }}</span>
  </div>
</template>

<style scoped>
.bubble {
  padding: var(--yb-space-2) var(--yb-space-3);
  border-radius: var(--yb-radius-md);
  max-width: 88%;
  font-size: var(--yb-fs-lg);
  line-height: var(--yb-lh-base);
  word-break: break-word;
  animation: pop var(--yb-dur-fast) var(--yb-ease-out);
}
/* 文字选中：浏览器默认蓝选区在两种气泡底色上对比度差且视觉杂
 * （ai 浅白 + 选区蓝 = 糊；user accent 蓝 + 选区蓝 = 无区分）。
 * ai 用 accent 实色 + 白字；user 蓝底用半透明白翻转（与气泡对比强）。 */
.bubble::selection {
  background: var(--yb-accent);
  color: #fff;
}
.bubble.user::selection {
  background: rgba(255, 255, 255, 0.4);
  color: var(--yb-text-on-accent);
}
/* 行首语义图标（提醒=accent / 告警=danger）与行尾中止图标 */
.b-lead {
  margin-right: var(--yb-space-1);
}
.icon-clock .b-lead {
  color: var(--yb-accent);
}
.icon-alert .b-lead {
  color: var(--yb-danger);
}
.b-tail {
  margin-left: var(--yb-space-1);
  color: var(--yb-text-dim);
}
.b-ic {
  margin-right: var(--yb-space-1);
}
.is-run .b-ic {
  color: var(--yb-accent);
}
.is-ok .b-ic {
  color: var(--yb-intent-ok);
}
.is-fail .b-ic {
  color: var(--yb-danger);
}
.ai {
  background: var(--yb-bubble-ai);
  border: 1px solid var(--yb-surface-border);
  color: var(--yb-text);
  align-self: flex-start;
  box-shadow:
    0 1px 2px rgba(var(--yb-c-slate-rgb), 0.05),
    0 2px 8px rgba(var(--yb-c-slate-rgb), 0.08);
  /* 尾巴角：靠左下的角收窄，拟小尾巴 */
  border-radius: var(--yb-radius-md) var(--yb-radius-md) var(--yb-radius-md) var(--yb-radius-xs);
}
.user {
  /* 纯色：与 ai 气泡统一无渐变（此前 135deg accent→deep 对角渐变会让
   * "用户发的第一条消息"看起来有渐变，浅色底上突兀） */
  background: var(--yb-accent);
  color: var(--yb-text-on-accent);
  align-self: flex-end;
  box-shadow: 0 2px 8px rgba(77, 144, 196, 0.3);
  /* 尾巴角：靠右下的角收窄 */
  border-radius: var(--yb-radius-md) var(--yb-radius-md) var(--yb-radius-xs) var(--yb-radius-md);
}
/* 轻提示（插件展开等 notice）：居中淡色小字，不拟气泡、不打断阅读 */
.sys {
  background: transparent;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-sm);
  align-self: center;
  padding: 0 var(--yb-space-3);
  box-shadow: none;
}
/* 「正在输入」占位：三点呼吸，accent 色 */
.typing {
  display: inline-flex;
  align-items: center;
  gap: var(--yb-space-1);
  padding: var(--yb-space-3);
}
.typing i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--yb-accent);
  animation: typing-breathe 1.2s ease-in-out infinite;
}
.typing i:nth-child(2) {
  animation-delay: 0.15s;
}
.typing i:nth-child(3) {
  animation-delay: 0.3s;
}
/* 流式光标：与 SpeechBubble 一致的闪烁 ▍ */
.cur {
  display: inline-block;
  width: 0.55em;
  margin-left: 1px;
  color: var(--yb-accent);
  animation: blink 0.9s steps(2, start) infinite;
}
/* markdown-lite 块样式（v-html 内容，需 :deep） */
.ai :deep(.md-h) {
  font-weight: var(--yb-fw-bold);
  margin: 2px 0;
}
.ai :deep(.md-li) {
  padding-left: 2px;
}
.ai :deep(.md-kv) {
  padding-left: 2px;
}
.ai :deep(.md-kv-h) {
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-md);
}
.ai :deep(.md-gap) {
  height: 6px;
}
.ai :deep(.md-hr) {
  border-top: 1px solid var(--yb-line);
  margin: 6px 0;
}
.ai :deep(code) {
  font-family: var(--yb-mono);
  font-size: 0.92em;
  background: var(--yb-code-inline-bg);
  border-radius: var(--yb-radius-xs);
  padding: 0 var(--yb-space-1);
}
/* 围栏代码块：等宽 + 浅底 + 横向滚动；块内 code 去掉行内底色 */
.ai :deep(pre) {
  margin: 4px 0;
  padding: 8px 10px;
  background: var(--yb-code-bg);
  border-radius: var(--yb-radius-sm);
  overflow-x: auto;
}
.ai :deep(pre code) {
  background: transparent;
  padding: 0;
  border-radius: 0;
}
@keyframes pop {
  from {
    opacity: 0;
    transform: translateY(4px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}
@keyframes typing-breathe {
  0%,
  100% {
    opacity: 0.3;
    transform: translateY(0);
  }
  50% {
    opacity: 1;
    transform: translateY(-2px);
  }
}
@keyframes blink {
  0%,
  50% {
    opacity: 1;
  }
  50.01%,
  100% {
    opacity: 0;
  }
}
@media (prefers-reduced-motion: reduce) {
  .cur,
  .typing i {
    animation: none;
  }
}
</style>
