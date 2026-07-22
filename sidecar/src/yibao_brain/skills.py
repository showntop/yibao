"""技能/动作抽象 + 注册表 + 一个 EchoSkill（真实技能在 Plan 3）。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from .host import Host
from .ipc import ActionResult, RiskLevel

if TYPE_CHECKING:  # 避免运行期循环 import（plugins.py 依赖本模块）
    from .memory import Memory
    from .plugindb import PluginDb


@dataclass
class SkillContext:
    """技能执行上下文：host 提供感知/执行基座，meta 放 per-call 杂项。

    插件技能由加载器按 manifest capabilities 注入 memory/http/llm/db/emit_panel，
    未声明的能力对应属性为 None（ctx 里根本没有）。
    """
    host: Host | None = None
    meta: dict = field(default_factory=dict)
    memory: Memory | None = None
    http: Any = None  # plugins.HttpClient（鸭子类型，避免循环依赖）
    llm: Any = None  # plugins.LlmChat
    db: PluginDb | None = None
    emit_panel: Callable[[dict], None] | None = None


class Skill(ABC):
    id: str = "base"
    description: str = ""
    default_risk: RiskLevel = RiskLevel.L1_LOW
    # 插件注入位：加载器按插件设置；底座技能保持 None/空集，行为不变
    plugin_ctx: SkillContext | None = None
    plugin_capabilities: frozenset = frozenset()
    # 声明式 refresh：执行成功后跟一次本插件只读 tool，面板拿刷新数据而非操作回执
    # （写操作 data 是回执 {"id":…} 不适合喂面板；None = 不刷新，面板直接用 result.data）
    refresh: str | None = None

    @abstractmethod
    def run(self, params: dict, ctx: SkillContext) -> ActionResult: ...

    def openai_schema(self) -> dict:
        """OpenAI function-calling 工具描述（子类按需覆盖 params 描述）。"""
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {"type": "object", "properties": {}, "required": []},
        }


class EchoSkill(Skill):
    id = "echo"
    description = "原样回显一段文本（占位技能，用于验证回路）。"

    def run(self, params: dict, ctx: SkillContext) -> ActionResult:
        return ActionResult(success=True, data={"echo": params.get("text", "")})


class UsePluginSkill(Skill):
    """路由式暴露（规格 §12-2）：插件 tool 默认隐藏，LLM 按需展开。

    active 是与 AgentLoop 共享的可变集合（激活即生效，下一步 LLM 调用就能看到新工具）；
    summaries 来自插件 manifest（id → {name, description}），写进描述让 LLM 知道有哪些插件。
    """

    id = "use_plugin"
    default_risk = RiskLevel.L0_READONLY

    def __init__(self, registry: "SkillRegistry", active: set, summaries: dict) -> None:
        self._reg = registry
        self._active = active
        self._summaries = summaries
        listing = "；".join(
            f"{pid}（{info.get('name', pid)}{'：' + info['description'] if info.get('description') else ''}）"
            for pid, info in summaries.items()
        )
        self.description = (
            "展开一个插件的能力（插件的工具默认隐藏以省上下文，展开后立即可用）。"
            "用户的请求需要某插件功能而工具列表里没有时，先调本工具再继续。"
            + (f"可用插件：{listing}" if listing else "当前没有已加载的插件。")
        )

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "plugin": {
                        "type": "string",
                        "enum": list(self._summaries) or ["(无插件)"],
                        "description": "插件 id",
                    }
                },
                "required": ["plugin"],
            },
        }

    def run(self, params: dict, ctx: SkillContext) -> ActionResult:
        pid = str(params.get("plugin", "")).strip()
        if pid not in self._summaries:
            return ActionResult(
                success=False,
                error=f"没有这个插件：{pid or '(空)'}（可用：{', '.join(self._summaries) or '无'}）",
            )
        name = self._summaries[pid].get("name", pid)
        if pid in self._active:
            return ActionResult(success=True, data={"plugin": pid, "already": True,
                                                    "human": f"「{name}」插件本来就是打开状态"})
        self._active.add(pid)
        tools = self._reg.plugin_tools().get(pid, [])
        return ActionResult(
            success=True,
            data={"plugin": pid, "already": False, "tools": tools,
                  "human": f"我打开了「{name}」插件，{len(tools)} 个能力可用了"},
        )


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}
        self._by_plugin: dict[str, list[str]] = {}  # 插件 id → 其 tool id 列表（路由暴露用）

    @staticmethod
    def llm_name(skill_id: str) -> str:
        """发给 LLM 的 function name：DeepSeek/OpenAI 要求 ^[a-zA-Z0-9_-]+$，
        插件 id 的点号（notes.keep）转成下划线（notes_keep）。"""
        return skill_id.replace(".", "_")

    def resolve_llm_name(self, name: str) -> str:
        """LLM 回调的安全名 → 真实 tool id。完全匹配优先（底座 id 可能本身带下划线，
        如 web_search——撞名时底座赢，插件侧撞名属插件作者命名失误）。"""
        if name in self._skills:
            return name
        for sid in self._skills:
            if self.llm_name(sid) == name:
                return sid
        return name

    def register(self, skill: Skill, plugin: str | None = None) -> None:
        """注册技能。命名空间强制（v2 方案 §3.2）：

        - plugin 不为 None：skill.id 必须是「<plugin>.<x>」，否则 ValueError；
        - plugin 为 None（底座注册）：id 不允许带点号（防伪装成插件 id）；
        - 两种情况都禁止覆盖已存在的 id。
        """
        if plugin is not None:
            prefix = f"{plugin}."
            if not skill.id.startswith(prefix) or skill.id == prefix:
                raise ValueError(f"插件 tool id 必须以「{prefix}」为前缀：{skill.id!r}")
        elif "." in skill.id:
            raise ValueError(f"底座技能 id 不允许带点号（防伪装成插件 id）：{skill.id!r}")
        if skill.id in self._skills:
            raise ValueError(f"技能 id 重复注册：{skill.id!r}")
        self._skills[skill.id] = skill
        if plugin is not None:
            self._by_plugin.setdefault(plugin, []).append(skill.id)

    def plugin_tools(self) -> dict[str, list[str]]:
        """插件 id → 其 tool id 列表（use_plugin 展开时告知 LLM 新可用能力）。"""
        return {pid: list(ids) for pid, ids in self._by_plugin.items()}

    def get(self, skill_id: str) -> Skill:
        return self._skills[skill_id]

    def list(self) -> list[Skill]:
        return list(self._skills.values())

    def openai_tools(self, active_plugins: set[str] | None = None) -> list[dict]:
        """LLM 工具清单。active_plugins 为 None 时全量（测试/兼容路径）；
        否则插件 tool 只暴露已激活插件的——底座技能（id 无点号）始终可见。"""
        out = []
        for s in self._skills.values():
            if active_plugins is not None and "." in s.id:
                pid = s.id.split(".", 1)[0]
                if pid not in active_plugins:
                    continue
            schema = s.openai_schema()
            safe = self.llm_name(s.id)  # LLM 只见安全名；回调经 resolve_llm_name 映射回
            if "function" in schema:
                # 嵌套 OpenAI 格式（code skill 自带）：名字在 function.name 里
                schema["function"]["name"] = safe
            else:
                schema["name"] = safe
            out.append(schema)
        return out
