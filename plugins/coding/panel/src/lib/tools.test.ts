import { describe, expect, it } from "vitest";
import { resultTally, summarizeTool, toolIcon } from "./tools";

describe("toolIcon", () => {
  it("已知工具取映射,未知工具默认 ⚙", () => {
    expect(toolIcon("Bash")).toBe("❯");
    expect(toolIcon("Edit")).toBe("✎");
    expect(toolIcon("Whatever")).toBe("⚙");
    expect(toolIcon("")).toBe("⚙");
  });
});

describe("summarizeTool", () => {
  it("按工具取代表字段", () => {
    expect(summarizeTool("Bash", { command: "ls -la" })).toBe("ls -la");
    expect(summarizeTool("Read", { file_path: "/a/b.ts" })).toBe("/a/b.ts");
    expect(summarizeTool("Glob", { pattern: "*.ts" })).toBe("*.ts");
    expect(summarizeTool("Grep", { pattern: "foo" })).toBe('"foo"');
    expect(summarizeTool("Edit", { file_path: "/x" })).toBe("/x");
    expect(summarizeTool("WebFetch", { url: "https://a.b" })).toBe("https://a.b");
    expect(summarizeTool("WebSearch", { query: "q" })).toBe("q");
    expect(summarizeTool("Task", { description: "找入口" })).toBe("找入口");
  });
  it("未列出工具取首个字符串参数", () => {
    expect(summarizeTool("McpTool", { n: 1, note: "备注" })).toBe("备注");
  });
  it("换行压平 + 超 60 字截断(59 字 + …)", () => {
    expect(summarizeTool("Bash", { command: "a\nb\tc" })).toBe("a b c");
    const long = "x".repeat(80);
    const out = summarizeTool("Bash", { command: long });
    expect(out).toBe("x".repeat(59) + "…");
  });
  it("拿不到意图 → 空串", () => {
    expect(summarizeTool("Bash", {})).toBe("");
    expect(summarizeTool("Bash", null)).toBe("");
    expect(summarizeTool("Bash", undefined)).toBe("");
  });
});

describe("resultTally", () => {
  it("Grep → 非空行计 matches", () => {
    expect(resultTally("Grep", "a\n\n  \nb\n")).toBe("2 matches");
    expect(resultTally("Grep", "\n\n")).toBe(""); // 全空行 → 不计
  });
  it("Read/Bash → 行数(末尾空行不算)", () => {
    expect(resultTally("Read", "1\n2\n3\n")).toBe("3 lines");
    expect(resultTally("Bash", "only")).toBe("1 lines");
  });
  it("其它工具 / 空文本 → 空串", () => {
    expect(resultTally("Edit", "x\ny")).toBe("");
    expect(resultTally("Read", "")).toBe("");
  });
});
