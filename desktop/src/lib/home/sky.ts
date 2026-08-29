// 天光（design §6）：石面桌底色温随真实时间极缓变化。零动画库——纯函数给时段，
// CSS 变量按 [data-sky] 切换，过渡交给 transition。测试见 sky.test.ts。

export type SkyPhase = "dawn" | "day" | "dusk" | "night";

/** 时段边界（本地时）：05-08 晨冷白，08-16 午暖，16-20 暮金，其余夜深。 */
export function skyPhase(now: Date): SkyPhase {
  const h = now.getHours();
  if (h >= 5 && h < 8) return "dawn";
  if (h >= 8 && h < 16) return "day";
  if (h >= 16 && h < 20) return "dusk";
  return "night";
}
