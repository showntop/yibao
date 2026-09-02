// Workspace / Mission 卡的读模型适配器。
// 新数据优先读取持久 WorkflowRun；旧 Project 数据才回退声明式 Workflow Pack
// 投影。组件只消费通用阶段合同，不知道视频、PPT 等领域步骤。
import type { DurableExecutionInfo, ProjectInfo, ProjectObject } from "../brain";

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
  stageStates: readonly string[];
  stage: number;
  stageLabel: string;
  activeLabels: readonly string[];
  nextStep: string;
  /** 能力缺口（capability preflight）：run 被 enforce 策略阻断时为真 */
  capabilityBlocked: boolean;
  /** 缺口人话原因（run.blocked_reason） */
  capabilityReason: string;
  /** 缺能力 provider 的 stage 标签（按 plan.missing 顺序） */
  missingStageLabels: readonly string[];
  /** 可做到哪：plan 中连续 available 前缀的末段标签；首段即缺/无缺口时为空 */
  availableThrough: string;
  artifactCount: number;
  pending: number;
  executionLabel: string;
  executionProgress: number | null;
  executionStatus: string;
  executionId: string;
  executionCanCancel: boolean;
  executionCanResume: boolean;
}

export function projectCardFace(project: ProjectInfo): ProjectCardFace {
  const objects = project.objects ?? [];
  const pack = workflowPackFor(project);
  const run = project.workflow_run;
  const stage = run
    ? Math.min(pack.stages.length - 1, Math.max(0, run.current_stage_index))
    : workflowStageIndex(pack, objects);
  const activeLabels = run
    ? (run.active_stage_ids ?? [run.current_stage_id])
      .map((id) => run.stages.find((item) => item.id === id)?.label)
      .filter((label): label is string => Boolean(label))
    : [pack.stages[stage] ?? "理解"];
  const blockedCount = run?.stages.filter((item) => item.status === "blocked").length ?? 0;
  const activeExecutions = run?.stages
    .filter((item) => (run.active_stage_ids ?? [run.current_stage_id]).includes(item.id))
    .map((item) => item.execution)
    .filter((execution): execution is DurableExecutionInfo => Boolean(execution)) ?? [];
  const execution = activeExecutions.find((item) =>
    ["queued", "running", "resuming", "checkpointing", "cancel_requested", "interrupted", "failed"].includes(item.status),
  ) ?? activeExecutions[0];
  const executionProgress = execution ? Math.round(Math.max(0, Math.min(1, execution.progress)) * 100) : null;
  const executionLabel = activeExecutions.length > 1
    ? `${activeExecutions.length} 条任务执行中`
    : execution
      ? execution.status === "interrupted"
        ? `可恢复 · ${executionProgress}%`
        : execution.status === "failed"
          ? "执行失败"
          : execution.status === "cancel_requested"
            ? "正在安全停止"
            : execution.status === "completed"
              ? "阶段执行完成"
              : `${execution.status === "resuming" ? "续跑" : "执行"} · ${executionProgress}%${execution.provider_id ? ` · ${execution.provider_id}` : ""}`
      : "";
  // 能力预检缺口：run 被阻断 + enforce 策略 + 确有缺失才上卡面；info（mission.general）只转述
  const plan = run?.capability_plan ?? null;
  const capabilityBlocked = Boolean(
    run?.status === "blocked" && plan && plan.policy !== "info" && plan.missing.length > 0,
  );
  const missingStageLabels = capabilityBlocked && plan
    ? plan.missing.map((id) => plan.stages.find((item) => item.id === id)?.label ?? id)
    : [];
  let availableThrough = "";
  if (capabilityBlocked && plan) {
    for (const item of plan.stages) {
      if (item.status !== "available") break;
      availableThrough = item.label;
    }
  }
  return {
    name: project.name,
    missionTitle: project.mission?.title || project.name,
    domain: pack.domain,
    packId: pack.id,
    packLabel: pack.label,
    stages: pack.stages,
    stageStates: run?.stages.map((item) => item.status)
      ?? pack.stages.map((_label, index) => index < stage ? "completed" : index === stage ? "running" : "pending"),
    stage,
    stageLabel: activeLabels.join(" · ") || pack.stages[stage] || "理解",
    activeLabels,
    nextStep: capabilityBlocked
      ? availableThrough ? `缺能力 · 可做到${availableThrough}` : "缺能力 · 待装 provider"
      : blockedCount > 0
        ? `${blockedCount} 个节点等待依赖`
        : activeLabels.length > 1
          ? `${activeLabels.length} 条路径可并行`
          : pack.stages[stage + 1] ? `接续 · ${pack.stages[stage + 1]}` : "",
    capabilityBlocked,
    capabilityReason: capabilityBlocked ? run?.blocked_reason ?? "" : "",
    missingStageLabels,
    availableThrough,
    artifactCount: objects.length,
    pending: blockedCount || (run?.status === "waiting_user" ? 1 : 0),
    executionLabel,
    executionProgress,
    executionStatus: execution?.status ?? "",
    executionId: execution?.id ?? "",
    executionCanCancel: Boolean(
      execution
      && execution.cancel_mode !== "unsupported"
      && ["queued", "running", "resuming", "checkpointing", "cancel_requested", "interrupted"].includes(execution.status),
    ),
    executionCanResume: execution?.status === "interrupted",
  };
}
