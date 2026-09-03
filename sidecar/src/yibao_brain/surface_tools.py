"""表面层 tool（design §3）：表面层向 agent 暴露一等接口，与插件 tool 平级注册。

裁决器不变量在 sidecar 把守（§11.1 初步倾向：不变量 sidecar、呈现细节前端）：
- surface.open 只收 inline/peek（peek = 宿主瞬态预览 placement）——stage/focus 必须
  用户亲手（宿主 explicit 通路），tool 连参数都不收，参数越界直接拒；
- editor.* 写类命令「发射即回执」：人在器内 diff 卡裁决，agent 永不静默落稿
  （tool.run 同步，阻塞等器内回执会死锁事件循环——见 surface.py docstring）。
"""

from .ipc import ActionResult
from .surface import SurfaceBridge
from .tools.core import RiskLevel, Tool

DEFAULT_PANEL = "zimeiti:editor"  # 今天唯一的器；参数保留 panel 以备新器注册


def _pid(panel: str) -> str:
    return panel.split(":", 1)[0]


class SurfaceOpenTool(Tool):
    id = "surface.open"
    label = "打开表面"
    description = (
        "把一个面板表面摆上台面：presentation 只能是 inline（行内回执）或 peek（宿主瞬态"
        "预览气泡——用完即收，不是工作面档位）。适合「把看板展开给我看看」这类请求。"
        "stage/focus 是正式工作面，需要用户亲手操作，你不要请求。"
        "panel 用「插件:面板名」引用（如 zimeiti:editor）。"
    )
    default_risk = RiskLevel.L1_LOW

    def __init__(self, bridge: SurfaceBridge) -> None:
        self.bridge = bridge

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "panel": {"type": "string", "description": f"面板引用「插件:面板名」，缺省 {DEFAULT_PANEL}"},
                    "presentation": {"type": "string", "enum": ["inline", "peek"], "description": "呈现档位，缺省 peek"},
                },
            },
        }

    def run(self, params: dict, ctx) -> ActionResult:
        ref = str(params.get("panel") or DEFAULT_PANEL)
        pres = str(params.get("presentation") or "peek")
        if pres not in ("inline", "peek"):
            # 裁决器不变量：agent 侧永不发起 stage/focus（§2 检验清单第 4 条）
            return ActionResult(success=False, error="stage/focus 需要用户亲手操作，agent 只能 inline/peek")
        if ":" not in ref:
            return ActionResult(success=False, error=f"panel 须为「插件:面板名」：{ref}")
        # panel 通路交给 loop：panel_payload → kind:"panel" 事件 → 宿主裁决器再兜底 AUTO_MAX
        return ActionResult(success=True, data={"opened": ref}, panel=ref, presentation=pres, attention="suggest")


class SurfaceReadTool(Tool):
    id = "surface.read"
    label = "读表面状态"
    description = (
        "不截图读到器里此刻的内容：文档快照（打开/保存/输入停顿时全量，含未保存的"
        "手打内容）与用户当前选区（start/end/quote 锚点）。"
        "器没开或还没上报过时返回空——先 surface.open 或让用户打开。"
    )
    default_risk = RiskLevel.L0_READONLY

    def __init__(self, bridge: SurfaceBridge) -> None:
        self.bridge = bridge

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {"panel": {"type": "string", "description": f"面板引用，缺省 {DEFAULT_PANEL}"}},
            },
        }

    def run(self, params: dict, ctx) -> ActionResult:
        pid = _pid(str(params.get("panel") or DEFAULT_PANEL))
        doc = self.bridge.snapshot(pid, "zimeiti.doc_snapshot")
        sel = self.bridge.snapshot(pid, "zimeiti.selection_changed")
        if doc is None and sel is None:
            return ActionResult(success=False, error="器还没上报过状态（没打开或刚打开）：先 surface.open，稍后再读")
        return ActionResult(success=True, data={"doc": doc, "selection": sel})


class _EditorCmdTool(Tool):
    """editor.* 命令共体：发射即回执，写类由器内 diff 卡裁决。"""

    cmd = ""

    def __init__(self, bridge: SurfaceBridge) -> None:
        self.bridge = bridge

    def run(self, params: dict, ctx) -> ActionResult:
        panel = str(params.pop("panel", None) or DEFAULT_PANEL)
        r = self.bridge.dispatch(panel, self.cmd, params)
        if not r.get("ok"):
            return ActionResult(success=False, error=str(r.get("error")))
        return ActionResult(success=True, data=r)


class EditorRevealTool(_EditorCmdTool):
    id = "editor.reveal_anchor"
    label = "指到段落"
    description = "在编辑器里点亮一个锚点（滚动+选中），配「你说的这段」这类指代。锚点失效会如实报错。"
    default_risk = RiskLevel.L0_READONLY
    cmd = "editor.reveal_anchor"

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "start": {"type": "integer", "description": "锚点起始偏移"},
                    "end": {"type": "integer", "description": "锚点结束偏移"},
                    "quote": {"type": "string", "description": "原文引文（漂移校验/回找用，尽量带上）"},
                    "panel": {"type": "string", "description": f"面板引用，缺省 {DEFAULT_PANEL}"},
                },
                "required": ["quote"],
            },
        }


class EditorReplaceRangeTool(_EditorCmdTool):
    id = "editor.replace_range"
    label = "提议改写区间"
    description = (
        "对文中区间提出改写：器里会弹 diff 卡，用户点「接受修改」才落稿——你只是提案人，"
        "不是执行者。不要用它做用户没要求的全篇重写。"
    )
    default_risk = RiskLevel.L1_LOW
    cmd = "editor.replace_range"

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "start": {"type": "integer", "description": "区间起始偏移"},
                    "end": {"type": "integer", "description": "区间结束偏移"},
                    "text": {"type": "string", "description": "替换后的新文本"},
                    "quote": {"type": "string", "description": "原区间引文（漂移校验用）"},
                    "panel": {"type": "string", "description": f"面板引用，缺省 {DEFAULT_PANEL}"},
                },
                "required": ["start", "end", "text"],
            },
        }


class EditorInsertTextTool(_EditorCmdTool):
    id = "editor.insert_text"
    label = "提议插入文本"
    description = "在指定位置插入文本提案：同样走器内 diff 卡，用户接受才落稿。"
    default_risk = RiskLevel.L1_LOW
    cmd = "editor.insert_text"

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "at": {"type": "integer", "description": "插入位置偏移"},
                    "text": {"type": "string", "description": "要插入的文本"},
                    "panel": {"type": "string", "description": f"面板引用，缺省 {DEFAULT_PANEL}"},
                },
                "required": ["at", "text"],
            },
        }


class EditorSetSelectionTool(_EditorCmdTool):
    id = "editor.set_selection"
    label = "选中区间"
    description = "在编辑器里选中一个区间（无副作用）。"
    default_risk = RiskLevel.L0_READONLY
    cmd = "editor.set_selection"

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "start": {"type": "integer", "description": "起始偏移"},
                    "end": {"type": "integer", "description": "结束偏移"},
                    "panel": {"type": "string", "description": f"面板引用，缺省 {DEFAULT_PANEL}"},
                },
                "required": ["start", "end"],
            },
        }


def make_surface_tools(bridge: SurfaceBridge) -> list[Tool]:
    """底座表面工具集：server 启动时以伪插件身份注册（surface./editor. 前缀）。"""
    return [
        SurfaceOpenTool(bridge),
        SurfaceReadTool(bridge),
        EditorRevealTool(bridge),
        EditorReplaceRangeTool(bridge),
        EditorInsertTextTool(bridge),
        EditorSetSelectionTool(bridge),
    ]
