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

  // 支持范围按档位升序规整一次，后续一律基于它取值——避免结果依赖 manifest 里的声明顺序
  const asc = input.supported?.length ? [...input.supported].sort((a, b) => RANK[a] - RANK[b]) : null;
  const highestAtMost = (cap: Presentation): Presentation | undefined =>
    asc ? [...asc].reverse().find((s) => RANK[s] <= RANK[cap]) : cap;

  let target: Presentation = input.suggested ?? "stage";

  // 面板声明的支持范围：优先向下回落；没有更低档就取它支持的最低档
  if (asc && !asc.includes(target)) target = highestAtMost(target) ?? asc[0];

  // 自动上限必须在回落「之后」施加：回落可能把档位抬高（面板只支持 stage/focus 时），
  // 先施加会被绕过——那正是「结果一回来就跳页」的复发路径。
  if (!input.explicit && RANK[target] > RANK[AUTO_MAX]) {
    const capped = highestAtMost(AUTO_MAX);
    // 连最低支持档都超过上限 → 不自动展开，只记账（用户可从活动轨点开）
    if (!capped) return { presentation: null, show: false };
    target = capped;
  }

  // 不把用户正在用的工作面缩掉：新结果只升不降——但不得越过面板支持范围
  if (input.current && RANK[input.current] > RANK[target] && (!asc || asc.includes(input.current))) {
    target = input.current;
  }

  return { presentation: target, show: true };
}
