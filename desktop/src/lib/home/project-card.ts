// 家态项目卡（specimen video-flow.html 场景 2）的纯逻辑：S0–S8 进度轨推导 + 待确认数。
// V1a：项目实体还没有阶段字段，按 objects 里的引用类型诚实推导，推不出一律落 S0；
// V1b 接阶段模型后替换 projectStageIndex / projectPendingCount，组件不用动。
import type { ProjectInfo, ProjectObject } from "../brain";

/** 视频创作九段（specimen video-flow.html 总览）：S0 选题 → S8 复盘。 */
export const PROJECT_STAGES = ["选题", "脚本", "分镜", "资产筹备", "出镜头", "粗剪", "精剪", "发布", "复盘"] as const;

/** 当前段下标（0..8）。证据只看 objects 引用类型（如 zimeiti.topic）：
 *  有脚本/文档类对象 → S1；其余（含只有选题类、空）一律落 S0。
 *  V1b 接阶段模型后替换此推导。 */
export function projectStageIndex(objects: readonly ProjectObject[]): number {
  const types = objects.map((o) => o.type.toLowerCase());
  if (types.some((t) => t.includes("script") || t.includes("doc"))) return 1;
  return 0;
}

/** 待确认数。V1a 无数据源，恒 0（不造假）；V1b 接项目确认队列后替换。 */
export function projectPendingCount(_objects: readonly ProjectObject[]): number {
  return 0;
}

/** 下一步标签：阶段梯上的下一段名（S0 → 脚本）；最后一段返回空串。 */
export function projectNextStep(stage: number): string {
  return PROJECT_STAGES[stage + 1] ?? "";
}

/** 触达时间行标签（项目行用；touched_at 是 Unix 秒）：今天/昨天/M月d日。 */
export function projectTouchLabel(touchedAt: number, now = new Date()): string {
  const d = new Date(touchedAt * 1000);
  const startOf = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((startOf(now) - startOf(d)) / 86_400_000);
  if (days <= 0) return "今天";
  if (days === 1) return "昨天";
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}

export interface ProjectCardFace {
  name: string;
  /** 当前段下标（0..8） */
  stage: number;
  /** 轨左标签：S{n} · {段名} */
  stageLabel: string;
  /** 轨右下一步标签（最后一段为空串） */
  nextStep: string;
  /** 待确认数（V1a 恒 0，接口留此） */
  pending: number;
}

/** 当前项目 → 卡面（HomeProject 只渲染，不推导）。 */
export function projectCardFace(project: ProjectInfo): ProjectCardFace {
  const objects = project.objects ?? [];
  const stage = projectStageIndex(objects);
  return {
    name: project.name,
    stage,
    stageLabel: `S${stage} · ${PROJECT_STAGES[stage]}`,
    nextStep: projectNextStep(stage),
    pending: projectPendingCount(objects),
  };
}
