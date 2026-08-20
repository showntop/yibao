<script setup lang="ts">
// AI 气泡:markdown 渲染基座(marked → DOMPurify → hljs),行为对齐 chat.html:1001-1152。
// 流式期 150ms 节流重渲(createMdThrottler);raw>50KB 流式期纯文本、done 终渲解除护栏;
// think-block 计算属性分离(thinking 在顶部天然保序,免去 DOM 摘出/插回);管线异常 → 纯文本兜底。
import { computed, onBeforeUnmount, onMounted, onUpdated, ref, watch } from "vue";
import hljs from "highlight.js";
import type { RenderItem } from "../stores/session";
import { createMdThrottler, mdToHtml, sanitizeHtml } from "../lib/markdown";

type AssistantItem = Extract<RenderItem, { type: "assistant" }>;
const props = defineProps<{ item: AssistantItem }>();

const MD_PLAIN_LIMIT = 50000; // 长消息护栏:raw 超过则流式期纯文本(终渲解除)

// rAF 对齐(无 rAF 退 16ms timer,对齐 chat.html raf())
const throttler = createMdThrottler((fn) => {
  if (typeof window !== "undefined" && window.requestAnimationFrame) window.requestAnimationFrame(fn);
  else setTimeout(fn, 16);
}, 150);

// 实际进入渲染管线的 raw:流式期节流推进(初始化直取当前值——挂载前已到的 delta 不丢)
const renderedRaw = ref(props.item.raw);
watch(() => props.item.raw, () => {
  throttler.request(() => { renderedRaw.value = props.item.raw; });
});
// done 翻终渲:flush 掉排队中的流式帧,立即以最新 raw 全量渲(护栏由 html 计算属性按 done 解除)
watch(() => props.item.done, (d) => { if (d) throttler.flush(); });
onBeforeUnmount(() => throttler.flush()); // 卸载前把排队帧结掉,防 raw 滞留上一拍

// 思考小块:摘出单独渲染(非 markdown 内容),全部收进一个 <details> 折叠块——
// 流式期自动展开看直播,done 自动收(省屏);用户手动开合后不再干预(thinkToggled 优先)。
// 接管 summary 点击(preventDefault 只经绑定驱动):原生 toggle 事件在程序性开合时也异步触发,
// 用它记录用户偏好会把自动展开误存成「用户钉开」,done 后永不收
const thinks = computed(() => props.item.thinking.filter((t) => t));
const thinkToggled = ref<boolean | null>(null);
const thinkOpen = computed(() => thinkToggled.value ?? !props.item.done);
function onSummaryClick(e: Event) {
  e.preventDefault();
  thinkToggled.value = !thinkOpen.value;
}

// v-html 唯一入口:sanitizeHtml(mdToHtml(raw));任一环异常/缺库 → null → 纯文本兜底,绝不上抛
const html = computed((): string | null => {
  const raw = renderedRaw.value;
  if (!props.item.done && raw.length > MD_PLAIN_LIMIT) return null; // 流式长消息护栏
  const h = mdToHtml(raw);
  return h == null ? null : sanitizeHtml(h);
});
const isMd = computed(() => html.value != null);

// ---- 代码块增强(pre 外套 .codeblock:左语言标签 + 右复制钮;hljs 仅命中语言才染) ----
const contentEl = ref<HTMLElement | null>(null);

function copyText(text: string) {
  if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
    navigator.clipboard.writeText(text).catch(() => legacyCopy(text));
  } else {
    legacyCopy(text);
  }
}
function legacyCopy(text: string) {
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.cssText = "position:fixed;opacity:0";
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  } catch { /* 复制失败静默 */ }
}

function wrapCodeBlock(pre: HTMLPreElement) {
  if (pre.parentElement && pre.parentElement.classList.contains("codeblock")) return; // 防重复套壳
  const code = pre.querySelector("code");
  let lang = "";
  if (code) {
    const m = /(?:^|\s)language-([\w-]+)/.exec(code.className || "");
    if (m) lang = m[1];
  }
  const box = document.createElement("div");
  box.className = "codeblock";
  const head = document.createElement("div");
  head.className = "code-head";
  const langEl = document.createElement("span");
  langEl.className = "code-lang";
  langEl.textContent = lang || "code";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "code-copy";
  btn.textContent = "复制";
  btn.addEventListener("click", () => {
    // hljs 高亮只包 span,textContent 仍是代码原文
    copyText(code ? code.textContent || "" : pre.textContent || "");
    btn.textContent = "✓";
    btn.classList.add("ok");
    setTimeout(() => { btn.textContent = "复制"; btn.classList.remove("ok"); }, 1200);
  });
  head.appendChild(langEl);
  head.appendChild(btn);
  pre.parentNode!.insertBefore(box, pre);
  box.appendChild(head);
  box.appendChild(pre);
  if (lang && code && hljs.getLanguage(lang)) {
    try { hljs.highlightElement(code as HTMLElement); } catch { /* 高亮失败不影响原文 */ }
  }
}

function enhanceCodeBlocks() {
  const el = contentEl.value;
  if (!el) return;
  el.querySelectorAll("pre").forEach((pre) => wrapCodeBlock(pre));
}

onMounted(enhanceCodeBlocks);
onUpdated(enhanceCodeBlocks); // v-html 换内容后 pre 回到未套壳态,重跑(已套壳的自拒)
</script>

<template>
  <div class="row ai">
    <div class="bubble" :class="{ md: isMd, done: item.done }">
      <details v-if="thinks.length" class="think" :open="thinkOpen">
        <summary @click="onSummaryClick"><span class="chev">▸</span>思考</summary>
        <div v-for="(t, i) in thinks" :key="i" class="think-block">{{ t }}</div>
      </details>
      <div v-if="isMd" ref="contentEl" class="mdc" v-html="html"></div>
      <div v-else class="mdc">{{ renderedRaw }}</div>
    </div>
  </div>
</template>
