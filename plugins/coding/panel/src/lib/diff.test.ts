import { describe, expect, it } from "vitest";
import { diffStats, lcsLines, multiEditDiff } from "./diff";

describe("lcsLines", () => {
  it("基本:同首行 ctx,不同行 del→add(顺序按 LCS)", () => {
    expect(lcsLines("a\nb", "a\nc")).toEqual([
      { type: "ctx", text: "a" },
      { type: "del", text: "b" },
      { type: "add", text: "c" },
    ]);
  });

  it("空 old → 全 add", () => {
    expect(lcsLines("", "a\nb")).toEqual([
      { type: "add", text: "a" },
      { type: "add", text: "b" },
    ]);
  });

  it("空 new → 全 del", () => {
    expect(lcsLines("a\nb", "")).toEqual([
      { type: "del", text: "a" },
      { type: "del", text: "b" },
    ]);
  });

  it("中间插入:前后 ctx 保住", () => {
    expect(lcsLines("a\nz", "a\nb\nz")).toEqual([
      { type: "ctx", text: "a" },
      { type: "add", text: "b" },
      { type: "ctx", text: "z" },
    ]);
  });

  it("完全相同 → 全 ctx", () => {
    expect(lcsLines("a\nb", "a\nb")).toEqual([
      { type: "ctx", text: "a" },
      { type: "ctx", text: "b" },
    ]);
  });
});

describe("multiEditDiff", () => {
  it("解析 edits JSON → 逐段 LCS,段头「第 N 处」", () => {
    const r = multiEditDiff('{"edits":[{"old_string":"x","new_string":"y"}]}');
    expect(r).not.toBeNull();
    expect(r!.segments).toHaveLength(1);
    expect(r!.segments[0].head).toBe("第 1 处");
    expect(r!.segments[0].lines).toEqual([
      { type: "del", text: "x" },
      { type: "add", text: "y" },
    ]);
  });

  it("兼容裸数组形态(runner 现状:json.dumps(edits))", () => {
    const r = multiEditDiff('[{"old_string":"a\\nb","new_string":"a\\nc"}]');
    expect(r).not.toBeNull();
    expect(r!.segments[0].lines).toEqual([
      { type: "ctx", text: "a" },
      { type: "del", text: "b" },
      { type: "add", text: "c" },
    ]);
  });

  it("多段:段头递增,lines 各自 LCS", () => {
    const r = multiEditDiff(
      '[{"old_string":"x","new_string":"y"},{"old_string":"1","new_string":"2"}]',
    );
    expect(r!.segments.map((s) => s.head)).toEqual(["第 1 处", "第 2 处"]);
    expect(r!.segments[1].lines).toEqual([
      { type: "del", text: "1" },
      { type: "add", text: "2" },
    ]);
  });

  it("坏 JSON / 非 edits 形态 → null(组件退回原文本)", () => {
    expect(multiEditDiff("{oops")).toBeNull();
    expect(multiEditDiff('"just a string"')).toBeNull();
    expect(multiEditDiff('{"nope":1}')).toBeNull();
  });
});

describe("LCS 护栏(F7: 超阈值退化全删+全加)", () => {
  it("行数和超 5000 退化为全删+全加(不分配 O(n·m) DP,语义无损)", () => {
    const big = Array.from({ length: 3000 }, (_, i) => `old-${i}`).join("\n");
    const bigNew = Array.from({ length: 2500 }, (_, i) => `new-${i}`).join("\n");
    const lines = lcsLines(big, bigNew);
    const adds = lines.filter((l) => l.type === "add").length;
    const dels = lines.filter((l) => l.type === "del").length;
    expect(dels).toBe(3000);
    expect(adds).toBe(2500);
    expect(lines[0]).toMatchObject({ type: "del", text: "old-0" });
    expect(lines[3000]).toMatchObject({ type: "add", text: "new-0" });
  });

  it("阈值内仍走 LCS 对齐(上下文行保留)", () => {
    const lines = lcsLines("a\nb\nc", "a\nx\nc");
    expect(lines).toEqual([
      { type: "ctx", text: "a" },
      { type: "del", text: "b" },
      { type: "add", text: "x" },
      { type: "ctx", text: "c" },
    ]);
  });
});

describe("diffStats", () => {
  it("只计 add/del,ctx 忽略", () => {
    expect(diffStats([
      { type: "ctx", text: "a" },
      { type: "del", text: "b" },
      { type: "add", text: "c" },
      { type: "add", text: "d" },
    ])).toEqual({ a: 2, d: 1 });
    expect(diffStats([])).toEqual({ a: 0, d: 0 });
  });
});
