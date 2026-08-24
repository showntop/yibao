import { describe, expect, it } from "vitest";
import { squashSpaces, stripTaskStatusEmoji, truncate } from "./text";

describe("truncate", () => {
  it("超长截断加省略号", () => {
    expect(truncate("abcdef", 3)).toBe("abc…");
  });
  it("未超长原样返回", () => {
    expect(truncate("abc", 3)).toBe("abc");
  });
});

describe("squashSpaces", () => {
  it("折叠连续空白并去首尾", () => {
    expect(squashSpaces("  a \n b\tc  ")).toBe("a b c");
  });
});

describe("stripTaskStatusEmoji", () => {
  it("剥掉行首状态 emoji 与紧随空白", () => {
    expect(stripTaskStatusEmoji("✅ 编码任务完成：整理 README")).toBe("编码任务完成：整理 README");
    expect(stripTaskStatusEmoji("❌ 沙箱脚本失败（退出码 3）：x")).toBe("沙箱脚本失败（退出码 3）：x");
    expect(stripTaskStatusEmoji("⏰ 沙箱脚本超时已终止：x")).toBe("沙箱脚本超时已终止：x");
    expect(stripTaskStatusEmoji("⏹ 编码任务已停止：x")).toBe("编码任务已停止：x");
  });
  it("无前缀或非行首原样返回", () => {
    expect(stripTaskStatusEmoji("到点提醒：喝水")).toBe("到点提醒：喝水");
    expect(stripTaskStatusEmoji("完成了 ✅")).toBe("完成了 ✅");
  });
});
