import { afterEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import MessageBody from "./MessageBody.vue";

// jsdom 无剪贴板：注入 fake（点击复制路径要调 navigator.clipboard.writeText）
function fakeClipboard(): ReturnType<typeof vi.fn> {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  return writeText;
}

afterEach(() => vi.useRealTimers());

describe("MessageBody", () => {
  it("渲染 Markdown：标题 h1 + 代码块 pre/code + 复制钮", () => {
    const w = mount(MessageBody, { props: { text: "# 标题\n\n```js\ncode()\n```" } });
    expect(w.element.querySelector("h1")?.textContent).toBe("标题");
    const code = w.element.querySelector("pre code");
    expect(code?.textContent).toBe("code()");
    expect(code?.className).toContain("language-js");
    expect(w.element.querySelector("pre .copy-btn")).toBeTruthy();
  });

  it("XSS 清毒：onerror 属性被剥，img 本身保留", () => {
    const w = mount(MessageBody, { props: { text: '<img src=x onerror=alert(1)>' } });
    expect(w.html()).not.toContain("onerror");
    expect(w.element.querySelector("img")).toBeTruthy();
  });

  it("行内纯文本不受影响：换行原样、不产生多余标签", () => {
    const w = mount(MessageBody, { props: { text: "你好，译宝" } });
    expect(w.element.textContent).toContain("你好，译宝");
    expect(w.element.querySelector("h1")).toBeNull();
    expect(w.element.querySelector("pre")).toBeNull();
  });

  it("复制钮：点击写剪贴板并短变「已复制」，随后复原", async () => {
    const writeText = fakeClipboard();
    vi.useFakeTimers();
    const w = mount(MessageBody, { props: { text: "```js\nx=1\n```" } });
    const btn = w.element.querySelector(".copy-btn") as HTMLElement;
    btn.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await vi.advanceTimersByTimeAsync(0); // 等剪贴板 promise 微任务落地
    expect(writeText).toHaveBeenCalledWith("x=1");
    expect(btn.textContent).toBe("已复制");
    await vi.advanceTimersByTimeAsync(1600);
    expect(btn.textContent).toBe("复制");
  });

  it("复制失败：剪贴板拒绝时按钮短示「复制失败」，随后复原（不误示已复制）", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("denied"));
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    vi.useFakeTimers();
    const w = mount(MessageBody, { props: { text: "```js\nx=1\n```" } });
    const btn = w.element.querySelector(".copy-btn") as HTMLElement;
    btn.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await vi.advanceTimersByTimeAsync(0);
    expect(writeText).toHaveBeenCalledWith("x=1");
    expect(btn.textContent).toBe("复制失败");
    await vi.advanceTimersByTimeAsync(1600);
    expect(btn.textContent).toBe("复制");
  });

  it("fence 语言标识白名单转义：非法字符不进 class（防属性值夹带）", () => {
    const w = mount(MessageBody, { props: { text: '```js onload=alert(1)\nx=1\n```' } });
    const code = w.element.querySelector("pre code") as HTMLElement;
    expect(code.className).toBe("language-js"); // 只留字母数字连字符下划线
    expect(code.className).toMatch(/^[\w-]*$/);
  });
});
