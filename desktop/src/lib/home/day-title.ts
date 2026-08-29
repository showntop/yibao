// 日题（field 家态的"空间题字"，design §8/§10-P1）：date + 星期，serif display 档渲染。
// 纯逻辑收在这，组件只是排版；测试见 day-title.test.ts。

const WEEKDAYS = ["日", "一", "二", "三", "四", "五", "六"] as const;

export function dayTitleFace(now: Date = new Date()): { main: string } {
  const main = `${now.getMonth() + 1}月${now.getDate()}日 · 星期${WEEKDAYS[now.getDay()]}`;
  return { main };
}
