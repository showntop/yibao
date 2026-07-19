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


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

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

    def get(self, skill_id: str) -> Skill:
        return self._skills[skill_id]

    def list(self) -> list[Skill]:
        return list(self._skills.values())

    def openai_tools(self) -> list[dict]:
        out = []
        for s in self._skills.values():
            schema = s.openai_schema()
            schema["name"] = self.llm_name(s.id)  # LLM 只见安全名；回调经 resolve_llm_name 映射回
            out.append(schema)
        return out
