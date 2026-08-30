// 今日枢轴（wb-prototype home.png 的「今日」瓷片）：月历 + 今日安排。
// 纯逻辑收在这，测试见 today-axis.test.ts；组件只是排版。
import type { FeedItem } from "../brain";

export interface MonthView {
  year: number;
  month: number; // 0-based，与 Date 一致
}

export interface MonthCell {
  date: number;
  inMonth: boolean;
  today: boolean;
}

const DAY = 86_400_000;

function dayKey(d: Date): string {
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

/** 周日为首列的月历网格，前后月补位灰显；today 只圈当前查看月的今天。 */
export function monthGrid(now: Date, view: MonthView): MonthCell[][] {
  const first = new Date(view.year, view.month, 1);
  const start = new Date(first);
  start.setDate(1 - first.getDay()); // 回到本周周日
  const todayKey = dayKey(now);
  const weeks: MonthCell[][] = [];
  for (let w = 0; w < 6; w++) {
    const row: MonthCell[] = [];
    for (let i = 0; i < 7; i++) {
      const d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + w * 7 + i);
      const inMonth = d.getMonth() === view.month;
      row.push({
        date: d.getDate(),
        inMonth,
        today: inMonth && dayKey(d) === todayKey,
      });
    }
    // 整行都越界（补位月）即收尾，不输出空的第六行
    if (w > 0 && row.every((c) => !c.inMonth)) break;
    weeks.push(row);
  }
  return weeks;
}

export function monthNav(view: MonthView, dir: 1 | -1): MonthView {
  const m = view.month + dir;
  if (m < 0) return { year: view.year - 1, month: 11 };
  if (m > 11) return { year: view.year + 1, month: 0 };
  return { year: view.year, month: m };
}

export type AgendaStatus = "done" | "active" | "upcoming";

export interface AgendaItem {
  time: string; // HH:MM
  title: string;
  status: AgendaStatus;
}

/** 今日安排：feed 里的 reminder 项 → 按时间排序，进行中=近 45 分钟窗口。 */
export function agendaOf(feed: readonly FeedItem[], now: Date, windowMin = 45): AgendaItem[] {
  const dayStart = new Date(now);
  dayStart.setHours(0, 0, 0, 0);
  const t0 = dayStart.getTime();
  const items = feed
    .filter((it) => it.kind === "reminder" && it.status !== "ignore")
    .filter((it) => it.ts >= t0 && it.ts < t0 + DAY)
    .sort((a, b) => a.ts - b.ts);
  const pad = (n: number) => String(n).padStart(2, "0");
  return items.map((it) => {
    const d = new Date(it.ts);
    const diff = now.getTime() - it.ts;
    const status: AgendaStatus =
      diff > windowMin * 60_000 ? "done" : diff >= -60_000 ? "active" : "upcoming";
    return { time: `${pad(d.getHours())}:${pad(d.getMinutes())}`, title: it.text, status };
  });
}
