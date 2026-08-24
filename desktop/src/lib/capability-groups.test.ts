import { describe, expect, it } from "vitest";
import { filterCapabilities, groupCapabilities, type LedgerSkill, type LedgerTool } from "./capability-groups";

const TOOLS: LedgerTool[] = [
  { id: "screenshot", source_type: "core" },
  { id: "use_skill", source_type: "core" },
  { id: "zimeiti.list", source_type: "plugin" },
  { id: "zimeiti.add", source_type: "plugin" },
  { id: "forge.list", source_type: "plugin" },
  { id: "mcp.github.pr", source_type: "mcp" },
];

const SKILLS: LedgerSkill[] = [
  { id: "skill:ppt", source_type: "skill", owner: null, name: "ppt", description: "做 PPT" },
  { id: "zimeiti:write", source_type: "plugin_skill", owner: "zimeiti", name: "write", description: "成文框架" },
  { id: "forge:triage", source_type: "plugin_skill", owner: "forge", name: "triage", description: "快筛方法论" },
];

const PLUGINS = [
  { id: "zimeiti", name: "自媒体" },
  { id: "forge", name: "需求磨刀" },
];

describe("groupCapabilities", () => {
  it("插件按 id 前缀聚合工具数；core/mcp 分桶", () => {
    const g = groupCapabilities(TOOLS, SKILLS, PLUGINS);
    expect(g.plugins).toEqual([
      { id: "zimeiti", name: "自媒体", toolCount: 2 },
      { id: "forge", name: "需求磨刀", toolCount: 1 },
    ]);
    expect(g.coreTools).toEqual(["screenshot", "use_skill"]);
    expect(g.mcpTools).toEqual(["mcp.github.pr"]);
  });

  it("技能按 owner 分组：独立置顶，包内归插件显示名（插件间按拼音序）", () => {
    const g = groupCapabilities(TOOLS, SKILLS, PLUGINS);
    expect(g.skillGroups.map((x) => x.ownerName)).toEqual(["独立技能", "需求磨刀", "自媒体"]);
    expect(g.skillGroups[0]!.skills[0]!.id).toBe("skill:ppt");
    expect(g.skillGroups[2]!.skills[0]!.id).toBe("zimeiti:write");
  });

  it("owner 查不到插件显示名时退回 id；空数据不炸", () => {
    const g = groupCapabilities([], [{ ...SKILLS[1]!, owner: "ghost" }], []);
    expect(g.skillGroups[0]!.ownerName).toBe("ghost");
    expect(groupCapabilities([], [], [])).toEqual({ plugins: [], skillGroups: [], coreTools: [], mcpTools: [] });
  });
});

describe("filterCapabilities", () => {
  const g = groupCapabilities(TOOLS, SKILLS, PLUGINS);

  it("空串原样返回", () => {
    expect(filterCapabilities(g, "  ")).toBe(g);
  });

  it("跨组过滤：技能描述/插件名/底座 id 都能命中", () => {
    const byDesc = filterCapabilities(g, "成文");
    expect(byDesc.skillGroups).toHaveLength(1);
    expect(byDesc.skillGroups[0]!.skills[0]!.id).toBe("zimeiti:write");
    expect(byDesc.plugins).toHaveLength(0);

    const byPlugin = filterCapabilities(g, "自媒体");
    expect(byPlugin.plugins).toHaveLength(1);
    expect(byPlugin.skillGroups.map((x) => x.ownerName)).toEqual(["自媒体"]); // owner 名命中

    const byCore = filterCapabilities(g, "screen");
    expect(byCore.coreTools).toEqual(["screenshot"]);
  });

  it("过滤后空组整段消失", () => {
    const r = filterCapabilities(g, "不存在的词");
    expect(r.skillGroups).toEqual([]);
    expect(r.plugins).toEqual([]);
  });
});
