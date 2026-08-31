// 日题（field 家态的"空间题字"，design §8/§10-P1）：date + 星期 + 陪伴天数。
// 纯逻辑收在这，组件只是排版；测试见 day-title.test.ts。

const WEEKDAYS = ["日", "一", "二", "三", "四", "五", "六"] as const;

export function dayTitleFace(now: Date = new Date(), firstSeen?: number): { main: string; sub: string | null } {
  const main = `${now.getMonth() + 1}月${now.getDate()}日 · 星期${WEEKDAYS[now.getDay()]}`;
  return { main, sub: companionSub(now, firstSeen) };
}

/** 「已陪伴你 N 天」：含当天（首日=1，数字当天内不闪）。未知 first_seen → null 不显示。 */
function companionSub(now: Date, firstSeen?: number): string | null {
  if (!firstSeen) return null;
  const day0 = new Date(firstSeen);
  day0.setHours(0, 0, 0, 0);
  const today0 = new Date(now);
  today0.setHours(0, 0, 0, 0);
  const days = Math.floor((today0.getTime() - day0.getTime()) / 86_400_000) + 1;
  return `已陪伴你 ${days} 天`;
}
