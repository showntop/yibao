/** 整桌「此刻」：日期、钟点、这一周，不编诗。 */

const WEEK = ["日", "一", "二", "三", "四", "五", "六"] as const;

export type WhenFace = {
  date: string;
  weekday: string;
  clock: string;
};

export type WhenDay = {
  week: string;
  date: number;
  today: boolean;
};

function pad(n: number): string {
  return n < 10 ? `0${n}` : String(n);
}

function dayKey(now: Date): string {
  return `${now.getFullYear()}-${now.getMonth()}-${now.getDate()}`;
}

/** 含当天的周一。 */
export function mondayOf(now: Date): Date {
  const d = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const day = d.getDay();
  d.setDate(d.getDate() + (day === 0 ? -6 : 1 - day));
  return d;
}

export function whenFace(now = new Date()): WhenFace {
  return {
    date: `${now.getMonth() + 1}月${now.getDate()}日`,
    weekday: `周${WEEK[now.getDay()]}`,
    clock: `${pad(now.getHours())}:${pad(now.getMinutes())}`,
  };
}

export function whenWeek(now = new Date()): WhenDay[] {
  const mon = mondayOf(now);
  const today = dayKey(now);
  return Array.from({ length: 7 }, (_, i) => {
    const d = new Date(mon);
    d.setDate(mon.getDate() + i);
    return {
      week: WEEK[(i + 1) % 7],
      date: d.getDate(),
      today: dayKey(d) === today,
    };
  });
}
