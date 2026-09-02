"""项目 tool（视频 workflow V1a）：项目实体向 agent 暴露的一等接口，与表面层 tool 平级注册。

分级口径：设计稿的「立项 = L2 按印」落到代码是 RiskLevel.L3_HIGH
（GatePolicy 默认 ≤L2 自动执行，≥L3 才弹确认条）——按印语义由确认链路保证。
"""
from __future__ import annotations

from .ipc import ActionResult
from .projects import ProjectStore
from .tools.core import RiskLevel, Tool


def _capability_summary(project: dict | None) -> dict | None:
    """立项回执的能力摘要（§4.2 preflight）：能完成哪些阶段、缺哪些、一句降级建议。

    让模型在立项时就能把缺口告知用户，而不是流程末端才发现。info 策略的流程
    （mission.general）enforced=False 且不给降级建议——没被阻断，谈不上「只能做到哪」。
    """
    run = (project or {}).get("workflow_run") or {}
    plan = run.get("capability_plan")
    if not isinstance(plan, dict):
        return None
    stages = plan.get("stages") or []
    available = [str(stage["label"]) for stage in stages if stage.get("status") == "available"]
    missing = [str(stage["label"]) for stage in stages if stage.get("status") == "missing"]
    enforced = str(plan.get("policy") or "") == "enforce"
    degradation = ""
    if enforced and missing:
        if available:
            degradation = f"可做到{available[-1]}；{missing[0]}起缺能力，安装对应 provider 后可继续"
        else:
            degradation = f"{missing[0]}起缺能力，安装对应 provider 后可继续"
    return {
        "ready": bool(plan.get("ready")),
        "enforced": enforced,
        "available_stages": available,
        "missing_stages": missing,
        "blocked_reason": str(run.get("blocked_reason") or ""),
        "degradation": degradation,
    }


class _ProjectTool(Tool):
    """共体：拿 store + 变更回调（server 注入广播，测试注入收集器）。"""

    def __init__(self, store: ProjectStore, on_change=None) -> None:
        self.store = store
        self._on_change = on_change

    @staticmethod
    def _conversation_id(ctx) -> str:
        return str(((getattr(ctx, "meta", None) or {}).get("conversation_id")) or "")

    def _notify(self, conversation_id: str = "") -> None:
        if self._on_change is not None:
            try:
                self._on_change(conversation_id)
            except TypeError:
                self._on_change()  # 兼容旧测试/注入方的零参数 callback
            except Exception:
                pass  # 广播失败不拖垮 tool 主链路


class ProjectCreateTool(_ProjectTool):
    id = "project.create"
    label = "立项"
    description = (
        "创建项目实体：名字唯一，自动创建目录骨架（01_素材/02_工程/03_导出/04_文档）"
        "并切为当前项目。可选 objects 把已有对象（如选题）挂进来。"
        "这是阶段门动作，会先弹确认条给用户按印。"
    )
    default_risk = RiskLevel.L3_HIGH  # 设计稿「L2 按印」→ 代码 L3（≤L2 是自动执行）

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "项目名（唯一）"},
                    "mission": {"type": "string", "description": "本工作语境下要完成的目标；缺省与项目名相同"},
                    "objects": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string", "description": "对象类型，如 zimeiti.topic"},
                                "ref": {"type": "string", "description": "对象引用 id"},
                            },
                            "required": ["type", "ref"],
                        },
                        "description": "立项时挂载的对象引用（可选）",
                    },
                },
                "required": ["name"],
            },
        }

    def run(self, params: dict, ctx) -> ActionResult:
        conversation_id = self._conversation_id(ctx)
        try:
            proj = self.store.create(
                str(params.get("name") or ""),
                objects=params.get("objects") if isinstance(params.get("objects"), list) else None,
                conversation_id=conversation_id,
                mission_title=str(params.get("mission") or "").strip() or None,
            )
        except ValueError as e:
            return ActionResult(success=False, error=str(e))
        except OSError as e:
            return ActionResult(success=False, error=f"目录骨架创建失败：{e}")
        self._notify(conversation_id)
        data: dict = {"project": proj}
        capability = _capability_summary(proj)
        if capability is not None:
            data["capability"] = capability
        return ActionResult(success=True, data=data)


class ProjectOpenTool(_ProjectTool):
    id = "project.open"
    label = "切换项目"
    description = "按名字或 id 切换到某个项目（成为当前项目）。"
    default_risk = RiskLevel.L1_LOW

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "项目名或 id"}},
                "required": ["name"],
            },
        }

    def run(self, params: dict, ctx) -> ActionResult:
        conversation_id = self._conversation_id(ctx)
        key = str(params.get("name") or "").strip()
        proj = self.store.get(key) or self.store.find_by_name(key)
        if proj is None:
            return ActionResult(success=False, error=f"找不到项目：{key}")
        self.store.switch(proj["id"], conversation_id)
        self._notify(conversation_id)
        return ActionResult(success=True, data={"project": proj})


class ProjectCurrentTool(_ProjectTool):
    id = "project.current"
    label = "当前项目"
    description = "读当前项目（含对象引用清单）；没有当前项目时返回空。"
    default_risk = RiskLevel.L0_READONLY

    def openai_schema(self) -> dict:
        return {"name": self.id, "description": self.description,
                "parameters": {"type": "object", "properties": {}}}

    def run(self, params: dict, ctx) -> ActionResult:
        proj = self.store.current(self._conversation_id(ctx))
        if proj is None:
            return ActionResult(success=True, data={"project": None})
        return ActionResult(success=True, data={"project": proj})


class ProjectAttachTool(_ProjectTool):
    id = "project.attach"
    label = "挂载到项目"
    description = "把一个对象引用（type + ref）挂到项目（缺省当前项目）；重复挂载自动去重。"
    default_risk = RiskLevel.L1_LOW

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "description": "对象类型，如 zimeiti.topic / video.script"},
                    "ref": {"type": "string", "description": "对象引用 id"},
                    "project": {"type": "string", "description": "项目名或 id，缺省当前项目"},
                },
                "required": ["type", "ref"],
            },
        }

    def run(self, params: dict, ctx) -> ActionResult:
        conversation_id = self._conversation_id(ctx)
        key = str(params.get("project") or "").strip()
        proj = ((self.store.get(key) or self.store.find_by_name(key))
                if key else self.store.current(conversation_id))
        if proj is None:
            return ActionResult(success=False, error="没有当前项目（先 project.create 或 project.open）" if not key else f"找不到项目：{key}")
        ok = self.store.add_object(proj["id"], str(params.get("type") or ""), str(params.get("ref") or ""))
        if ok:
            self._notify(conversation_id)
        return ActionResult(success=ok, data={"project_id": proj["id"]})


def make_project_tools(store: ProjectStore, on_change=None) -> list[Tool]:
    """项目工具集：server 启动时与 surface tools 同样以伪插件身份注册。"""
    return [
        ProjectCreateTool(store, on_change),
        ProjectOpenTool(store, on_change),
        ProjectCurrentTool(store, on_change),
        ProjectAttachTool(store, on_change),
    ]
