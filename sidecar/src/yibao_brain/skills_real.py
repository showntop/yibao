"""Plan 3a 真实原子技能：经 SkillContext.host 调感知/执行基座操作 macOS。

技能只做「编排」，原语（截图/查找/触发/点击/输入）由 host 提供。
click_control 的三级回退（AX 动作 → 坐标）在这里体现。
"""
from __future__ import annotations

import json

from .ipc import ActionResult, RiskLevel
from .skills import Skill, SkillContext, SkillRegistry


def _no_host() -> ActionResult:
    return ActionResult(success=False, error="无执行基座 host（ctx.host 为空）")


class ScreenshotSkill(Skill):
    id = "screenshot"
    description = "截取当前主屏幕，保存为图片并返回路径。"
    default_risk = RiskLevel.L0_READONLY

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {"type": "object", "properties": {}, "required": []},
        }

    def run(self, params: dict, ctx: SkillContext) -> ActionResult:
        if ctx.host is None:
            return _no_host()
        path = ctx.host.screenshotter.capture()
        return ActionResult(success=True, data={"path": path}, screenshot_path=path)


class ReadTreeSkill(Skill):
    id = "read_tree"
    description = "读取前台应用的辅助功能(A11y)控件树（标题/角色/位置），了解屏幕上有哪些可交互控件。"
    default_risk = RiskLevel.L0_READONLY

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "max_depth": {"type": "integer", "default": 8, "description": "最大递归深度"}
                },
                "required": [],
            },
        }

    def run(self, params: dict, ctx: SkillContext) -> ActionResult:
        if ctx.host is None:
            return _no_host()
        depth = int(params.get("max_depth", 8))
        tree = ctx.host.a11y.frontmost_tree(max_depth=depth)
        return ActionResult(success=True, data={"tree": tree})


class OpenAppSkill(Skill):
    id = "open_app"
    description = "按名字打开一个应用，如 Calculator / Safari / TextEdit。"
    default_risk = RiskLevel.L1_LOW

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {"app": {"type": "string", "description": "应用名"}},
                "required": ["app"],
            },
        }

    def run(self, params: dict, ctx: SkillContext) -> ActionResult:
        if ctx.host is None:
            return _no_host()
        app = str(params.get("app", "")).strip()
        if not app:
            return ActionResult(success=False, error="缺少 app 参数")
        pid = ctx.host.a11y.launch_app(app)
        if pid is None:
            return ActionResult(success=False, error=f"无法打开应用：{app}")
        return ActionResult(success=True, data={"app": app, "pid": pid})


class ClickControlSkill(Skill):
    id = "click_control"
    description = "点击一个控件：优先按 role/title 查找并触发其动作（确定性），找不到或不支持则回退屏幕坐标 (x,y) 点击。"
    default_risk = RiskLevel.L1_LOW

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "role": {"type": "string", "description": "控件角色，如 AXButton"},
                    "title": {"type": "string", "description": "控件标题/文字，如 '等于' 或 'OK'"},
                    "x": {"type": "number", "description": "回退用屏幕坐标 x"},
                    "y": {"type": "number", "description": "回退用屏幕坐标 y"},
                },
                "required": [],
            },
        }

    def run(self, params: dict, ctx: SkillContext) -> ActionResult:
        if ctx.host is None:
            return _no_host()
        a11y = ctx.host.a11y
        role = params.get("role")
        title = params.get("title")
        # 1. role/title → 查控件 → AX 主动作（Press/Pick）
        if role or title:
            handle = a11y.find(role, title)
            if handle is not None and a11y.press(handle):
                return ActionResult(success=True, data={"method": "ax", "target": title or role})
        # 2. 回退坐标点击
        x, y = params.get("x"), params.get("y")
        if x is not None and y is not None:
            ctx.host.input.click(float(x), float(y))
            return ActionResult(success=True, data={"method": "coord", "x": float(x), "y": float(y)})
        return ActionResult(success=False, error="无法定位控件（需提供 role/title 或 x/y）")


