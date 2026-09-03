/**
 * 能力表面裁决：把「插件建议 + 注意力级别 + 用户是否明确要求 + 当前表面」裁决为实际展示级别。
 *
 * 三态合同（架构 §6）：插件/面板只声明 inline / stage / focus 三档公共枚举（SurfaceMode）。
 * peek 不是第四档——它是宿主对 Stage 的瞬态 compact placement（§6.5），只出不进：
 * 只由 AUTO_MAX 封顶或用户主动收起工作面产生，插件无法声明、无法请求。
 * （wire 兼容例外：surface.open 仍可用 "peek" 请求一次瞬态预览，见 suggested 参数。）
 *
 * 硬规则（调研 §2.3）：模型或插件最多自动展开到 peek；进入 stage/focus 必须有用户明确意图。
 * 这条规则是「结果一回来就跳页」的根治手段，不可为了某个插件开后门。
 */

/** 插件公共三态：manifest surfaces / tool presentation 的合法值全集。 */
export type SurfaceMode = "inline" | "stage" | "focus";
/** 宿主实际展示档位：三态 + peek（Stage 的瞬态 compact placement）。 */
export type Presentation = SurfaceMode | "peek";
export type Attention = "quiet" | "suggest" | "focus";

/** placement 全序：inline < peek < stage < focus（peek 是 stage 的 compact 形态，排在 stage 前） */
const RANK: Record<Presentation, number> = { inline: 0, peek: 1, stage: 2, focus: 3 };
/** 三态内部序：面板支持列表的排序与回落依据 */
const MODE_RANK: Record<SurfaceMode, number> = { inline: 0, stage: 1, focus: 2 };
const AUTO_MAX: Presentation = "peek";

/** 请求某个 placement 需要面板实际支持的三态档位，以及回落时的天花板 */
const REQUIRES: Record<Presentation, { mode: SurfaceMode; ceiling: number }> = {
  inline: { mode: "inline", ceiling: MODE_RANK.inline },
  peek: { mode: "stage", ceiling: MODE_RANK.stage }, // peek 是 compact stage，可由 inline 兜底
  stage: { mode: "stage", ceiling: MODE_RANK.stage },
  focus: { mode: "focus", ceiling: MODE_RANK.focus },
};

export function decideSurface(input: {
  /** wire 级建议：插件只可能给出三态；"peek" 仅来自 surface.open 的瞬态预览请求 */
  suggested: Presentation | null;
  attention: Attention;
  explicit: boolean;
  current: Presentation | null;
  /** 面板声明的三态支持范围（sidecar 已保证不含 peek） */
  supported?: SurfaceMode[];
}): { presentation: Presentation | null; show: boolean } {
  // quiet：只记账，不打扰（进活动轨 / Feed，由调用方处理）
  if (input.attention === "quiet") return { presentation: null, show: false };

  // 支持范围按三态升序规整一次，后续一律基于它取值——避免结果依赖 manifest 里的声明顺序。
  // 先过滤：旧 sidecar → 新前端的混跑方向里 wire 可能残留 "peek" 等非法档，不参与裁决。
  const legal = input.supported?.filter((s): s is SurfaceMode => s in MODE_RANK) ?? [];
  const asc = legal.length ? [...legal].sort((a, b) => MODE_RANK[a] - MODE_RANK[b]) : null;

  let target: Presentation = input.suggested ?? "stage";

  // 面板声明的支持范围：按「请求档位的真实所需」校验，不满足则优先向下回落；
  // 没有更低档就取它支持的最低档
  if (asc) {
    const req = REQUIRES[target] ?? REQUIRES.stage; // wire 垃圾值兜底：按 stage 所需裁
    if (!asc.includes(req.mode)) {
      target = [...asc].reverse().find((s) => MODE_RANK[s] <= req.ceiling) ?? asc[0];
    }
  }

  // 自动上限必须在回落「之后」施加：回落可能把档位抬高（面板只支持 stage/focus 时），
  // 先施加会被绕过——那正是「结果一回来就跳页」的复发路径。
  if (!input.explicit && RANK[target] > RANK[AUTO_MAX]) {
    // peek 是 stage 的瞬态 compact 形态：面板支持 stage 才可瞬态预览；
    // 否则退 inline；连 inline 都不支持 → 不自动展开，只记账（用户可从活动轨点开）。
    // 真不适合紧凑预览的重面板应声明 min_width，由宿主按几何约束跳过，不靠枚举表达。
    if (!asc) target = "peek";
    else if (asc.includes("stage")) target = "peek";
    else if (asc.includes("inline")) target = "inline";
    else return { presentation: null, show: false };
  }

  // 不把用户正在用的工作面缩掉：新结果只升不降——但不得越过面板支持范围
  if (input.current && RANK[input.current] > RANK[target]) {
    const currentMode: SurfaceMode = input.current === "peek" ? "stage" : input.current;
    if (!asc || asc.includes(currentMode)) target = input.current;
  }

  return { presentation: target, show: true };
}
