"""Plan 3a 真实原子技能：经 SkillContext.host 调感知/执行基座操作 macOS。

技能只做「编排」，原语（截图/查找/触发/点击/输入）由 host 提供。
click_control 仅走 AX 动作；a11y 找不到/不支持时返回失败并指向 computer_use 视觉兜底（不再坐标回退）。
"""
from __future__ import annotations

import json
import os
from .background_jobs import BackgroundJobManager
from .grounding import SoMGrounding, _physical_scale, zoom_ground
from .ipc import ActionResult, RiskLevel
from .skills import Skill, SkillContext, SkillRegistry


def _no_host() -> ActionResult:
    return ActionResult(success=False, error="无执行基座 host（ctx.host 为空）")


def _interaction_lease(ctx: SkillContext):
    guard = getattr(ctx.host, "user_input", None) if ctx.host is not None else None
    if guard is None:
        return None
    return guard, guard.checkpoint()


def _permit_interaction(ctx: SkillContext, lease) -> tuple[bool, str | None]:
    if lease is None:
        return True, None
    guard, token = lease
    try:
        allowed, reason = guard.permit(token)
    except Exception:
        allowed, reason = False, "无法确认用户输入状态，已停止 AI 前台操作"
    if not allowed:
        request_cancel = ctx.meta.get("request_cancel")
        if callable(request_cancel):
            request_cancel()
    return allowed, reason


class ScreenshotSkill(Skill):
    id = "screenshot"
    label = "截屏看屏幕"
    description = "截取当前主屏幕，保存为图片并返回路径；配置了视觉模型时附可见窗口描述。"
    default_risk = RiskLevel.L0_READONLY

    def __init__(self, describe=None) -> None:
        self._describe = describe  # callable(path)->str|None；None=无视觉（只回路径）

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
        data: dict = {"path": path}
        if self._describe is not None:
            try:
                desc = self._describe(path)
            except Exception:
                desc = None
            if desc:
                data["description"] = desc
        return ActionResult(success=True, data=data, screenshot_path=path)


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
        # open_app 只是启动/置前，不注入键鼠输入；不纳入「用户正在操作则让出」租约——
        # 否则任务一慢、用户动一下鼠标，连恢复动作（置前）都会被一起拦死。
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
        lease = _interaction_lease(ctx)
        role = params.get("role")
        title = params.get("title")
        if role or title:
            handle = a11y.find(role, title)
            if handle is not None:
                allowed, reason = _permit_interaction(ctx, lease)
                if not allowed:
                    return ActionResult(success=False, error=reason)
                if a11y.press(handle):
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
        allowed, reason = _permit_interaction(ctx, _interaction_lease(ctx))
        if not allowed:
            return ActionResult(success=False, error=reason)
        ctx.host.input.type_text(text)
        return ActionResult(success=True, data={"chars": len(text)})


