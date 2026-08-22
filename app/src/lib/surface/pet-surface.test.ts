import { describe, expect, it } from "vitest";
import { deactivateAll, petFormOf, surfaceCount, type SurfaceAttr } from "./pet-surface";

describe("petFormOf", () => {
  it("stage → 开窗", () => {
    expect(petFormOf({ presentation: "stage", show: true })).toBe("window");
  });

  it("focus → 开窗", () => {
    expect(petFormOf({ presentation: "focus", show: true })).toBe("window");
  });

  it("inline → 行", () => {
    expect(petFormOf({ presentation: "inline", show: true })).toBe("line");
  });

  it("peek → 行（小窗只有行一种形态）", () => {
    expect(petFormOf({ presentation: "peek", show: true })).toBe("line");
  });

  it("不展开（quiet）→ 行", () => {
    expect(petFormOf({ presentation: null, show: false })).toBe("line");
  });

  it("show=false 时即便带着 stage 也不开窗", () => {
    // 裁决器非 explicit 本就封顶 peek，这里是双保险：
    // 「模型自动调用绝不在小窗开浮窗」不能依赖单一判断
    expect(petFormOf({ presentation: "stage", show: false })).toBe("line");
  });
});

describe("surfaceCount", () => {
  it("db 类结果数 rows", () => {
    expect(surfaceCount({ rows: [1, 2, 3] })).toBe(3);
  });

  it("没有 rows → null（只显示面板名）", () => {
    expect(surfaceCount({ id: "abc" })).toBeNull();
  });

  it("非对象输入不炸", () => {
    expect(surfaceCount(null)).toBeNull();
    expect(surfaceCount(undefined)).toBeNull();
    expect(surfaceCount("x")).toBeNull();
  });
});

describe("deactivateAll", () => {
  it("所有带表面的行一律失活——流里永远最多一条可点", () => {
    const attr = (live: boolean): SurfaceAttr => ({ panel: "notes:list", title: "闪念列表", count: 3, live });
    const rows = [{ surface: attr(true) }, {}, { surface: attr(true) }];
    deactivateAll(rows);
    expect(rows[0].surface!.live).toBe(false);
    expect(rows[2].surface!.live).toBe(false);
  });

  it("已失活的行保持失活（幂等）", () => {
    const rows = [{ surface: { panel: "p", title: "t", count: null, live: false } }];
    deactivateAll(rows);
    expect(rows[0].surface.live).toBe(false);
  });
});
