/**
 * 能力表面裁决：把「插件建议 + 注意力级别 + 用户是否明确要求 + 当前表面」裁决为实际展示级别。
 *
 * 硬规则（调研 §2.3）：模型或插件最多自动展开到 peek；进入 stage/focus 必须有用户明确意图。
 * 这条规则是「结果一回来就跳页」的根治手段，不可为了某个插件开后门。
 */
export type Presentation = "inline" | "peek" | "stage" | "focus";
export type Attention = "quiet" | "suggest" | "focus";

const RANK: Record<Presentation, number> = { inline: 0, peek: 1, stage: 2, focus: 3 };
const AUTO_MAX: Presentation = "peek";

export function decideSurface(input: {
  suggested: Presentation | null;
  attention: Attention;
  explicit: boolean;
  current: Presentation | null;
  supported?: Presentation[];
}): { presentation: Presentation | null; show: boolean } {
  // quiet：只记账，不打扰（进活动轨 / Feed，由调用方处理）
  if (input.attention === "quiet") return { presentation: null, show: false };

  let target: Presentation = input.suggested ?? "stage";
  if (!input.explicit && RANK[target] > RANK[AUTO_MAX]) target = AUTO_MAX;

  // 面板声明的支持范围：向下回落到它支持的最高档
  const supported = input.supported;
  if (supported && supported.length && !supported.includes(target)) {
    const fallback = [...supported].sort((a, b) => RANK[b] - RANK[a]).find((s) => RANK[s] <= RANK[target]);
    target = fallback ?? supported[0];
  }

  // 不把用户正在用的工作面缩掉：新结果只升不降
  if (input.current && RANK[input.current] > RANK[target]) target = input.current;

  return { presentation: target, show: true };
}
