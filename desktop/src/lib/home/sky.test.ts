import { describe, expect, it } from "vitest";
import { skyPhase } from "./sky.ts";

/** 天光：石面桌底色温随真实时间极缓变化（design §6）。时段边界要稳，供 CSS 变量切换。 */
describe("skyPhase", () => {
  it("maps the four phases across the day", () => {
    expect(skyPhase(new Date("2026-08-30T06:00:00"))).toBe("dawn");
    expect(skyPhase(new Date("2026-08-30T12:00:00"))).toBe("day");
    expect(skyPhase(new Date("2026-08-30T18:00:00"))).toBe("dusk");
    expect(skyPhase(new Date("2026-08-30T22:00:00"))).toBe("night");
  });

  it("holds the phase at boundaries (start-inclusive, night wraps midnight)", () => {
    expect(skyPhase(new Date("2026-08-30T05:00:00"))).toBe("dawn");
    expect(skyPhase(new Date("2026-08-30T08:00:00"))).toBe("day");
    expect(skyPhase(new Date("2026-08-30T16:00:00"))).toBe("dusk");
    expect(skyPhase(new Date("2026-08-30T20:00:00"))).toBe("night");
    expect(skyPhase(new Date("2026-08-30T04:59:00"))).toBe("night");
    expect(skyPhase(new Date("2026-08-30T00:00:00"))).toBe("night");
  });
});
