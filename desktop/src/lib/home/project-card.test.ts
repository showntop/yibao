import { describe, expect, it } from "vitest";
import {
  projectCardFace,
  projectTouchLabel,
  workflowPackFor,
  workflowStageIndex,
} from "./project-card.ts";
import type { ProjectInfo } from "../brain";

function project(partial: Partial<ProjectInfo> & Pick<ProjectInfo, "id" | "name">): ProjectInfo {
  return { created_at: 0, touched_at: 0, dir: "", objects: [], ...partial };
}

describe("Workflow Pack 兼容投影", () => {
  it("视频与 PPT 不共用硬编码阶段", () => {
    const video = workflowPackFor(project({ id: "v", name: "Agent 科普视频" }));
    const deck = workflowPackFor(project({ id: "d", name: "Agent 概念 PPT" }));
    expect(video.id).toBe("video.explainer");
    expect(video.stages).toContain("分镜");
    expect(deck.id).toBe("deck.presentation");
    expect(deck.stages).toContain("故事线");
    expect(deck.stages).not.toContain("分镜");
  });

  it("未知领域回落通用任务，不冒充视频", () => {
    const pack = workflowPackFor(project({ id: "g", name: "搬家安排" }));
    expect(pack.id).toBe("mission.general");
    expect(pack.stages).toEqual(["理解", "推进", "核验", "交付"]);
  });

  it("同一语义对象投影到各自 pack 的阶段", () => {
    const video = workflowPackFor(project({ id: "v", name: "视频" }));
    const deck = workflowPackFor(project({ id: "d", name: "PPT" }));
    expect(workflowStageIndex(video, [{ type: "video.script", ref: "s1" }])).toBeGreaterThan(0);
    expect(workflowStageIndex(deck, [{ type: "deck.slide", ref: "p1" }])).toBeGreaterThan(0);
  });
});

describe("projectCardFace", () => {
  it("暴露 pack、产物数和下一阶段给通用卡面", () => {
    const face = projectCardFace(project({
      id: "d",
      name: "季度复盘 PPT",
      objects: [{ type: "deck.storyline", ref: "s1" }, { type: "deck.slide", ref: "p1" }],
    }));
    expect(face.packLabel).toBe("演示文稿");
    expect(face.artifactCount).toBe(2);
    expect(face.stageLabel).toBeTruthy();
    expect(face.nextStep).toBeTruthy();
  });

  it("优先读取真实 WorkflowRun，不再按对象字符串猜当前阶段", () => {
    const face = projectCardFace(project({
      id: "d2",
      name: "季度复盘 PPT",
      objects: [{ type: "video.script", ref: "legacy-misleading" }],
      mission: { id: "m1", title: "周五前完成 12 页策略汇报", status: "active" },
      workflow_run: {
        id: "wr1",
        definition_id: "deck.presentation",
        definition_version: "1.0.0",
        domain: "deck",
        label: "演示文稿",
        status: "running",
        current_stage_id: "slides",
        current_stage_index: 3,
        updated_at: 0,
        stages: [
          { id: "brief", label: "需求", status: "completed" },
          { id: "claims", label: "主张", status: "completed" },
          { id: "storyline", label: "故事线", status: "completed" },
          { id: "slides", label: "页面", status: "running" },
          { id: "export", label: "导出", status: "pending" },
        ],
      },
    }));
    expect(face.packId).toBe("deck.presentation");
    expect(face.stageLabel).toBe("页面");
    expect(face.nextStep).toBe("接续 · 导出");
    expect(face.missionTitle).toBe("周五前完成 12 页策略汇报");
  });

  it("把 DAG 的并行与阻塞状态直接投影到卡面", () => {
    const face = projectCardFace(project({
      id: "dag",
      name: "Agent 策略 PPT",
      objects: [],
      workflow_run: {
        id: "wr-dag",
        definition_id: "deck.presentation",
        definition_version: "2.0.0",
        domain: "deck",
        label: "演示文稿",
        status: "running",
        current_stage_id: "slides",
        current_stage_index: 3,
        active_stage_ids: ["slides", "visual"],
        updated_at: 0,
        stages: [
          { id: "brief", label: "需求", status: "completed" },
          { id: "claims", label: "主张", status: "completed" },
          { id: "storyline", label: "故事线", status: "completed" },
          { id: "slides", label: "页面", status: "running" },
          { id: "visual", label: "视觉", status: "ready" },
          { id: "validate", label: "校验", status: "pending" },
          { id: "export", label: "导出", status: "blocked" },
        ],
      },
    }));
    expect(face.activeLabels).toEqual(["页面", "视觉"]);
    expect(face.stageLabel).toBe("页面 · 视觉");
    expect(face.nextStep).toBe("1 个节点等待依赖");
    expect(face.stageStates).toContain("blocked");
  });

  it("把长任务的 provider、断点进度与恢复态投影到卡面", () => {
    const face = projectCardFace(project({
      id: "durable",
      name: "Agent 概念科普视频",
      objects: [],
      workflow_run: {
        id: "wr-durable",
        definition_id: "video.explainer",
        definition_version: "1.1.0",
        domain: "video",
        label: "视频创作",
        status: "blocked",
        current_stage_id: "assets",
        current_stage_index: 4,
        active_stage_ids: ["assets"],
        updated_at: 0,
        stages: [
          { id: "topic", label: "选题", status: "completed" },
          { id: "evidence", label: "证据", status: "completed" },
          { id: "script", label: "脚本", status: "completed" },
          { id: "storyboard", label: "分镜", status: "completed" },
          {
            id: "assets",
            label: "素材",
            status: "blocked",
            execution: {
              id: "execution-1",
              capability_id: "media.generate",
              provider_id: "primary",
              provider_candidates: ["primary", "fallback"],
              status: "interrupted",
              progress: 0.42,
              attempt: 1,
              checkpoint_version: 3,
              cancel_mode: "checkpoint",
              resume_supported: true,
            },
          },
        ],
      },
    }));
    expect(face.executionLabel).toBe("可恢复 · 42%");
    expect(face.executionProgress).toBe(42);
    expect(face.executionStatus).toBe("interrupted");
    expect(face.executionCanResume).toBe(true);
    expect(face.executionCanCancel).toBe(true);
  });
});

describe("projectTouchLabel", () => {
  it("按本地日期给今天/昨天/月日", () => {
    const now = new Date(2026, 7, 31, 15, 0);
    const at = (d: Date) => d.getTime() / 1000;
    expect(projectTouchLabel(at(new Date(2026, 7, 31, 9, 12)), now)).toBe("今天");
    expect(projectTouchLabel(at(new Date(2026, 7, 30, 23, 59)), now)).toBe("昨天");
    expect(projectTouchLabel(at(new Date(2026, 7, 24, 10, 0)), now)).toBe("8月24日");
  });
});
