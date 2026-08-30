import { describe, expect, it } from "vitest";
import { dayTitleFace } from "./day-title.ts";

describe("dayTitleFace", () => {
  it("formats month-day with weekday in the specimen voice", () => {
    // 2026-08-28 实际是星期五（系统日历核对过）；specimen 小样里的"星期四"是手写笔误
    expect(dayTitleFace(new Date("2026-08-28T09:00:00")).main).toBe("8月28日 · 星期五");
  });

  it("rolls the weekday correctly across the week", () => {
    expect(dayTitleFace(new Date("2026-08-30T23:59:00")).main).toBe("8月30日 · 星期日");
    expect(dayTitleFace(new Date("2026-09-01T00:05:00")).main).toBe("9月1日 · 星期二");
  });

  it("uses midnight as the day boundary, not the session start", () => {
    expect(dayTitleFace(new Date("2026-08-28T23:59:59")).main).toBe("8月28日 · 星期五");
    expect(dayTitleFace(new Date("2026-08-29T00:00:00")).main).toBe("8月29日 · 星期六");
  });

  it("counts companion days as full days elapsed since first_seen", () => {
    const first = new Date("2026-06-01T10:00:00").getTime();
    expect(dayTitleFace(new Date("2026-08-30T09:00:00"), first).sub).toBe("已陪伴你 90 天");
  });

  it("anchors the count to midnight so the number does not flicker within a day", () => {
    const first = new Date("2025-08-30T23:59:00").getTime();
    const early = dayTitleFace(new Date("2026-08-30T00:01:00"), first).sub;
    const late = dayTitleFace(new Date("2026-08-30T23:59:00"), first).sub;
    expect(early).toBe(late);
  });

  it("hides the sub on day one and when first_seen is unknown", () => {
    const now = new Date("2026-08-30T09:00:00");
    expect(dayTitleFace(now, new Date("2026-08-30T08:00:00").getTime()).sub).toBeNull();
    expect(dayTitleFace(now).sub).toBeNull();
  });
});
