// 能力页分组纯函数（单一事实源，组件不手写聚合）：tool_ledger 的 tools/skills
// + listPlugins → 插件（带工具数）/ 技能（按 owner 分组）/ 底座·MCP 三段。

export interface LedgerTool {
  id: string;
  source_type: string; // core | plugin | mcp
}

export interface LedgerSkill {
  id: string; // skill:xxx（独立）或 <pid>:xxx（插件包内）
  source_type: "skill" | "plugin_skill";
  owner: string | null;
  name: string;
  description: string;
}

export interface PluginLike {
  id: string;
  name: string;
  panels?: { name: string; label: string; open: string }[];
}

export interface CapabilityPlugin extends PluginLike {
  toolCount: number;
}

export interface SkillGroup {
  owner: string | null; // null = 独立技能
  ownerName: string; // 独立技能 | 插件显示名（查不到退回 owner id）
  skills: LedgerSkill[];
}

export interface CapabilityGroups {
  plugins: CapabilityPlugin[];
  skillGroups: SkillGroup[];
  coreTools: string[];
  mcpTools: string[];
}

export function groupCapabilities(
  tools: LedgerTool[],
  skills: LedgerSkill[],
  plugins: PluginLike[],
): CapabilityGroups {
  const countByPlugin = new Map<string, number>();
  const coreTools: string[] = [];
  const mcpTools: string[] = [];
  for (const t of tools) {
    if (t.source_type === "core") {
      coreTools.push(t.id);
    } else if (t.source_type === "mcp") {
      mcpTools.push(t.id);
    } else {
      const pid = t.id.split(".", 1)[0];
      countByPlugin.set(pid, (countByPlugin.get(pid) ?? 0) + 1);
    }
  }

  const nameOf = new Map(plugins.map((p) => [p.id, p.name]));
  const byOwner = new Map<string | null, LedgerSkill[]>();
  for (const s of skills) {
    const owner = s.source_type === "skill" ? null : (s.owner ?? s.id.split(":", 1)[0]);
    const list = byOwner.get(owner) ?? [];
    list.push(s);
    byOwner.set(owner, list);
  }
  const skillGroups: SkillGroup[] = [...byOwner.entries()]
    .map(([owner, list]) => ({
      owner,
      ownerName: owner === null ? "独立技能" : (nameOf.get(owner) ?? owner),
      skills: list,
    }))
    .sort((a, b) => {
      if (a.owner === null) return -1; // 独立技能置顶（用户显式安装的库）
      if (b.owner === null) return 1;
      return a.ownerName.localeCompare(b.ownerName, "zh");
    });

  return {
    plugins: plugins.map((p) => ({ ...p, toolCount: countByPlugin.get(p.id) ?? 0 })),
    skillGroups,
    coreTools,
    mcpTools,
  };
}

/** 跨组过滤：插件名/id、技能名/描述/id/owner、底座与 MCP 工具 id。空串原样返回。 */
export function filterCapabilities(groups: CapabilityGroups, query: string): CapabilityGroups {
  const q = query.trim().toLowerCase();
  if (!q) return groups;
  return {
    plugins: groups.plugins.filter(
      (p) => p.name.toLowerCase().includes(q) || p.id.toLowerCase().includes(q),
    ),
    skillGroups: groups.skillGroups
      .map((g) => ({
        ...g,
        skills: g.skills.filter(
          (s) =>
            s.name.toLowerCase().includes(q) ||
            s.description.toLowerCase().includes(q) ||
            s.id.toLowerCase().includes(q) ||
            g.ownerName.toLowerCase().includes(q),
        ),
      }))
      .filter((g) => g.skills.length > 0),
    coreTools: groups.coreTools.filter((id) => id.toLowerCase().includes(q)),
    mcpTools: groups.mcpTools.filter((id) => id.toLowerCase().includes(q)),
  };
}
