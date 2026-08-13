// app/src/lib/explicit-intent.test.ts
import { describe, expect, it } from "vitest";
import { matchExplicitOpen } from "./explicit-intent";

const PLUGINS = [
  { id: "notes", name: "闪念盘" },
  { id: "calendar", name: "日历" },
];

describe("matchExplicitOpen", () => {
  it.each([
    ["打开日历", "calendar"],
    ["打开闪念盘", "notes"],
    ["展开 calendar", "calendar"],
  ])("动词开头 + 插件 → 命中：%s", (input, expected) => {
    expect(matchExplicitOpen(input, PLUGINS)).toBe(expected);
  });

  it.each([
    ["不要打开日历"],
    ["别给我看日历"],
    ["能打开日历吗"],
    ["怎么打开日历？"],
    ["打开日历了吗"],
    ["闪念盘里有牛奶吗"],
    ["看看闪念盘"],
    ["帮我打开日历"],
  ])("非明确打开指令 → 不命中：%s", (input) => {
    expect(matchExplicitOpen(input, PLUGINS)).toBeNull();
  });

  it("只有动词没有宾语 → 不命中", () => {
    expect(matchExplicitOpen("打开", PLUGINS)).toBeNull();
  });

  it("空插件列表 → 不命中", () => {
    expect(matchExplicitOpen("打开闪念盘", [])).toBeNull();
  });
});
