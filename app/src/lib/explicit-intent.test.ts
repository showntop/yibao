// app/src/lib/explicit-intent.test.ts
import { describe, expect, it } from "vitest";
import { matchExplicitOpen } from "./explicit-intent";

const PLUGINS = [
  { id: "notes", name: "闪念盘" },
  { id: "calendar", name: "日历" },
];

describe("matchExplicitOpen", () => {
  it("动词 + 插件名 → 命中", () => {
    expect(matchExplicitOpen("打开闪念盘", PLUGINS)).toBe("notes");
  });

  it("动词 + 插件 id → 命中", () => {
    expect(matchExplicitOpen("展开 calendar", PLUGINS)).toBe("calendar");
  });

  it("只有动词没有宾语 → 不命中", () => {
    expect(matchExplicitOpen("打开", PLUGINS)).toBeNull();
  });

  it("只有插件名没有动词 → 不命中", () => {
    // 「闪念盘里有牛奶吗」是查询不是打开指令，绝不能判成 explicit
    expect(matchExplicitOpen("闪念盘里有牛奶吗", PLUGINS)).toBeNull();
  });

  it("弱动词不算明确意图", () => {
    // 「看看」「查查」「有没有」语气太弱，误判的代价是抢屏——宁可漏
    expect(matchExplicitOpen("看看闪念盘", PLUGINS)).toBeNull();
    expect(matchExplicitOpen("查查闪念盘", PLUGINS)).toBeNull();
    expect(matchExplicitOpen("有没有日历", PLUGINS)).toBeNull();
  });

  it("否定语气 → 不命中", () => {
    expect(matchExplicitOpen("不要打开日历", PLUGINS)).toBeNull();
    expect(matchExplicitOpen("别显示日历", PLUGINS)).toBeNull();
  });

  it("疑问语气 → 不命中", () => {
    expect(matchExplicitOpen("怎么打开日历？", PLUGINS)).toBeNull();
  });

  it("空插件列表 → 不命中", () => {
    expect(matchExplicitOpen("打开闪念盘", [])).toBeNull();
  });
});
