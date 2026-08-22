import { describe, it, expect } from "vitest";
import {
  parseAtTrigger,
  stripAtTrigger,
  formatContextPrefix,
  type InputContext,
} from "./at-mention";

describe("parseAtTrigger", () => {
  it("光标前无 @ → null", () => {
    expect(parseAtTrigger("hello", 5)).toBeNull();
    expect(parseAtTrigger("a b c", 2)).toBeNull();
  });
  it("行首裸 @ → 空 query", () => {
    expect(parseAtTrigger("@", 1)).toEqual({ start: 0, query: "" });
  });
  it("@ 后带 query", () => {
    expect(parseAtTrigger("看一下 @src/mai", 12)).toEqual({ start: 4, query: "src/mai" });
  });
  it("光标在文本中间：只解析光标前", () => {
    expect(parseAtTrigger("@abc def", 4)).toEqual({ start: 0, query: "abc" });
    expect(parseAtTrigger("@abc def", 8)).toBeNull(); // 光标在词后（中间有空格阻断）
  });
  it("query 允许 . - _ /（路径字符）", () => {
    expect(parseAtTrigger("@a-b_c.d/e", 10)).toEqual({ start: 0, query: "a-b_c.d/e" });
  });
  it("@ 后空格 → 不触发（与 coding 面板同规则）", () => {
    expect(parseAtTrigger("email @ 我", 5)).toBeNull();
  });
  it("caret 越界钳制", () => {
    expect(parseAtTrigger("@ab", 99)).toEqual({ start: 0, query: "ab" });
  });
});

describe("stripAtTrigger", () => {
  it("移除 @query 片段保留其余文本", () => {
    expect(stripAtTrigger("看下 @src/mai 谢谢", 11, 3)).toBe("看下  谢谢");
  });
  it("光标在片段末尾之后：按 start+caret 切", () => {
    expect(stripAtTrigger("@ab", 3, 0)).toBe("");
  });
});

describe("formatContextPrefix", () => {
  it("空 → 空串", () => {
    expect(formatContextPrefix([])).toBe("");
  });
  it("attachment/reference 沿用既有格式", () => {
    const ctx: InputContext[] = [
      { kind: "attachment", label: "a.png" },
      { kind: "reference", label: "当前会话" },
    ];
    expect(formatContextPrefix(ctx)).toBe("【附件：a.png】\n【引用：当前会话】\n\n");
  });
  it("reference 带 refId（最近会话引用）格式不变", () => {
    expect(formatContextPrefix([{ kind: "reference", label: "帮我改首页", refId: "conv-1" }]))
      .toBe("【引用：帮我改首页】\n\n");
  });
  it("attachment 有 path 落路径（粘贴截图落盘后 AI 可按路径读图）", () => {
    expect(formatContextPrefix([{ kind: "attachment", label: "截图 x.png", path: "/tmp/att-1.png" }]))
      .toBe("【附件：/tmp/att-1.png】\n\n");
  });
  it("file 落全路径（path 优先于 label）", () => {
    expect(formatContextPrefix([{ kind: "file", label: "main.rs", path: "src/main.rs" }]))
      .toBe("【文件：src/main.rs】\n\n");
  });
});
