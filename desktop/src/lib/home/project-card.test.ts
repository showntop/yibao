import { describe, expect, it } from "vitest";
import {
  PROJECT_STAGES,
  projectCardFace,
  projectNextStep,
  projectPendingCount,
  projectStageIndex,
  projectTouchLabel,
} from "./project-card.ts";
import type { ProjectInfo } from "../brain";

function project(partial: Partial<ProjectInfo> & Pick<ProjectInfo, "id" | "name">): ProjectInfo {
  return {
    created_at: 0,
    touched_at: 0,
    dir: "",
    objects: [],
    ...partial,
  };
}

describe("projectStageIndex（V1a 诚实推导）", () => {
  it("无对象/只有选题类一律落 S0", () => {
    expect(projectStageIndex([])).toBe(0);
    expect(projectStageIndex([{ type: "zimeiti.topic", ref: "t1" }])).toBe(0);
  });

  it("脚本/文档类对象在场 → S1", () => {
    expect(projectStageIndex([{ type: "zimeiti.script", ref: "s1" }])).toBe(1);
    expect(projectStageIndex([{ type: "notes.doc", ref: "d1" }])).toBe(1);
    expect(projectStageIndex([{ type: "zimeiti.topic", ref: "t1" }, { type: "zimeiti.script", ref: "s1" }])).toBe(1);
  });
});

describe("projectPendingCount", () => {
  it("V1a 无数据源恒 0（不造假）", () => {
    expect(projectPendingCount([])).toBe(0);
    expect(projectPendingCount([{ type: "zimeiti.topic", ref: "t1" }])).toBe(0);
  });
});

describe("projectNextStep", () => {
  it("给阶段梯上的下一段名，最后一段为空串", () => {
    expect(projectNextStep(0)).toBe("脚本");
    expect(projectNextStep(1)).toBe("分镜");
    expect(projectNextStep(PROJECT_STAGES.length - 1)).toBe("");
  });
});

describe("projectTouchLabel", () => {
  it("按本地日期给 今天/昨天/M月d日", () => {
    const now = new Date(2026, 7, 31, 15, 0);
    const at = (d: Date) => d.getTime() / 1000;
    expect(projectTouchLabel(at(new Date(2026, 7, 31, 9, 12)), now)).toBe("今天");
    expect(projectTouchLabel(at(new Date(2026, 7, 30, 23, 59)), now)).toBe("昨天");
    expect(projectTouchLabel(at(new Date(2026, 7, 24, 10, 0)), now)).toBe("8月24日");
  });
});

describe("projectCardFace", () => {
  it("空项目落 S0，待确认 0", () => {
    const face = projectCardFace(project({ id: "p1", name: "译宝品牌短片" }));
    expect(face).toEqual({
      name: "译宝品牌短片",
      stage: 0,
      stageLabel: "S0 · 选题",
      nextStep: "脚本",
      pending: 0,
    });
  });

  it("有脚本文档类对象落 S1", () => {
    const face = projectCardFace(
      project({ id: "p1", name: "译宝品牌短片", objects: [{ type: "zimeiti.script", ref: "s1" }] }),
    );
    expect(face.stage).toBe(1);
    expect(face.stageLabel).toBe("S1 · 脚本");
    expect(face.nextStep).toBe("分镜");
  });
});
