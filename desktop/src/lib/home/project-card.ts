// Workspace / Mission 卡的读模型适配器。
// 新数据优先读取持久 WorkflowRun；旧 Project 数据才回退声明式 Workflow Pack
// 投影。组件只消费通用阶段合同，不知道视频、PPT 等领域步骤。
import type { ProjectInfo, ProjectObject } from "../brain";

export type WorkflowDomain = "general" | "video" | "deck" | "code" | "data";

export interface WorkflowPackPreview {
  id: string;
  domain: WorkflowDomain;
  label: string;
  stages: readonly string[];
  matches: readonly string[];
}

export const WORKFLOW_PACKS: readonly WorkflowPackPreview[] = [
  {
    id: "video.explainer",
    domain: "video",
    label: "视频创作",
    stages: ["选题", "证据", "脚本", "分镜", "素材", "配音", "合成", "交付"],
    matches: ["video", "视频", "zimeiti", "storyboard", "voice", "timeline"],
  },
  {
    id: "deck.presentation",
    domain: "deck",
    label: "演示文稿",
    stages: ["需求", "主张", "故事线", "页面", "视觉", "校验", "导出"],
    matches: ["deck", "ppt", "演示", "幻灯", "presentation", "slide"],
  },
  {
    id: "code.change",
    domain: "code",
    label: "软件交付",
    stages: ["问题", "方案", "改动", "验证", "交付"],
    matches: ["code", "代码", "开发", "coding", "repo", "patch", "test"],
  },
  {
    id: "data.analysis",
    domain: "data",
    label: "数据分析",
    stages: ["问题", "数据", "质量", "分析", "洞察", "交付"],
    matches: ["data", "数据", "dataset", "query", "chart", "analysis"],
  },
  {
    id: "mission.general",
    domain: "general",
    label: "通用任务",
    stages: ["理解", "推进", "核验", "交付"],
    matches: [],
  },
] as const;

function haystack(project: Pick<ProjectInfo, "name" | "objects">): string {
  return [project.name, ...project.objects.map((o) => o.type)].join(" ").toLowerCase();
}

export function workflowPackFor(project: Pick<ProjectInfo, "name" | "objects" | "workflow_run">): WorkflowPackPreview {
  const run = project.workflow_run;
  if (run?.stages.length) {
    return {
      id: run.definition_id,
      domain: run.domain,
      label: run.label,
      stages: run.stages.map((stage) => stage.label),
      matches: [],
    };
  }
  const text = haystack(project);
  return WORKFLOW_PACKS.find((pack) => pack.matches.some((token) => text.includes(token)))
    ?? WORKFLOW_PACKS[WORKFLOW_PACKS.length - 1];
}

function evidenceScore(type: string): number {
  if (/render|export|release|published/.test(type)) return 99;
  if (/quality|review|test|validation/.test(type)) return 80;
  if (/timeline|composition|deck\.document|report/.test(type)) return 70;
  if (/visual|image|audio|voice|chart|slide/.test(type)) return 55;
  if (/storyboard|storyline|outline|plan|patch|query/.test(type)) return 40;
  if (/script|article|doc|analysis/.test(type)) return 28;
  if (/claim|evidence|material|research|dataset/.test(type)) return 16;
  if (/topic|brief|issue|question/.test(type)) return 4;
  return 0;
}

/** 兼容投影：按对象语义估算流程位置；不把领域阶段写死进组件。 */
export function workflowStageIndex(
  pack: WorkflowPackPreview,
  objects: readonly ProjectObject[],
): number {
  if (!objects.length) return 0;
  const score = Math.max(...objects.map((o) => evidenceScore(o.type.toLowerCase())));
  if (score >= 99) return pack.stages.length - 1;
  return Math.min(pack.stages.length - 2, Math.max(0, Math.floor(score / 14)));
}

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
  missionTitle: string;
  domain: WorkflowDomain;
  packId: string;
  packLabel: string;
  stages: readonly string[];
  stage: number;
  stageLabel: string;
  nextStep: string;
  artifactCount: number;
  pending: number;
}

export function projectCardFace(project: ProjectInfo): ProjectCardFace {
  const objects = project.objects ?? [];
  const pack = workflowPackFor(project);
  const run = project.workflow_run;
  const stage = run
    ? Math.min(pack.stages.length - 1, Math.max(0, run.current_stage_index))
    : workflowStageIndex(pack, objects);
  return {
    name: project.name,
    missionTitle: project.mission?.title || project.name,
    domain: pack.domain,
    packId: pack.id,
    packLabel: pack.label,
    stages: pack.stages,
    stage,
    stageLabel: pack.stages[stage] ?? "理解",
    nextStep: pack.stages[stage + 1] ?? "",
    artifactCount: objects.length,
    pending: run?.status === "waiting_user" || run?.status === "blocked" ? 1 : 0,
  };
}
