"""Plan 3a 真实原子技能：经 SkillContext.host 调感知/执行基座操作 macOS。

技能只做「编排」，原语（截图/查找/触发/点击/输入）由 host 提供。
click_control 仅走 AX 动作；a11y 找不到/不支持时返回失败并指向 computer_use 视觉兜底（不再坐标回退）。
"""
from __future__ import annotations

import json

from .grounding import SoMGrounding, _physical_scale
from .ipc import ActionResult, RiskLevel
from .skills import Skill, SkillContext, SkillRegistry


def _no_host() -> ActionResult:
    return ActionResult(success=False, error="无执行基座 host（ctx.host 为空）")


class ScreenshotSkill(Skill):
    id = "screenshot"
    label = "截屏看屏幕"
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
    label = "读取界面结构"
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
    label = "打开应用"
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
    label = "点击控件"
    description = "点击一个控件：按 role/title 查找并触发其动作（确定性）。找不到或不支持时返回失败，应改用 computer_use 视觉定位。"
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
        if role or title:
            handle = a11y.find(role, title)
            if handle is not None and a11y.press(handle):
                return ActionResult(success=True, data={"method": "ax", "target": title or role})
        # 不再盲坐标回退：a11y 找不到 → 导向 computer_use 视觉定位
        return ActionResult(
            success=False,
            error="无法用 a11y 定位该控件（自绘 UI 或无 title）。请改用 computer_use 视觉定位。",
        )


class TypeTextSkill(Skill):
    id = "type_text"
    label = "输入文字"
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
    """视觉兜底：截图 → SoM 叠编号 → GLM 选号/动作 → 解析执行，覆盖 a11y 力不能及的 UI。

    build_marks 渲染失败时回退旧 raw-bbox（next_action）。
    """

    id = "computer_use"
    label = "操作电脑"
    description = (
        "computer-use 视觉兜底：当 read_tree/click_control 因控件无 title 或 UI 自绘而失效时，"
        "用视觉模型看截图识别目标并点击/输入。慢、可能不准、高风险。"
    )
    default_risk = RiskLevel.L2_MEDIUM

    def __init__(self, client, max_steps: int = 1, som: SoMGrounding | None = None):
        self._client = client
        self._default_max_steps = max_steps
        self._som = som or SoMGrounding()

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "要完成的操作目标"},
                    "app": {"type": "string", "description": "目标应用或窗口名称，如 计算器、Safari"},
                    "max_steps": {
                        "type": "integer",
                        "default": 1,
                        "maximum": self._default_max_steps,
                        "description": "最多执行步数；单次批准最多执行 1 步",
                    },
                },
                "required": ["task", "app"],
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
        prefers_raw_bbox = bool(getattr(self._client, "prefers_raw_bbox", False))
        app = str(params.get("app", "")).strip()
        if prefers_raw_bbox and not app:
            return ActionResult(success=False, error="缺少目标应用名称，无法安全裁剪目标窗口")
        requested_steps = max(1, int(params.get("max_steps", self._default_max_steps)))
        max_steps = min(requested_steps, self._default_max_steps)
        cancel = ctx.meta.get("cancel")

        def cancelled() -> bool:
            return bool(cancel is not None and cancel.is_set())

        history: list[dict] = []
        done: list[dict] = []
        prev_hash: str | None = None
        for _ in range(max_steps):
            if cancelled():
                return ActionResult(success=False, error="操作已中断")
            origin = (0.0, 0.0)
            if prefers_raw_bbox:
                capture_window = getattr(ctx.host.screenshotter, "capture_window", None)
                captured = capture_window(app) if callable(capture_window) else None
                if not captured:
                    return ActionResult(
                        success=False,
                        error=f"找不到目标应用窗口：{app}；为避免误点，已停止操作",
                    )
                shot, origin, scale = captured
            else:
                shot = ctx.host.screenshotter.capture()
                scale = _physical_scale(shot)
            shot_hash = self._md5(shot)
            if shot_hash is not None and shot_hash == prev_hash:
                break  # 连续两帧无变化 → 停
            prev_hash = shot_hash
            if prefers_raw_bbox:
                action = self._raw_bbox_step(
                    shot, task, history, ctx.host, scale, origin, cancelled
                )  # 模型原生 grounding
            else:
                tree = ctx.host.a11y.frontmost_tree()
                marked, marks = self._som.build_marks(shot, tree, scale)
                if marked is None:
                    action = self._raw_bbox_step(shot, task, history, ctx.host, scale)  # 回退
                else:
                    action = self._client.choose_action(marked, task, len(marks), history)
                    if cancelled():
                        return ActionResult(success=False, error="操作已中断")
                    if action is None:
                        break  # 模型输出非法 → 停，防失控
                    if action.get("action") == "finish":
                        break
                    self._apply_marked(action, marks, ctx.host)
            if action and action.get("action") == "interrupted":
                return ActionResult(success=False, error="操作已中断")
            if action is not None and action.get("action") and action.get("action") != "finish":
                done.append(action)
                history.append({"role": "assistant", "content": json.dumps(action, ensure_ascii=False)})
        return ActionResult(success=True, data={"steps": len(done), "actions": done})

    def _apply_marked(self, action: dict, marks: list[dict], host) -> None:
        kind = action.get("action")
        if kind == "click":
            self._som.resolve(action.get("mark"), marks, host)
        elif kind == "type":
            host.input.type_text(str(action.get("text", "")))

    def _raw_bbox_step(
        self, shot, task, history, host, scale, origin=(0.0, 0.0), should_cancel=None
    ):
        """旧 raw-bbox 回退路径（build_marks 渲染失败时）。"""
        b64 = self._b64(shot)
        if b64 is None:
            return None
        action = self._client.next_action(b64, task, history)
        if should_cancel is not None and should_cancel():
            return {"action": "interrupted"}
        if not action or action.get("action") == "finish":
            return action
        self._execute(action, host, scale, origin)
        return action

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
    def _execute(action: dict, host, scale: float, origin=(0.0, 0.0)) -> None:
        kind = action.get("action")
        box = action.get("box") or []
        ox, oy = origin
        if kind == "click" and len(box) == 4:
            x1, y1, x2, y2 = (float(v) for v in box)
            host.input.click(ox + (x1 + x2) / 2 / scale, oy + (y1 + y2) / 2 / scale)
        elif kind == "type":
            host.input.type_text(str(action.get("text", "")))
        elif kind == "scroll":
            import pyautogui

            delta = int(action.get("delta", -3))
            if len(box) == 4:
                x1, y1, x2, y2 = (float(v) for v in box)
                pyautogui.scroll(
                    delta,
                    ox + (x1 + x2) / 2 / scale,
                    oy + (y1 + y2) / 2 / scale,
                )
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
