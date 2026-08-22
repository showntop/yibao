<script setup lang="ts">
import { computed } from "vue";
import { renderMarkdownLite } from "../../lib/markdown";
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
  /** 无气泡排版：AI 主回复直接落在对话流里（轻量排版，结构化内容独立成卡） */
  plain?: boolean;
}>();
// 用户消息原样纯文本；AI 消息走 markdown-lite（转义在前，安全）；sys 是轻提示（插件展开等）
const html = computed(() => (props.role === "ai" && !props.typing ? renderMarkdownLite(props.text) : null));
</script>

<template>
  <div v-if="typing" class="bubble ai typing" aria-label="正在输入"><i /><i /><i /></div>
  <div
    v-else-if="html !== null"
    :class="['bubble', role, icon && `icon-${icon}`, plain && 'plain', halted && 'is-halted']"
    :role="icon === 'clock' ? 'status' : undefined"
  >
    <YbIcon v-if="icon" class="b-lead" :name="icon" :size="13" />
    <span v-html="html"></span><YbIcon v-if="halted && text !== '已打断'" class="b-tail" name="stop" :size="13" title="已中止" /><span v-if="streaming" class="cur">▍</span>
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
  /* fit-content：短文本按内容收缩不撑满容器（避免窄栏下"打开工具箱"被截成两行）；
     max-width 仍是长内容的上限。min-width:0 让 flex 子项真正遵守 max-width。 */
  width: fit-content;
  max-width: 88%;
  min-width: 0;
  font-size: var(--yb-fs-lg);
  line-height: var(--yb-lh-base);
  overflow-wrap: anywhere;
  word-break: break-word;
  animation: pop var(--yb-dur-fast) var(--yb-ease-out);
  /* 双击/选中触发 :focus 时浏览器会画 outline: auto（系统 accent 蓝 2-3px 实色），
   * 在 user 气泡上呈现"深色蓝矩形"。显式去掉；box-shadow 一并强制清空防残留。 */
  outline: none;
  box-shadow: none;
}
/* 文字选中：选中底色只作用于**文字 span**，不延伸到气泡 padding——
 * 否则双击全选时选区把气泡 padding 也包进去，在气泡底色上形成一截"淡蓝带"。
 * .bubble 的 padding 区域选区透明，仅内层 span（v-html 渲染的 + 文本 span）着淡蓝。 */
.bubble::selection {
  background: transparent;
}
.bubble :deep(span)::selection,
.bubble span::selection {
  background: rgba(var(--yb-c-sky-rgb), 0.22);
  color: var(--yb-text);
}
.bubble.user :deep(span)::selection,
.bubble.user span::selection {
  background: rgba(255, 255, 255, 0.4);
  color: var(--yb-text-on-accent);
}
/* 行首语义图标（提醒=accent / 告警=danger）与行尾中止图标 */
.b-lead {
  margin-right: var(--yb-space-1);
}
.icon-clock .b-lead {
  margin: 0;
  width: 26px;
  height: 26px;
  padding: 6px;
  box-sizing: border-box;
  border-radius: 50%;
  background: var(--yb-accent);
  color: var(--yb-text-on-accent);
  vertical-align: 0;
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
  /* 不加 box-shadow：0 2px 8px 模糊在白底上与 ::selection accent 蓝叠加会形成
   * 视觉上的"深蓝条"（双击全选时尤甚）。靠 1px 边 + 实色底出"卡"感。 */
  /* 尾巴角：靠左下的角收窄，拟小尾巴 */
  border-radius: var(--yb-radius-md) var(--yb-radius-md) var(--yb-radius-md) var(--yb-radius-xs);
}
/* 到期提醒：居中胶囊，不当成一条 AI 对话。长文必须换行，不能撑破对话栏。 */
.ai.icon-clock {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  align-self: center;
  box-sizing: border-box;
  width: auto;
  max-width: min(100%, 36em);
  min-width: 0;
  margin: 6px auto;
  padding: 8px 16px 8px 8px;
  border-radius: 18px;
  background: color-mix(in srgb, var(--yb-accent) 18%, var(--yb-surface-1));
  border: 1px solid color-mix(in srgb, var(--yb-accent) 42%, transparent);
  color: var(--yb-text-strong);
  font-size: var(--yb-fs-lg);
  font-weight: var(--yb-fw-medium);
  line-height: 1.4;
  white-space: normal;
  box-shadow: var(--yb-glaze-hi), 0 1px 3px rgba(var(--yb-c-sky-rgb), 0.16);
}
.ai.icon-clock .b-lead {
  flex: none;
  margin-top: 1px;
}
.ai.icon-clock > span {
  min-width: 0;
  flex: 1 1 auto;
  overflow-wrap: anywhere;
  word-break: break-word;
}
.ai.icon-clock :deep(div) {
  margin: 0;
}
/* 无气泡排版：AI 主回复直接落在流里（无底无边），仅保留行距与可读性 */
.plain.ai {
  background: transparent;
  border: none;
  border-radius: 0;
  padding: 0;
  max-width: min(100%, 760px);
}
.plain.ai :deep(.md-h) {
  margin-top: 10px;
}
.plain.ai.is-halted {
  color: var(--yb-text-faint);
  font-size: 13px;
}
.plain.ai.is-halted :deep(p) {
  margin: 0;
}
.user {
  /* 纯色：与 ai 气泡统一无渐变（此前 135deg accent→deep 对角渐变会让
   * "用户发的第一条消息"看起来有渐变，浅色底上突兀） */
  background: var(--yb-accent);
  color: var(--yb-text-on-accent);
  align-self: flex-end;
  /* 不加 box-shadow：0.3 sky 蓝 8px blur 在白底上向四周扩散，气泡上方会
   * 出现淡蓝晕，看起来像"上半部分叠了一层"。靠实色 + 1px 边出"卡"感。 */
  border: 1px solid var(--yb-accent);
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
  /* 空行占位 div：双击/全选时选区覆盖它会让 ::selection 显示成 accent 蓝条。
   * user-select: none 让它不可选中，选区直接跳过（CSS 层兜底，旧消息也生效）。 */
  user-select: none;
}
.ai :deep(.md-hr) {
  border-top: 1px solid var(--yb-line);
  margin: 6px 0;
}
/* 可勾选清单：AI 给可操作项时的清单卡（checkbox 由用户勾选） */
.ai :deep(.md-task) {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  margin: 4px 0;
  cursor: pointer;
}
.ai :deep(.md-task input) {
  margin: 4px 0 0;
  width: 13px;
  height: 13px;
  flex: none;
  accent-color: var(--yb-accent);
  cursor: pointer;
}
.ai :deep(.md-task span) {
  min-width: 0;
  flex: 1;
}
.ai :deep(.md-task input:checked + span) {
  color: var(--yb-text-dim);
  text-decoration: line-through;
  text-decoration-color: rgba(var(--yb-c-sky-rgb), 0.4);
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
