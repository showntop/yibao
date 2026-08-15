<script setup lang="ts">
import { computed } from "vue";
import { Marked, type Tokens } from "marked";
import DOMPurify from "dompurify";

const props = defineProps<{ text: string }>();

// 代码块渲染：marked 默认 pre/code 外再注入「复制」钮（事件委托，见 onClick）。
// token.text 为原始码文（escaped=false 时）——自行转义防把代码当 HTML 注入。
const escapeHtml = (s: string) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
// 局部实例：marked.use() 会污染全局单例（同包其他 marked 使用者一并被改渲染器），
// new Marked() 的 options 只作用于本实例，未覆写的方法仍走默认渲染
const md = new Marked({
  renderer: {
    code({ text, lang, escaped }: Tokens.Code): string {
      // fence 信息串做字符白名单（字母数字_-）：防语言标识里夹带的字符进 class 属性值
      const l = ((lang || "").trim().split(/\s+/)[0] ?? "").replace(/[^\w-]/g, "");
      const body = escaped ? text : escapeHtml(text);
      return `<pre><button class="copy-btn" type="button">复制</button><code${l ? ` class="language-${l}"` : ""}>${body}</code></pre>`;
    },
  },
});

// marked 解析 + DOMPurify 清毒（USE_PROFILES html：剥事件属性/脚本，保常规标签）
const html = computed(() =>
  DOMPurify.sanitize(md.parse(props.text, { async: false }), {
    USE_PROFILES: { html: true },
  }),
);

// 非安全上下文（http 内网，Task 5 上 HTTPS 前）无 navigator.clipboard：退化 execCommand
function copyText(t: string): Promise<void> {
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(t);
  return new Promise((resolve) => {
    const ta = document.createElement("textarea");
    ta.value = t;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand("copy");
    } catch {
      /* 复制失败只能放弃（提示复原即可，不误报「已复制」之外的状态） */
    }
    ta.remove();
    resolve();
  });
}

// 事件委托：一个监听接管整条消息的所有复制钮（消息可长，不逐块挂监听）
function onClick(e: MouseEvent): void {
  const btn = (e.target as HTMLElement)?.closest?.(".copy-btn");
  if (!(btn instanceof HTMLElement)) return;
  const code = btn.closest("pre")?.querySelector("code")?.textContent ?? "";
  void copyText(code)
    .then(() => {
      btn.textContent = "已复制";
    })
    .catch(() => {
      btn.textContent = "复制失败"; // 剪贴板被拒（权限/非手势上下文）：如实提示，不误示已复制
    })
    .finally(() => {
      window.setTimeout(() => (btn.textContent = "复制"), 1500); // 短暂确认后复原
    });
}
</script>

<template>
  <!-- 内容已过 DOMPurify 清毒方可 v-html；点击委托到容器处理代码块复制 -->
  <div class="md" v-html="html" @click="onClick"></div>
</template>

<style scoped>
/* 覆盖气泡的 pre-wrap 继承：markdown 自管空白（代码块/段落） */
.md { white-space: normal; font-size: 15px; line-height: 1.55; }
.md :deep(p) { margin: 0 0 0.5em; }
.md :deep(p:last-child) { margin-bottom: 0; }
.md :deep(h1), .md :deep(h2), .md :deep(h3), .md :deep(h4) { margin: 0.6em 0 0.35em; line-height: 1.3; }
.md :deep(h1) { font-size: 1.25em; }
.md :deep(h2) { font-size: 1.15em; }
.md :deep(h3), .md :deep(h4) { font-size: 1.05em; }
.md :deep(ul), .md :deep(ol) { margin: 0.25em 0; padding-left: 1.4em; }
.md :deep(li) { margin: 0.15em 0; }
.md :deep(a) { color: #2f6fed; }
.md :deep(blockquote) { margin: 0.4em 0; padding: 0.1em 0.8em; border-left: 3px solid rgba(128, 128, 128, 0.4); opacity: 0.9; }
.md :deep(code) { font-family: ui-monospace, Menlo, monospace; font-size: 0.9em; }
.md :deep(:not(pre) > code) { background: rgba(128, 128, 128, 0.18); border-radius: 4px; padding: 0.1em 0.35em; }
.md :deep(pre) { position: relative; margin: 0.45em 0; padding: 10px 12px; border-radius: 8px;
  background: rgba(0, 0, 0, 0.75); overflow-x: auto; }
.md :deep(pre code) { background: none; padding: 0; color: #f2f2f2; }
.md :deep(.copy-btn) { position: absolute; top: 6px; right: 6px; padding: 2px 8px; border: none;
  border-radius: 6px; font-size: 11px; line-height: 1.5; background: rgba(255, 255, 255, 0.14); color: #fff; }
.md :deep(.copy-btn:active) { background: rgba(255, 255, 255, 0.28); }
.md :deep(table) { border-collapse: collapse; margin: 0.4em 0; }
.md :deep(th), .md :deep(td) { border: 1px solid rgba(128, 128, 128, 0.35); padding: 4px 8px; }
.md :deep(hr) { border: none; border-top: 1px solid rgba(128, 128, 128, 0.35); margin: 0.6em 0; }
.md :deep(img) { max-width: 100%; }
</style>
