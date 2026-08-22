import { describe, expect, it } from "vitest";
import { mondayOf, whenFace, whenWeek } from "./home-when-face.ts";

describe("whenFace", () => {
  it("writes the calendar day and a 24h clock", () => {
    const face = whenFace(new Date("2026-08-22T22:07:00"));
    expect(face.date).toBe("8月22日");
    expect(face.weekday).toBe("周六");
    expect(face.clock).toBe("22:07");
  });

  it("pads a single-digit hour", () => {
    expect(whenFace(new Date("2026-01-03T09:05:00")).clock).toBe("09:05");
    expect(whenFace(new Date("2026-01-03T09:05:00")).date).toBe("1月3日");
    expect(whenFace(new Date("2026-01-03T09:05:00")).weekday).toBe("周六");
  });
});

describe("whenWeek", () => {
  it("starts Monday and marks today", () => {
    const now = new Date("2026-08-22T22:07:00");
    const mon = mondayOf(now);
    expect(mon.getFullYear()).toBe(2026);
    expect(mon.getMonth()).toBe(7);
    expect(mon.getDate()).toBe(17);
    const days = whenWeek(now);
    expect(days.map((d) => d.week)).toEqual(["一", "二", "三", "四", "五", "六", "日"]);
    expect(days.map((d) => d.date)).toEqual([17, 18, 19, 20, 21, 22, 23]);
    expect(days[5]?.today).toBe(true);
    expect(days.filter((d) => d.today)).toHaveLength(1);
  });

  it("crosses a month boundary", () => {
    const days = whenWeek(new Date("2026-01-03T09:05:00"));
    expect(days.map((d) => d.date)).toEqual([29, 30, 31, 1, 2, 3, 4]);
    expect(days[5]?.today).toBe(true);
  });
});
