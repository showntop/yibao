import { describe, expect, it } from "vitest";
import { decideSurface, type SurfaceMode } from "./surface-policy";

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
    expect(decideSurface({ suggested: "focus", attention: "suggest", explicit: true, current: null, supported: ["inline", "stage"] }))
      .toEqual({ presentation: "stage", show: true });
  });

  // ---- 回落不得击穿自动上限（回归：兜底曾把档位抬回 stage/focus）----

  it("模型自动调用最多得到瞬态预览——绝不自动 stage/focus", () => {
    // 只声明工作面的面板：自动结果承接为 peek（stage 的瞬态 compact 形态）；
    // 真不适合紧凑预览的面板应声明 min_width，由宿主按几何约束跳过。
    expect(decideSurface({ suggested: null, attention: "suggest", explicit: false, current: null, supported: ["stage", "focus"] }))
      .toEqual({ presentation: "peek", show: true });
    // 技能建议最轻的 inline 也一样：面板不支持 inline → 向上回落到 stage，仍被压成 peek
    expect(decideSurface({ suggested: "inline", attention: "suggest", explicit: false, current: null, supported: ["stage", "focus"] }))
      .toEqual({ presentation: "peek", show: true });
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

  it("peek 是 stage 的瞬态 compact 形态：面板支持 stage 即可被自动瞬态预览", () => {
    expect(decideSurface({ suggested: "stage", attention: "suggest", explicit: false, current: null, supported: ["inline", "stage"] }))
      .toEqual({ presentation: "peek", show: true });
    // 连 stage 都不支持（只支持 inline）→ 自动上限退到 inline
    expect(decideSurface({ suggested: "stage", attention: "suggest", explicit: false, current: null, supported: ["inline"] }))
      .toEqual({ presentation: "inline", show: true });
  });

  it("wire 仍可携带 peek（surface.open 的瞬态预览请求），宿主照常承接", () => {
    expect(decideSurface({ suggested: "peek", attention: "suggest", explicit: false, current: null }))
      .toEqual({ presentation: "peek", show: true });
    // 但瞬态预览要求面板支持 stage；只支持 inline 的面板回落 inline
    expect(decideSurface({ suggested: "peek", attention: "suggest", explicit: false, current: null, supported: ["inline"] }))
      .toEqual({ presentation: "inline", show: true });
  });

  it("current 抬升不得越过面板支持范围", () => {
    expect(decideSurface({ suggested: "peek", attention: "suggest", explicit: false, current: "focus", supported: ["inline", "stage"] }))
      .toEqual({ presentation: "peek", show: true });
  });

  it("面板只支持 focus 时自动调用不展开——只记账进活动轨（自动上限的最后兜底）", () => {
    expect(decideSurface({ suggested: "stage", attention: "suggest", explicit: false, current: null, supported: ["focus"] }))
      .toEqual({ presentation: null, show: false });
  });

  it("explicit 的瞬态预览请求保持 peek——不被升级成整个 stage", () => {
    expect(decideSurface({ suggested: "peek", attention: "suggest", explicit: true, current: null, supported: ["inline", "stage"] }))
      .toEqual({ presentation: "peek", show: true });
  });

  it("当前正挂着瞬态预览（current=peek）时新结果只升不降，按 stage 查支持范围", () => {
    // 面板仍支持 stage → 保持 peek，不被 inline 结果缩掉
    expect(decideSurface({ suggested: "inline", attention: "suggest", explicit: false, current: "peek", supported: ["inline", "stage"] }))
      .toEqual({ presentation: "peek", show: true });
    // 面板已不支持 stage → 允许落到 inline
    expect(decideSurface({ suggested: "inline", attention: "suggest", explicit: false, current: "peek", supported: ["inline"] }))
      .toEqual({ presentation: "inline", show: true });
  });

  it("legacy wire 里残留的 peek 支持声明被安全忽略（不炸、不误判）", () => {
    // 旧 sidecar → 新前端的混跑方向：surfaces 可能还带 "peek"，过滤后按 ["inline"] 裁决
    expect(decideSurface({ suggested: "stage", attention: "suggest", explicit: false, current: null, supported: ["inline", "peek"] as unknown as SurfaceMode[] }))
      .toEqual({ presentation: "inline", show: true });
  });
});
