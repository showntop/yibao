import { describe, expect, it } from "vitest";
import { docWordsOf } from "./doc-status.ts";

/** 字数口径：CJK 每字计 1，拉丁/数字连续串计 1；markdown 语法符号不计。 */
describe("docWordsOf", () => {
  it("counts CJK chars individually", () => {
    expect(docWordsOf("你好世界")).toBe(4);
  });

  it("counts latin words as one each, mixed with CJK", () => {
    expect(docWordsOf("# 标题\n\nHello world 世界")).toBe(6); // 标题(2)+hello+world+世界(2)
  });

  it("ignores markdown syntax marks", () => {
    // 一(1)+模型与评测(5)+重点(2)+内容(2) = 10；顿号/井号/星号不计
    expect(docWordsOf("## 一、模型与评测\n\n**重点**内容")).toBe(10);
  });

  it("strips code fences and link urls, keeps link text", () => {
    const doc = "看[文档](https://example.com/a)和\n```\ncode_ignore\n```\n完";
    expect(docWordsOf(doc)).toBe(5); // 看+文档(2)+和+完
  });

  it("empty is zero", () => {
    expect(docWordsOf("")).toBe(0);
    expect(docWordsOf("# \n---\n")).toBe(0);
  });
});
