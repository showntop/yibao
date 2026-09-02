import { describe, expect, it } from "vitest";
import { capabilityGapFromResult, capabilityGapTitle } from "./capability-gap.ts";
import type { BrainResult } from "../brain";

describe("capabilityGapFromResult", () => {
  it("命中：enforced 且有缺失 stage → 抽出边界卡素材", () => {
    const gap = capabilityGapFromResult({
      success: true,
      data: {
        capability: {
          ready: false,
          enforced: true,
          available_stages: ["选题", "证据", "脚本"],
          missing_stages: ["分镜", "素材", "配音"],
          blocked_reason: "分镜、素材、配音 缺能力 provider",
          degradation: "可做到脚本；分镜起缺能力，安装对应 provider 后可继续",
        },
      },
    } as BrainResult);
    expect(gap).not.toBeNull();
    expect(gap?.through).toBe("脚本");
    expect(gap?.available).toEqual(["选题", "证据", "脚本"]);
    expect(gap?.missing).toEqual(["分镜", "素材", "配音"]);
    expect(gap?.note).toBe("可做到脚本；分镜起缺能力，安装对应 provider 后可继续");
  });

  it("degradation 缺失时回退 blocked_reason", () => {
    const gap = capabilityGapFromResult({
      success: true,
      data: {
        capability: {
          ready: false,
          enforced: true,
          available_stages: [],
          missing_stages: ["理解"],
          blocked_reason: "理解 缺能力 provider",
        },
      },
    } as BrainResult);
    expect(gap?.through).toBe("");
    expect(gap?.note).toBe("理解 缺能力 provider");
  });

  it("不命中：enforced=false（info 策略只转述，不出卡）", () => {
    const result = {
      success: true,
      data: {
        capability: {
          ready: false,
          enforced: false,
          available_stages: ["理解"],
          missing_stages: ["推进"],
        },
      },
    } as BrainResult;
    expect(capabilityGapFromResult(result)).toBeNull();
  });

  it("不命中：enforced 但无缺失", () => {
    const result = {
      success: true,
      data: {
        capability: { ready: true, enforced: true, available_stages: ["理解"], missing_stages: [] },
      },
    } as BrainResult;
    expect(capabilityGapFromResult(result)).toBeNull();
  });

  it("不命中：结果无 capability 字段 / 工具失败 / 无结果", () => {
    expect(capabilityGapFromResult({ success: true, data: { human: "好了" } } as BrainResult)).toBeNull();
    expect(capabilityGapFromResult({
      success: false,
      data: { capability: { enforced: true, missing_stages: ["分镜"] } },
    } as BrainResult)).toBeNull();
    expect(capabilityGapFromResult(undefined)).toBeNull();
  });
});

describe("capabilityGapTitle", () => {
  it("有可达前缀：能力边界 · 可做到X", () => {
    expect(capabilityGapTitle({ through: "脚本", available: ["脚本"], missing: ["分镜"], note: "" }))
      .toBe("能力边界 · 可做到脚本");
  });

  it("无可达前缀：只报能力边界", () => {
    expect(capabilityGapTitle({ through: "", available: [], missing: ["分镜"], note: "" }))
      .toBe("能力边界");
  });
});
