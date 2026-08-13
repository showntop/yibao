import { describe, expect, it } from "vitest";
import { decideSurface } from "./surface-policy";

describe("decideSurface", () => {
  it("模型自作主张时最多到 peek——绝不自动 stage/focus", () => {
    expect(decideSurface({ suggested: "focus", attention: "suggest", explicit: false, current: null }))
      .toEqual({ presentation: "peek", show: true });
    expect(decideSurface({ suggested: "stage", attention: "suggest", explicit: false, current: null }))
      .toEqual({ presentation: "peek", show: true });
  });

  it("attention=quiet 只进活动轨，不展开任何表面", () => {
    expect(decideSurface({ suggested: "inline", attention: "quiet", explicit: false, current: null }))
      .toEqual({ presentation: null, show: false });
  });

  it("用户明确请求时按建议展开，可达 stage/focus", () => {
    expect(decideSurface({ suggested: "stage", attention: "suggest", explicit: true, current: null }))
      .toEqual({ presentation: "stage", show: true });
  });

  it("插件不声明 presentation → 回落 stage（保持 Slice 1 既有行为）", () => {
    expect(decideSurface({ suggested: null, attention: "suggest", explicit: true, current: null }))
      .toEqual({ presentation: "stage", show: true });
  });

  it("已在 stage/focus 时新结果不降级——不把用户正在用的工作面缩掉", () => {
    expect(decideSurface({ suggested: "inline", attention: "suggest", explicit: false, current: "stage" }))
      .toEqual({ presentation: "stage", show: true });
  });

  it("面板不支持的档位向下回落到它支持的最高档", () => {
    expect(decideSurface({ suggested: "focus", attention: "suggest", explicit: true, current: null, supported: ["inline", "peek"] }))
      .toEqual({ presentation: "peek", show: true });
  });
});
