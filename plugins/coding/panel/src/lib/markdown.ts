// markdown 渲染纯函数(移植 chat.html:1001-1152 的管线语义,去掉 DOM 部分)。
// mdToHtml 只做 marked(gfm+breaks) 解析,不消毒——消毒是组件 v-html 前的 sanitizeHtml。
// 流式 150ms 节流:createMdThrottler,rAF 由调用方注入(组件注入 rAF,测试注入 setTimeout)。
// 长消息 50KB 护栏与终渲时机留在组件层,不在本库。
import { marked } from "marked";
import DOMPurify from "dompurify";

// marked 解析:异常/返回非字符串 → null(组件降级纯文本,绝不上抛)
export function mdToHtml(raw: string): string | null {
  try {
    const html = marked.parse(raw, { gfm: true, breaks: true });
    return typeof html === "string" ? html : null;
  } catch {
    return null;
  }
}

type Sanitizer = { sanitize(html: string, cfg?: Record<string, unknown>): string };

// dompurify 默认导出在浏览器(有 window)下是可用实例;node/无 DOM 环境下是工厂
// (isSupported=false,无 sanitize)。懒实例化:优先直用实例,工厂形态且有 window 才构造。
let cachedSanitizer: Sanitizer | null | undefined;

function getSanitizer(): Sanitizer | null {
  if (cachedSanitizer !== undefined) return cachedSanitizer;
  try {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const dp: any = DOMPurify;
    let inst: any = null;
    if (dp && typeof dp.sanitize === "function") inst = dp;
    else if (typeof dp === "function" && typeof window !== "undefined") inst = dp(window);
    cachedSanitizer = inst && typeof inst.sanitize === "function" ? (inst as Sanitizer) : null;
  } catch {
    cachedSanitizer = null;
  }
  return cachedSanitizer;
}

// 组件 v-html 唯一入口:FORBID_ATTR style 收掉模型输出原始 HTML 的 style 属性面。
// 拿不到 DOMPurify 实例(node 测试环境等) → 返回 "":宁可空白也不放未消毒 HTML 进 v-html。
export function sanitizeHtml(html: string): string {
  const s = getSanitizer();
  if (!s) return "";
  try {
    return s.sanitize(html, { FORBID_ATTR: ["style"] });
  } catch {
    return "";
  }
}

export interface MdThrottler {
  request(render: () => void): void;
  flush(): void;
}

// 流式重渲染节流(移植 scheduleBubbleRender/flushBubbleRender 语义,原 chat.html:1033-1050):
// pending 位合并——间隔内多次 request 只跑一次(跑最新传入的 render,旧的自然丢弃);
// 调度帧到后距上次渲染 <minInterval 则 setTimeout 补满间隔(trailing,兜底最后一块);
// flush 立即跑(终渲用),已排队的帧与 timer 失效。
export function createMdThrottler(
  schedule: (fn: () => void) => void,
  minInterval = 150,
): MdThrottler {
  let pending = false;
  let last = 0;
  let renderFn: (() => void) | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;

  function run(): void {
    if (!pending) return; // 期间已被 flush 接管
    pending = false;
    last = Date.now();
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
    const r = renderFn;
    renderFn = null;
    r?.();
  }

  return {
    request(render) {
      renderFn = render; // 合并:只留最新
      if (pending) return;
      pending = true;
      schedule(() => {
        if (!pending) return; // 帧到前已被 flush 接管
        const wait = minInterval - (Date.now() - last);
        if (wait <= 0) run();
        else timer = setTimeout(run, wait);
      });
    },
    flush() {
      run();
    },
  };
}