class TypeTextSkill(Skill):
    id = "type_text"
    description = "向当前聚焦的文本控件输入文字（支持中文）。"
    default_risk = RiskLevel.L1_LOW

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "要输入的文字"}},
                "required": ["text"],
            },
        }

    def run(self, params: dict, ctx: SkillContext) -> ActionResult:
        if ctx.host is None:
            return _no_host()
        text = str(params.get("text", ""))
        if not text:
            return ActionResult(success=False, error="缺少 text 参数")
        ctx.host.input.type_text(text)
        return ActionResult(success=True, data={"chars": len(text)})


class ComputerUseSkill(Skill):
    """视觉兜底：截图 → GLM-4.6V → 动作 → 注入，覆盖 a11y 力不能及的 UI。"""

    id = "computer_use"
    description = (
        "computer-use 视觉兜底：当 read_tree/click_control 因控件无 title 或 UI 自绘而失效时，"
        "用视觉模型看截图识别目标并点击/输入。慢、可能不准、高风险。"
    )
    default_risk = RiskLevel.L2_MEDIUM

    def __init__(self, client, max_steps: int = 5):
        self._client = client
        self._default_max_steps = max_steps

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "要完成的操作目标"},
                    "max_steps": {"type": "integer", "default": 5, "description": "最多执行步数"},
                },
                "required": ["task"],
            },
        }

    def run(self, params: dict, ctx: SkillContext) -> ActionResult:
        if ctx.host is None:
            return _no_host()
        task = str(params.get("task", "")).strip()
        if not task:
            return ActionResult(success=False, error="缺少 task 参数")
        if self._client is None:
            return ActionResult(success=False, error="无 computer-use client")
        max_steps = int(params.get("max_steps", self._default_max_steps))
        history: list[dict] = []
        done: list[dict] = []
        prev_hash: str | None = None
        for _ in range(max_steps):
            shot = ctx.host.screenshotter.capture()
            shot_hash = self._md5(shot)
            if shot_hash is not None and shot_hash == prev_hash:
                break  # 连续两帧无变化 → 停
            prev_hash = shot_hash
            b64 = self._b64(shot)
            if b64 is None:
                break
            action = self._client.next_action(b64, task, history)
            if not action:
                break  # 模型输出非法/空 → 停，防失控
            if action.get("action") == "finish":
                break
            scale = self._scale(shot)
            self._execute(action, ctx.host, scale)
            done.append(action)
            history.append({"role": "assistant", "content": json.dumps(action, ensure_ascii=False)})
        return ActionResult(success=True, data={"steps": len(done), "actions": done})

    @staticmethod
    def _md5(path: str) -> str | None:
        try:
            import hashlib

            with open(path, "rb") as f:
                return hashlib.md5(f.read()).hexdigest()
        except Exception:
            return None

    @staticmethod
    def _b64(path: str) -> str | None:
        try:
            import base64

            with open(path, "rb") as f:
                return "data:image/png;base64," + base64.b64encode(f.read()).decode()
        except Exception:
            return None

    @staticmethod
    def _scale(shot_path: str) -> float:
        """截图像素物理宽 / pyautogui 逻辑宽（Retina≈2）。失败退化 1.0。"""
        try:
            from PIL import Image
            import pyautogui

            phys_w = Image.open(shot_path).width
            logical_w = pyautogui.size().width
            return phys_w / logical_w if logical_w else 1.0
        except Exception:
            return 1.0

    @staticmethod
    def _execute(action: dict, host, scale: float) -> None:
        kind = action.get("action")
        box = action.get("box") or []
        if kind == "click" and len(box) == 4:
            x1, y1, x2, y2 = (float(v) for v in box)
            host.input.click((x1 + x2) / 2 / scale, (y1 + y2) / 2 / scale)
        elif kind == "type":
            host.input.type_text(str(action.get("text", "")))
        elif kind == "scroll":
            import pyautogui

            delta = int(action.get("delta", -3))
            if len(box) == 4:
                x1, y1, x2, y2 = (float(v) for v in box)
                pyautogui.scroll(delta, (x1 + x2) / 2 / scale, (y1 + y2) / 2 / scale)
            else:
                pyautogui.scroll(delta)
        # finish / 未知动作 → 不执行


def register_real_skills(reg: SkillRegistry) -> None:
    """把 5 个真实原子技能注册到 registry。"""
    for skill in (
        ScreenshotSkill(),
        ReadTreeSkill(),
        OpenAppSkill(),
        ClickControlSkill(),
        TypeTextSkill(),
    ):
        reg.register(skill)
