"""工具领域包（2026-08-23 目录重构）：按领域归拢工具实现。

- core.py       工具核心：Tool 基类 / ToolContext / ToolRegistry / 底座工具
                 （echo / use_plugin / use_skill / use_mcp / tool_list / capability_refresh）
- perception.py 本机感知与操作（截图/点按/打字/打开应用/ComputerUse/watch_command）
- composite.py  组合能力（web_search / find_file / extract_url / open_path / write_note）
- ledger.py     台账管理（tool_uninstall / disable / enable / status / update）
- mcp.py        MCP 适配（client / manager / use_mcp / mcp_* 管理）
- management.py 来源管理（SourceRecord / SourceStore / SourceManager / Plugin / Skill）
- skills_index.py 技能索引（扫描 / frontmatter / resolve / build_body，供 use_skill 与 skills 插件共享）

re-export 保持 `from yibao_brain.tools import X` 兼容（原 tools.py 单模块时代）。
"""
from .core import (
    CapabilityRefreshTool,
    EchoTool,
    Tool,
    ToolContext,
    ToolListTool,
    ToolRegistry,
    UsePluginTool,
    UseSkillTool,
)
from .composite import register_composite_tools
from .ledger import ToolDisableTool, ToolEnableTool, ToolStatusTool, ToolUninstallTool, ToolUpdateTool
from .management import PluginManager, SkillManager, SourceManager, SourceRecord, SourceStore
from .mcp import (
    McpAddTool,
    McpClient,
    McpConnectTool,
    McpDisconnectTool,
    McpListTool,
    McpManager,
    McpTool,
    UseMcpTool,
)
from .perception import register_core_tools
from .skills_index import build_body, frontmatter, index, refresh_index, resolve, scan, skills_root

__all__ = [
    "build_body",
    "CapabilityRefreshTool",
    "EchoTool",
    "frontmatter",
    "index",
    "McpAddTool",
    "McpClient",
    "McpConnectTool",
    "McpDisconnectTool",
    "McpListTool",
    "McpManager",
    "McpTool",
    "PluginManager",
    "refresh_index",
    "register_composite_tools",
    "register_core_tools",
    "resolve",
    "scan",
    "SkillManager",
    "skills_root",
    "SourceManager",
    "SourceRecord",
    "SourceStore",
    "Tool",
    "ToolContext",
    "ToolDisableTool",
    "ToolEnableTool",
    "ToolListTool",
    "ToolRegistry",
    "ToolStatusTool",
    "ToolUninstallTool",
    "ToolUpdateTool",
    "UseMcpTool",
    "UsePluginTool",
    "UseSkillTool",
]