class ComputerUseSkill(Skill):
    """视觉兜底：截图 → SoM 叠编号 → GLM 选号/动作 → 解析执行，覆盖 a11y 力不能及的 UI。

    build_marks 渲染失败时回退旧 raw-bbox（next_action）。
    """

    id = "computer_use"
    label = "操作电脑"
    description = (
        "视觉操作：看截图用视觉模型识别目标并点击/输入，适合无 AX 的自绘 UI 或 read_tree 找不到的控件。"
        "可一次连续完成多步（如一连串点击/输入），模型输出 finish 即停；每步调一次视觉大模型，"
        "标准控件用 click_control/type_text 更快更稳。"
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
                        "default": self._default_max_steps,
                        "maximum": self._default_max_steps,
                        "description": "一次调用最多连续执行步数；可一次完成多步，模型输出 finish 即停",
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
            interaction_lease = _interaction_lease(ctx)
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
                    shot, task, history, ctx.host, scale, origin, cancelled,
                    lambda: _permit_interaction(ctx, interaction_lease),
                )  # 模型原生 grounding
            else:
                tree = ctx.host.a11y.frontmost_tree()
                marked, marks, zones = self._som.build_marks(shot, tree, scale)
                if marked is None:
                    action = self._raw_bbox_step(shot, task, history, ctx.host, scale)  # 回退
                else:
                    action = self._client.choose_action(marked, task, len(marks), history,
                                                        n_zones=len(zones))
                    if cancelled():
                        return ActionResult(success=False, error="操作已中断")
                    if action is None:
                        break  # 模型输出非法 → 停，防失控
                    if action.get("action") == "finish":
                        break
                    allowed, reason = _permit_interaction(ctx, interaction_lease)
                    if not allowed:
                        action = {"action": "interrupted", "reason": reason}
                    else:
                        self._apply_marked(action, marks, ctx.host,
                                           zones=zones, shot=shot, scale=scale, task=task)
            if action and action.get("action") == "interrupted":
                return ActionResult(success=False, error=action.get("reason") or "操作已中断")
            if action is not None and action.get("action") and action.get("action") != "finish":
                done.append(action)
                history.append({"role": "assistant", "content": json.dumps(action, ensure_ascii=False)})
        return ActionResult(success=True, data={"steps": len(done), "actions": done})

    def _apply_marked(self, action, marks, host, *, zones=(), shot=None, scale=1.0, task=""):
        kind = action.get("action")
        if kind == "click":
            self._som.resolve(action.get("mark"), marks, host)
        elif kind == "type":
            host.input.type_text(str(action.get("text", "")))
        elif kind == "zoom" and shot is not None:
            zone = next((z for z in zones if z["letter"] == action.get("zone")), None)
            if zone is None:
                return
            point = zoom_ground(self._client, shot, zone["rect"], scale, task)
            if point is not None:
                self._som.resolve_point(point[0], point[1], host)

    def _raw_bbox_step(
        self, shot, task, history, host, scale, origin=(0.0, 0.0), should_cancel=None,
        before_interaction=None,
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
        if before_interaction is not None:
            allowed, reason = before_interaction()
            if not allowed:
                return {"action": "interrupted", "reason": reason}
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

    def _execute(self, action: dict, host, scale: float, origin=(0.0, 0.0)) -> None:
        kind = action.get("action")
        box = action.get("box") or []
        ox, oy = origin
        if kind == "click" and len(box) == 4:
            x1, y1, x2, y2 = (float(v) for v in box)
            x = ox + (x1 + x2) / 2 / scale
            y = oy + (y1 + y2) / 2 / scale
            # 原生 bbox 也先命中 AX 元素并触发动作，不移动用户可见鼠标。
            self._som.resolve(
                1,
                [{"id": 1, "source": "bbox", "center": (x, y), "rect": (x, y, x, y)}],
                host,
            )
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


def register_real_skills(
    reg: SkillRegistry, background_jobs: BackgroundJobManager | None = None,
    describe=None,
) -> BackgroundJobManager:
    """把真实原子技能注册到 registry。describe：截屏附视觉描述（无视觉 None）。"""
    jobs = background_jobs or BackgroundJobManager()
    for skill in (
        ScreenshotSkill(describe=describe),
        ReadTreeSkill(),
        OpenAppSkill(),
        ClickControlSkill(),
        TypeTextSkill(),
        WatchCommandSkill(jobs),
        WatchCommandStatusSkill(jobs),
        CancelWatchCommandSkill(jobs),
    ):
        reg.register(skill)
    reg.background_jobs = jobs
    return jobs


class WatchCommandSkill(Skill):
    """后台盯命令：后台跑 shell 命令，完成/失败经 ctx.emit_event 主动报告。不阻塞。"""
    id = "watch_command"
    label = "后台盯命令"
    description = (
        "后台运行一条 shell 命令（编译/下载/测试/长跑脚本），立即返回不阻塞；"
        "完成或失败时主动报告（退出码 + 末尾输出）。适合要等很久的任务。"
    )
    default_risk = RiskLevel.L3_HIGH  # 跑任意 shell → 走确认闸门
    allow_session_remember = False

    def session_remember_key(self, params: dict) -> dict | None:
        command = str(params.get("command", "")).strip()
        cwd = str(params.get("cwd", "")).strip()
        if not command or not cwd:
            return None
        return {
            "command": command,
            "cwd": os.path.abspath(os.path.expanduser(cwd)),
        }

    def __init__(self, jobs: BackgroundJobManager | None = None) -> None:
        self.jobs = jobs or BackgroundJobManager()

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要后台运行的 shell 命令"},
                    "cwd": {"type": "string", "description": "任务工作目录（必填，绝对路径优先）"},
                    "name": {"type": "string", "description": "任务别名（报告时显示，可选）"},
                    "timeout": {"type": "number", "description": "超时秒数，默认 600"},
                },
                "required": ["command", "cwd"],
            },
        }

    def run(self, params: dict, ctx: SkillContext) -> ActionResult:
        command = str(params.get("command", "")).strip()
        if not command:
            return ActionResult(success=False, error="缺少 command 参数")
        cwd = str(params.get("cwd", "")).strip()
        if not cwd:
            return ActionResult(success=False, error="缺少 cwd 参数")
        label = str(params.get("name", "")).strip() or command
        emit = getattr(ctx, "emit_event", None)
        try:
            data = self.jobs.start(
                command,
                cwd=cwd,
                name=label,
                timeout=float(params.get("timeout", 600)),
                emit=emit,
            )
        except (TypeError, ValueError, RuntimeError) as exc:
            return ActionResult(success=False, error=str(exc))
        return ActionResult(success=True, data={"started": command, "label": label, **data})


class WatchCommandStatusSkill(Skill):
    id = "watch_command_status"
    label = "查看后台任务"
    description = "查看一个后台命令的状态和末尾输出；不传 task_id 时列出最近任务。"
    default_risk = RiskLevel.L0_READONLY

    def __init__(self, jobs: BackgroundJobManager) -> None:
        self.jobs = jobs

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": [],
            },
        }

    def run(self, params: dict, ctx: SkillContext) -> ActionResult:
        task_id = str(params.get("task_id", "")).strip()
        if not task_id:
            return ActionResult(success=True, data={"tasks": self.jobs.list()})
        job = self.jobs.status(task_id)
        return (
            ActionResult(success=True, data=job)
            if job is not None
            else ActionResult(success=False, error=f"找不到后台任务：{task_id}")
        )


class CancelWatchCommandSkill(Skill):
    id = "cancel_watch_command"
    label = "取消后台任务"
    description = "取消指定 task_id 的后台命令，并终止它的整个进程组。"
    default_risk = RiskLevel.L2_MEDIUM

    def __init__(self, jobs: BackgroundJobManager) -> None:
        self.jobs = jobs

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
            },
        }

    def run(self, params: dict, ctx: SkillContext) -> ActionResult:
        task_id = str(params.get("task_id", "")).strip()
        if not task_id:
            return ActionResult(success=False, error="缺少 task_id 参数")
        if not self.jobs.cancel(task_id):
            return ActionResult(success=False, error=f"任务不存在或已结束：{task_id}")
        return ActionResult(success=True, data={"task_id": task_id, "status": "cancelling"})
