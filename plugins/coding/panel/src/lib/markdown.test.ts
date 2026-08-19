import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { marked } from "marked";
import { createMdThrottler, mdToHtml, sanitizeHtml } from "./markdown";

describe("mdToHtml", () => {
  it("基本 gfm:标题 / 代码块(带语言类名) / 行内码", () => {
    const html = mdToHtml("# 标题\n\n```js\nconst a = 1;\n```\n\n这是 `code` 行内码");
    expect(html).not.toBeNull();
    expect(html).toContain("<h1>标题</h1>");
    expect(html).toContain('class="language-js"');
    expect(html).toContain("<code>code</code>");
  });

  it("breaks:true 单换行渲染为 <br>", () => {
    expect(mdToHtml("a\nb")).toContain("<br>");
  });

  it("原始 HTML 保留(lib 不消毒——消毒在组件 v-html 前)", () => {
    const html = mdToHtml("<b>hi</b>");
    expect(html).toContain("<b>hi</b>");
  });

  it("marked 抛错时返回 null(降级纯文本由组件负责,绝不上抛)", () => {
    const spy = vi.spyOn(marked, "parse").mockImplementation(() => {
      throw new Error("boom");
    });
    try {
      expect(mdToHtml("anything")).toBeNull();
    } finally {
      spy.mockRestore();
    }
  });
});

// node 测试环境无 DOM,dompurify 退化为无 sanitize 的工厂 → sanitizeHtml 返回 "";
// 完整消毒行为(FORBID_ATTR style 等)只在浏览器构建生效,node 下不测。
// 这里只锁一条环境无关的安全性质:任何环境下 script 都不会原样穿过 v-html 唯一入口。
describe("sanitizeHtml", () => {
  it("script 不会原样穿过(node 无 DOM → 空串;有 DOM → 消毒剥除)", () => {
    expect(sanitizeHtml('<p>ok</p><script>alert(1)</script>')).not.toContain("<script>");
  });
});

describe("createMdThrottler", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(0); // Date 一并假造,节流间隔计算才有确定性时间轴
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  // 测试用 setTimeout(16) 模拟 rAF;生产由组件注入 window.requestAnimationFrame
  const rafSchedule = (fn: () => void) => {
    setTimeout(fn, 16);
  };

  it("间隔内多次 request 合并只跑一次(且跑的是最新 render)", () => {
    const t = createMdThrottler(rafSchedule, 150);
    const r1 = vi.fn();
    const r2 = vi.fn();
    const r3 = vi.fn();
    t.request(r1);
    vi.advanceTimersByTime(16); // rAF 帧到 → 距上次渲染 0ms <150 → 排 trailing
    t.request(r2); // pending 中 → 合并,render 换成最新
    t.request(r3);
    vi.advanceTimersByTime(150); // trailing 到点
    expect(r1).not.toHaveBeenCalled();
    expect(r2).not.toHaveBeenCalled();
    expect(r3).toHaveBeenCalledTimes(1);
  });

  it("trailing 补跑:渲染后间隔内再 request,到点补跑一次不丢", () => {
    const t = createMdThrottler(rafSchedule, 150);
    const r = vi.fn();
    t.request(r);
    vi.advanceTimersByTime(16 + 150); // 第一次渲染完成(t=150)
    expect(r).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(50); // 距上次 50ms
    t.request(r);
    vi.advanceTimersByTime(16); // rAF 帧到 → wait=100 → 排 trailing,此帧不跑
    expect(r).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(100); // 到点补跑
    expect(r).toHaveBeenCalledTimes(2);
  });

  it("距上次渲染已够间隔 → rAF 帧到即跑,不等 trailing", () => {
    const t = createMdThrottler(rafSchedule, 150);
    const r = vi.fn();
    vi.advanceTimersByTime(500); // 静默 500ms,距"上次渲染"(last=0)已远超间隔
    t.request(r);
    vi.advanceTimersByTime(16); // rAF 帧到,wait<=0 → 当帧直接跑
    expect(r).toHaveBeenCalledTimes(1);
  });

  it("flush 立即跑,已排队的帧/timer 不重复跑;无 pending 时 flush 为 no-op", () => {
    const t = createMdThrottler(rafSchedule, 150);
    const r = vi.fn();
    t.flush(); // 无 pending → no-op,不抛
    t.request(r);
    t.flush();
    expect(r).toHaveBeenCalledTimes(1); // 同步立即跑
    vi.advanceTimersByTime(1000);
    expect(r).toHaveBeenCalledTimes(1); // 排队的 rAF/trailing 全部失效
  });
});
