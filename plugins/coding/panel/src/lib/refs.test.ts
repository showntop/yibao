import { describe, expect, it } from "vitest";
import { basename, composeRefs, matchAtQuery, pushRef } from "./refs";

describe("composeRefs", () => {
  it("空数组 → 空串(不追加引用段)", () => {
    expect(composeRefs([])).toBe("");
  });
  it("单条 → 「\\n\\n引用文件:\\n@a」", () => {
    expect(composeRefs(["a.ts"])).toBe("\n\n引用文件:\n@a.ts");
  });
  it("多条逐行 @ 前缀", () => {
    expect(composeRefs(["a.ts", "dir/b.ts"])).toBe("\n\n引用文件:\n@a.ts\n@dir/b.ts");
  });
});

describe("matchAtQuery", () => {
  it("光标前文本尾部 @ 起片段 → start + query", () => {
    expect(matchAtQuery("hello @sr")).toEqual({ start: 6, query: "sr" });
  });
  it("裸 @ → 空 query 也触发(弹出全量候选)", () => {
    expect(matchAtQuery("@")).toEqual({ start: 0, query: "" });
  });
  it("路径字符全收(字母数字 _ - . /)", () => {
    expect(matchAtQuery("@src/lib-x/y_z.ts")).toEqual({ start: 0, query: "src/lib-x/y_z.ts" });
  });
  it("无 @ → null", () => {
    expect(matchAtQuery("hello")).toBeNull();
  });
  it("@ 片段后有空格(光标不在片段尾)→ null", () => {
    expect(matchAtQuery("@foo bar")).toBeNull();
  });
  it("email 式 a@b 同样触发(与原正则 parity,不过滤前缀字符)", () => {
    expect(matchAtQuery("a@b")).toEqual({ start: 1, query: "b" });
  });
  it("@ 前是中文字符正常触发", () => {
    expect(matchAtQuery("改一下 @RE")).toEqual({ start: 4, query: "RE" });
  });
});

describe("basename", () => {
  it("相对路径取末段", () => {
    expect(basename("src/lib/refs.ts")).toBe("refs.ts");
  });
  it("绝对路径取末段(截图落盘路径)", () => {
    expect(basename("/abs/path/shot.png")).toBe("shot.png");
  });
  it("裸文件名原样", () => {
    expect(basename("refs.ts")).toBe("refs.ts");
  });
  it("空串 → 空串(parity:split('/').pop())", () => {
    expect(basename("")).toBe("");
  });
  it("尾斜杠 → 空串(parity:与原 split('/').pop() 行为一致)", () => {
    expect(basename("dir/")).toBe("");
  });
});

describe("pushRef", () => {
  it("新路径追加成新数组,原数组不变", () => {
    const refs = ["a.ts"];
    const next = pushRef(refs, "b.ts");
    expect(next).toEqual(["a.ts", "b.ts"]);
    expect(refs).toEqual(["a.ts"]);
  });
  it("重复路径 → 返回原数组(同引用,同文件重复引用无意义)", () => {
    const refs = ["a.ts"];
    expect(pushRef(refs, "a.ts")).toBe(refs);
  });
});
