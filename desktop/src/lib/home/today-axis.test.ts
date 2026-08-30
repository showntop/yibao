import { describe, expect, it } from "vitest";
import { agendaOf, monthGrid, monthNav } from "./today-axis.ts";
import type { FeedItem } from "../brain";

function feed(partial: Partial<FeedItem> & Pick<FeedItem, "id" | "text">): FeedItem {
  return { ts: 1, kind: "reminder", read: 0, status: "none", ...partial };
}

/** 2026-08-30（周日）的固定时刻 */
const NOW = new Date("2026-08-30T10:15:00");

describe("monthGrid", () => {
  it("builds Sunday-first weeks with in-month padding", () => {
    // 2026-08：8/1 是周六 → 首行 7/26..8/1，8/30 是周日最后一行开头
    const grid = monthGrid(new Date("2026-08-30T10:00:00"), { year: 2026, month: 7 });
    expect(grid[0]).toHaveLength(7);
    expect(grid[0][0]).toMatchObject({ date: 26, inMonth: false });
    expect(grid[0][6]).toMatchObject({ date: 1, inMonth: true });
    expect(grid.flat().find((c) => c.today)).toMatchObject({ date: 30, inMonth: true });
  });

  it("marks today only for the viewed month's today", () => {
    const grid = monthGrid(NOW, { year: 2026, month: 7 });
    expect(grid.flat().filter((c) => c.today)).toHaveLength(1);
    const july = monthGrid(NOW, { year: 2026, month: 6 });
    expect(july.flat().some((c) => c.today)).toBe(false);
  });

  it("navigates months with year rollover", () => {
    expect(monthNav({ year: 2026, month: 0 }, 1)).toEqual({ year: 2026, month: 1 });
    expect(monthNav({ year: 2026, month: 0 }, -1)).toEqual({ year: 2025, month: 11 });
    expect(monthNav({ year: 2026, month: 11 }, 1)).toEqual({ year: 2027, month: 0 });
  });
});

describe("agendaOf", () => {
  const at = (hhmm: string) => new Date(`2026-08-30T${hhmm}:00`).getTime();

  it("lists today's reminders sorted by time with honest status", () => {
    const items = [
      feed({ id: 3, text: "19:00 晚间复盘", ts: at("19:00") }),
      feed({ id: 1, text: "09:00 开战会", ts: at("09:00") }),
      feed({ id: 2, text: "10:00 产品评审", ts: at("10:00") }),
    ];
    expect(agendaOf(items, NOW)).toEqual([
      { time: "09:00", title: "09:00 开战会", status: "done" }, // 早于 now 45 分钟以上
      { time: "10:00", title: "10:00 产品评审", status: "active" }, // 进行中窗口
      { time: "19:00", title: "19:00 晚间复盘", status: "upcoming" },
    ]);
  });

  it("keeps only today's reminders and drops ignored ones", () => {
    const items = [
      feed({ id: 1, text: "昨天的事", ts: new Date("2026-08-29T09:00:00").getTime() }),
      feed({ id: 2, text: "已忽略", ts: at("12:00"), status: "ignore" }),
      feed({ id: 3, text: "明晚的事", ts: new Date("2026-08-31T19:00:00").getTime() }),
    ];
    expect(agendaOf(items, NOW)).toEqual([]);
  });
});
