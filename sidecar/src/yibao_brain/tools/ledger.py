"""能力台账管理（P3 管理面操作闭环 + B/E 收尾）：卸载 / 停用 / 启用 / 状态 / 更新。

- 操作统一路由到 SourceManager（PluginManager / SkillManager / McpManager，management.py）；
- 停用/启用持久化到 SourceStore（disabled 状态跨重启保留）；
- privileged（如 coding）：不可卸载、不可停用；core：只读。
"""
from __future__ import annotations

from typing import Any

from ..ipc import ActionResult, RiskLevel
from .management import SourceManager, SourceRecord, SourceStore
from .core import Tool, ToolRegistry


class _LedgerBase(Tool):
    """管理工具公共：来源解析 + manager 路由 + 特权检查 + 状态持久化。"""

    def __init__(self, registry: ToolRegistry, active: set,
                 managers: dict[str, SourceManager] | None = None,
                 store: SourceStore | None = None) -> None:
        self._reg = registry
        self._active = active
        self._managers = managers or {}
        self._store = store

    def _classify(self, source: str) -> tuple[str, str]:
        if source.startswith("mcp."):
            return ("mcp", source)
        if source.startswith("skill:"):
            return ("skill", source)
        if source in self._reg.plugin_ids():
            return ("plugin", source)
        return ("core", source)

    def _manager(self, kind: str) -> SourceManager | None:
        return self._managers.get(kind)

    def _plugin_privileged(self, pid: str) -> bool:
        # 延迟 import：ledger 模块级不依赖 plugins，避免 tools/__init__→ledger→plugins 循环
        from ..plugins import get_plugin_summaries

        return bool(get_plugin_summaries().get(pid, {}).get("privileged", False))

    def _persist(self, source: str, status: str) -> None:
        """更新 SourceStore 中该来源记录的 status（disabled 跨重启保留）。"""
        if self._store is None:
            return
        records = self._store.load()
        rec = records.get(source)
        if rec is None:
            return
        rec["status"] = status
        self._store.save({k: SourceRecord.from_dict(v) for k, v in records.items()})


class ToolDisableTool(_LedgerBase):
    id = "tool_disable"
    label = "停用能力来源"
    default_risk = RiskLevel.L2_MEDIUM
    description = "停用一个能力来源（插件 / MCP 服务器 / 技能）：其工具不再暴露、不可展开。底座与特权插件不可停用。用 tool_enable 恢复。"

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string",
                               "description": "来源组 id：插件 id（coding）/ mcp.<server> / skill:<name>"},
                    },
                    "required": ["id"],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        source = str(params.get("id") or "").strip()
        kind, _ = self._classify(source)
        if kind == "core":
            return ActionResult(success=False, error=f"底座能力「{source}」不可停用")
        if kind == "plugin" and self._plugin_privileged(source):
            return ActionResult(success=False, error=f"特权插件「{source}」不可停用（管理面只读）")
        self._reg.disable_source(source)
        self._persist(source, "disabled")
        return ActionResult(success=True, data={"id": source, "disabled": True,
                                                "human": f"已停用 {kind}「{source}」（tool_enable 恢复）"})


class ToolEnableTool(_LedgerBase):
    id = "tool_enable"
    label = "启用能力来源"
    default_risk = RiskLevel.L2_MEDIUM
    description = "重新启用一个被停用的能力来源（tool_disable 的逆操作）。"

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "来源组 id（同 tool_disable）"},
                    },
                    "required": ["id"],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        source = str(params.get("id") or "").strip()
        self._reg.enable_source(source)
        self._persist(source, "active")
        kind, _ = self._classify(source)
        return ActionResult(success=True, data={"id": source, "disabled": False,
                                                "human": f"已启用 {kind}「{source}」"})


class ToolUninstallTool(_LedgerBase):
    id = "tool_uninstall"
    label = "卸载能力来源"
    default_risk = RiskLevel.L3_HIGH
    description = (
        "卸载一个能力来源：插件（删除 plugins/ 目录）、MCP 服务器（断开并删配置）、"
        "技能（删除技能目录）。底座与特权插件不可卸载。"
    )

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string",
                               "description": "来源组 id：插件 id（coding）/ mcp.<server> / skill:<name>"},
                    },
                    "required": ["id"],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        source = str(params.get("id") or "").strip()
        kind, rid = self._classify(source)
        if kind == "core":
            return ActionResult(success=False, error=f"底座能力「{source}」不可卸载")
        if kind == "plugin" and self._plugin_privileged(rid):
            return ActionResult(success=False, error=f"特权插件「{rid}」不可卸载（管理面只读）")
        mgr = self._manager(kind)
        if mgr is None:
            return ActionResult(success=False, error=f"来源类型「{kind}」无管理器")
        try:
            if kind == "mcp":
                removed = mgr.remove_server(rid[4:])  # McpManager.remove_server(name)
            else:
                removed = mgr.uninstall(rid)
        except Exception as e:
            return ActionResult(success=False, error=f"卸载失败：{e}")
        self._active.discard(source)
        self._reg.enable_source(source)
        self._persist(source, "active")
        return ActionResult(success=True, data={"id": source, "removed": removed,
                                                "human": f"已卸载 {kind}「{source}」（注销 {removed} 个工具）"})


class ToolStatusTool(_LedgerBase):
    id = "tool_status"
    label = "能力来源状态"
    default_risk = RiskLevel.L0_READONLY
    description = "查询一个能力来源的当前状态（active / disabled / missing / online）。"

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "来源组 id（同 tool_disable）"},
                    },
                    "required": ["id"],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        source = str(params.get("id") or "").strip()
        kind, rid = self._classify(source)
        mgr = self._manager(kind)
        status = "active" if mgr is None else mgr.status(rid)
        if kind == "core":
            status = "active"
        return ActionResult(success=True, data={"id": source, "source_type": kind, "status": status,
                                                "human": f"{source}：{status}"})


class ToolUpdateTool(_LedgerBase):
    id = "tool_update"
    label = "更新能力来源"
    default_risk = RiskLevel.L3_HIGH
    description = "更新一个能力来源（插件重新加载 / MCP 断开重连 / 技能重装覆盖）。"

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "来源组 id（同 tool_disable）"},
                    },
                    "required": ["id"],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        source = str(params.get("id") or "").strip()
        kind, rid = self._classify(source)
        if kind == "core":
            return ActionResult(success=False, error=f"底座能力「{source}」随代码发布，无需更新")
        if kind == "plugin" and self._plugin_privileged(rid):
            return ActionResult(success=False, error=f"特权插件「{rid}」更新需重启（privileged）")
        mgr = self._manager(kind)
        if mgr is None:
            return ActionResult(success=False, error=f"来源类型「{kind}」无管理器")
        try:
            detail = mgr.update(rid[4:] if kind == "mcp" else rid)
        except Exception as e:
            return ActionResult(success=False, error=f"更新失败：{e}")
        return ActionResult(success=True, data={"id": source,
                                                "human": f"已更新 {kind}「{source}」：{detail}"})
