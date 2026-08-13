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

  // ---- 回落不得击穿自动上限（回归：兜底曾把档位抬回 stage/focus）----

  it("面板只支持 stage/focus 时模型不得自动展开——只记账进活动轨", () => {
    expect(decideSurface({ suggested: null, attention: "suggest", explicit: false, current: null, supported: ["stage", "focus"] }))
      .toEqual({ presentation: null, show: false });
    // 技能建议最轻的 inline 也一样：回落只能向上找到 stage，仍超自动上限
    expect(decideSurface({ suggested: "inline", attention: "suggest", explicit: false, current: null, supported: ["stage", "focus"] }))
      .toEqual({ presentation: null, show: false });
  });

  it("裁决与 manifest 里 surfaces 的声明顺序无关", () => {
    const asc = decideSurface({ suggested: null, attention: "suggest", explicit: true, current: null, supported: ["stage", "focus"] });
    const desc = decideSurface({ suggested: null, attention: "suggest", explicit: true, current: null, supported: ["focus", "stage"] });
    expect(asc).toEqual(desc);
    expect(asc).toEqual({ presentation: "stage", show: true });
  });

  it("明确意图 + 建议档位低于面板支持范围 → 取它支持的最低档", () => {
    expect(decideSurface({ suggested: "inline", attention: "suggest", explicit: true, current: null, supported: ["stage", "focus"] }))
      .toEqual({ presentation: "stage", show: true });
  });

  it("自动上限回落到面板支持的最高可自动档（peek 不支持则退到 inline）", () => {
    expect(decideSurface({ suggested: "stage", attention: "suggest", explicit: false, current: null, supported: ["inline", "stage"] }))
      .toEqual({ presentation: "inline", show: true });
  });

  it("current 抬升不得越过面板支持范围", () => {
    expect(decideSurface({ suggested: "peek", attention: "suggest", explicit: false, current: "focus", supported: ["inline", "peek"] }))
      .toEqual({ presentation: "peek", show: true });
  });
});
