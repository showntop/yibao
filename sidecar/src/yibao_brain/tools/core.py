"""技能/动作抽象 + 注册表 + 一个 EchoTool（真实技能在 Plan 3）。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from ..host import Host
from ..ipc import ActionResult, RiskLevel

if TYPE_CHECKING:  # 避免运行期循环 import（plugins.py 依赖本模块）
    from ..blob_store import BlobStore
    from ..memory import Memory
    from .plugindb import PluginDb


@dataclass
class ToolContext:
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
    blobs: BlobStore | None = None
    emit_panel: Callable[[dict], None] | None = None
    # 插件后台线程发事件的通道（由底座注入；测试环境为 None 时插件应静默跳过）。
    # 事件形如 {"kind": "reminder", "text": …}——前端已支持亮窗+气泡+TTS。
    emit_event: Callable[[dict], None] | None = None
    reminders: Any = None  # reminders.ReminderStore（提醒管理插件与底座技能共享同一实例）
    durable: Any = None  # DurableExecutionEngine；插件需声明 durable capability


class Tool(ABC):
    id: str = "base"
    description: str = ""
    default_risk: RiskLevel = RiskLevel.L1_LOW
    # 插件注入位：加载器按插件设置；底座技能保持 None/空集，行为不变
    plugin_ctx: ToolContext | None = None
    plugin_capabilities: frozenset = frozenset()
    # 声明式 refresh：执行成功后跟一次本插件只读 tool，面板拿刷新数据而非操作回执
    # （写操作 data 是回执 {"id":…} 不适合喂面板；None = 不刷新，面板直接用 result.data）
    refresh: str | None = None
    # 过程展示短标签（如「运行沙箱脚本」）：action_proposed 事件带给前端气泡行；
    # 空则 invoker 回退用 skill id。description 是长路由文案，不能当标题用
    label = ""
    # 敏感工具可以给当前模型完整结果，但审计、壳事件与会话历史只能使用 safe_result。
    # 默认关闭，现有工具行为不变。
    sensitive_output: bool = False
    # Arbitrary or context-sensitive actions can require confirmation every time.
    allow_session_remember: bool = True
    # 成功结果写入 Work Graph 的声明式投影；由唯一 ToolInvoker 消费。
    # kind=artifact/evidence，字段通过 data.* / params.* 路径取值。
    work_outputs: tuple[dict, ...] = ()

    def session_remember_key(self, params: dict) -> dict | None:
        """Return a canonical parameter subset for exact-action session approval.

        Skills that disable broad skill-level remembering may override this to
        allow a narrowly scoped, in-memory approval for equivalent parameters.
        """
        return None

    @abstractmethod
    def run(self, params: dict, ctx: ToolContext) -> ActionResult: ...

    def precheck(self, params: dict) -> str | None:
        """执行前的本地启发式检查：返回人话原因则拦截（不执行、不弹审批），None 放行。

        用途：路由纠偏（如 dispatch_task 拦截一次性任务指路 code_exec）——比 LLM 自觉
        读 description 可靠，又比风险审批轻（不打断用户）。子类按需覆盖。
        """
        return None

    def safe_result(self, result: ActionResult) -> ActionResult:
        """返回可持久化、可暴露给壳侧的结果；普通工具默认原样返回。"""
        return result

    def post_reply_notice(self, result: ActionResult) -> str | None:
        """工具成功影响最终回答时，可在最终回复后给用户一个轻提示。"""
        return None

    def openai_schema(self) -> dict:
        """OpenAI function-calling 工具描述（子类按需覆盖 params 描述）。"""
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {"type": "object", "properties": {}, "required": []},
        }


class EchoTool(Tool):
    id = "echo"
    label = "回声测试"
    description = "原样回显一段文本（占位技能，用于验证回路）。"

    def run(self, params: dict, ctx: ToolContext) -> ActionResult:
        return ActionResult(success=True, data={"echo": params.get("text", "")})


class UsePluginTool(Tool):
    """路由式暴露（规格 §12-2）：插件 tool 默认隐藏，LLM 按需展开。

    active 是与 AgentLoop 共享的可变集合（激活即生效，下一步 LLM 调用就能看到新工具）；
    summaries 来自插件 manifest（id → {name, description}），写进描述让 LLM 知道有哪些插件。
    """

    id = "use_plugin"
    label = "展开插件"
    default_risk = RiskLevel.L0_READONLY

    def __init__(self, registry: "ToolRegistry", active: set, summaries: dict) -> None:
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

    def run(self, params: dict, ctx: ToolContext) -> ActionResult:
        pid = str(params.get("plugin", "")).strip()
        if pid not in self._summaries:
            return ActionResult(
                success=False,
                error=f"没有这个插件：{pid or '(空)'}（可用：{', '.join(self._summaries) or '无'}）",
            )
        if pid in self._reg.disabled_sources:  # 停用来源：拒绝展开
            return ActionResult(success=False, error=f"插件「{pid}」已被停用（先 tool_enable 再展开）")
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


class CapabilityRefreshTool(Tool):
    """能力热重载（P1 reload 地基）：增量扫描插件目录，新插件免重启生效。

    已注册插件跳过（load_plugins existing 参数），只加载新放入的目录；代码插件受
    importlib 缓存限制，重载仍建议重启（spec §E：清 sys.modules 后再 _import_file）。
    """
    id = "capability_refresh"
    label = "重新加载插件"
    default_risk = RiskLevel.L1_LOW

    def __init__(self, registry: "ToolRegistry", loader: "Callable[[set | None], dict]") -> None:
        self._reg = registry
        self._loader = loader  # callable(existing=None) -> dict[pid, status]
        self.description = (
            "增量重新扫描插件目录，把新放入的插件热加载进系统（已加载的不动，无需重启）。"
            "往 plugins/ 新增了插件目录后调用；代码类插件的改动仍建议重启（importlib 缓存）。"
        )

    def run(self, params: dict, ctx: ToolContext) -> ActionResult:
        try:
            results = self._loader(existing=self._reg.plugin_ids())
        except Exception as e:
            return ActionResult(success=False, error=f"插件重载失败：{e}")
        ok = sorted(pid for pid, st in results.items() if st == "ok")
        failed = {k: v for k, v in results.items() if v != "ok"}
        lines = []
        if ok:
            lines.append(f"新增插件：{', '.join(ok)}")
        if failed:
            lines.append("失败：" + "；".join(f"{k}（{v}）" for k, v in failed.items()))
        if not ok and not failed:
            lines.append("没有发现新插件（已加载的保持原样）")
        return ActionResult(success=True, data={"added": ok, "failed": failed, "human": "；".join(lines)})


class UseSkillTool(Tool):
    """展开技能说明书到主上下文（与 use_plugin 对称：use_plugin 展开工具集，use_skill 展开说明书）。

    返回 SKILL.md + references 全文（ActionResult.data.text），loop 作为 tool 结果回填
    messages → LLM 下一轮结合用户原任务，在工具循环里读说明、调 code_exec 等完成。
    """

    id = "use_skill"
    label = "展开技能说明书"
    default_risk = RiskLevel.L0_READONLY
    description = (
        "展开一个 SKILL.md 技能的操作说明书到对话上下文（SKILL.md + references 全文），"
        "按说明书完成用户任务（需要读写文件/联网/跑脚本时调 code_exec 沙箱等工具）。"
        "可用技能用 skills.list 查看；用户请求明显匹配某个技能（做 PPT/设计/写作/品牌等）时用本工具。"
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
                        "skill": {"type": "string",
                                  "description": "技能 id（skills.list 返回的 skill:xxx，也接受裸名）"},
                    },
                    "required": ["skill"],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        from . import skills_index

        name = str(params.get("skill") or "").strip()
        hit = skills_index.resolve(name)
        if hit is None:
            return ActionResult(
                success=False,
                error=f"没有这个技能：{name or '(空)'}（可用：{', '.join(skills_index.index()) or '无'}）",
            )
        key, entry = hit
        body = skills_index.build_body(entry)
        return ActionResult(
            success=True,
            data={"skill": key, "text": body,
                  "human": f"已展开技能「{entry['name']}」说明书，按其说明完成任务"},
        )


class ToolListTool(Tool):
    """能力台账（P3 管理面地基）：列出全部已注册 Tool，附来源形态/风险/展开态/特权标记。

    四形态归位：core（底座 id 无点号）、plugin（两段 id，含 skills 桥）、
    mcp（三段 id，mcp.<server>.<tool>）。skill 域不注册 Tool，另列 data.skills 段
    （独立技能 skill:* + 插件包内 <pid>:*，只读展示，展开用 use_skill）。
    """

    id = "tool_list"
    label = "列出能力台账"
    default_risk = RiskLevel.L0_READONLY

    def __init__(self, registry: "ToolRegistry", active: set, summaries: dict) -> None:
        self._reg = registry
        self._active = active
        self._summaries = summaries  # 插件摘要（含 privileged）

    def run(self, params: dict, ctx: ToolContext) -> ActionResult:
        rows: list[dict] = []
        for t in sorted(self._reg.list(), key=lambda x: x.id):
            sid = t.id
            if "." in sid:
                group = sid.rsplit(".", 1)[0]
                source_type = "mcp" if group.startswith("mcp.") else "plugin"
            else:
                group = None
                source_type = "core"
            row: dict = {
                "id": sid,
                "source_type": source_type,
                "risk": getattr(t.default_risk, "name", str(t.default_risk)),
                "expanded": (group in self._active) if group is not None else True,
                "disabled": group in self._reg.disabled_sources if group is not None else False,
            }
            if source_type == "plugin" and group is not None:
                info = self._summaries.get(group, {})
                row["privileged"] = bool(info.get("privileged", False))
            rows.append(row)
        by_type: dict[str, int] = {}
        for r in rows:
            by_type[r["source_type"]] = by_type.get(r["source_type"], 0) + 1
        # skill 域（不注册 Tool，单列一段）：独立技能 skill:* + 插件包内 <pid>:*
        from . import skills_index

        skill_rows = [
            {
                "id": sid,
                "source_type": "skill" if sid.startswith("skill:") else "plugin_skill",
                "owner": None if sid.startswith("skill:") else sid.split(":", 1)[0],
                "name": str(entry.get("name") or sid),
                "description": str(entry.get("description") or ""),
            }
            for sid, entry in sorted(skills_index.index().items())
        ]
        human = "；".join(f"{k} {v} 个" for k, v in sorted(by_type.items())) or "（空）"
        if skill_rows:
            human += f"；技能 {len(skill_rows)} 个"
        return ActionResult(success=True, data={"tools": rows, "skills": skill_rows, "human": f"能力台账：{human}"})


class ToolRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, Tool] = {}
        self._by_plugin: dict[str, list[str]] = {}  # 插件 id → 其 tool id 列表（路由暴露用）
        # 表面层伪插件（design §3）：always_visible 注册的来源组，不参与 use_plugin 折叠
        self._always_visible: set[str] = set()
        # 停用的来源组 id（插件 id / mcp.<server> / skill:<name>）：不暴露、不可展开
        self.disabled_sources: set[str] = set()

    def disable_source(self, source: str) -> None:
        self.disabled_sources.add(source)

    def enable_source(self, source: str) -> None:
        self.disabled_sources.discard(source)

    def source_disabled(self, source: str) -> bool:
        return source in self.disabled_sources

    @staticmethod
    def llm_name(tool_id: str) -> str:
        """发给 LLM 的 function name：DeepSeek/OpenAI 要求 ^[a-zA-Z0-9_-]+$，
        插件 id 的点号（notes.keep）转成下划线（notes_keep）。"""
        return tool_id.replace(".", "_")

    def resolve_llm_name(self, name: str) -> str:
        """LLM 回调的安全名 → 真实 tool id。完全匹配优先（底座 id 可能本身带下划线，
        如 web_search——撞名时底座赢，插件侧撞名属插件作者命名失误）。"""
        if name in self._skills:
            return name
        for sid in self._skills:
            if self.llm_name(sid) == name:
                return sid
        return name

    def register(self, skill: Tool, plugin: str | None = None, replace: bool = False,
                 always_visible: bool = False) -> None:
        """注册技能。命名空间强制（v2 方案 §3.2）：

        - plugin 不为 None：skill.id 必须是「<plugin>.<x>」，否则 ValueError；
        - plugin 为 None（底座注册）：id 不允许带点号（防伪装成插件 id）；
        - 默认禁止覆盖已存在的 id；replace=True（能力热重载场景，见 capability_refresh）
          允许同 id 覆盖：_by_plugin 位置跟随 plugin 归属。
        - always_visible=True（表面层伪插件，design §3）：不参与 use_plugin 折叠，
          LLM 始终可见——表面层是一等操作面，不属于任何单个插件。
        """
        if plugin is not None:
            prefix = f"{plugin}."
            if not skill.id.startswith(prefix) or skill.id == prefix:
                raise ValueError(f"插件 tool id 必须以「{prefix}」为前缀：{skill.id!r}")
        elif "." in skill.id:
            raise ValueError(f"底座技能 id 不允许带点号（防伪装成插件 id）：{skill.id!r}")
        if skill.id in self._skills:
            if not replace:
                raise ValueError(f"技能 id 重复注册：{skill.id!r}")
            old_plugin = next((p for p, ids in self._by_plugin.items() if skill.id in ids), None)
            self._skills[skill.id] = skill
            if old_plugin is not None and plugin is not None and old_plugin != plugin:
                self._by_plugin[old_plugin].remove(skill.id)
                if not self._by_plugin[old_plugin]:
                    del self._by_plugin[old_plugin]
                self._by_plugin.setdefault(plugin, []).append(skill.id)
            return
        self._skills[skill.id] = skill
        if plugin is not None:
            self._by_plugin.setdefault(plugin, []).append(skill.id)
            if always_visible:
                self._always_visible.add(plugin)

    def unregister(self, tool_id: str) -> None:
        """注销技能（能力热重载）：从注册表与插件归属表移除；不存在则静默。"""
        if tool_id not in self._skills:
            return
        del self._skills[tool_id]
        for p, ids in list(self._by_plugin.items()):
            if tool_id in ids:
                ids.remove(tool_id)
                if not ids:
                    del self._by_plugin[p]
                return

    def plugin_ids(self) -> set[str]:
        """已注册的插件 id 集合（增量重载时跳过已加载插件）。"""
        return set(self._by_plugin)

    def plugin_tools(self) -> dict[str, list[str]]:
        """插件 id → 其 tool id 列表（use_plugin 展开时告知 LLM 新可用能力）。"""
        return {pid: list(ids) for pid, ids in self._by_plugin.items()}

    def get(self, tool_id: str) -> Tool:
        return self._skills[tool_id]

    def list(self) -> list[Tool]:
        return list(self._skills.values())

    def openai_tools(self, active_plugins: set[str] | None = None) -> list[dict]:
        """LLM 工具清单。active_plugins 为 None 时全量（测试/兼容路径）；
        否则插件 tool 只暴露已激活插件的——底座技能（id 无点号）始终可见。"""
        out = []
        for s in self._skills.values():
            if active_plugins is not None and "." in s.id:
                # rsplit：插件 tool 两段（coding.send→coding）与 MCP 工具三段
                # （mcp.mock.hello→mcp.mock）都能取到正确的来源组 id
                pid = s.id.rsplit(".", 1)[0]
                if pid in self.disabled_sources:  # 停用来源：不暴露
                    continue
                if pid not in active_plugins and pid not in self._always_visible:
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
