"""IPC schema：译宝 shell ↔ 脑 的契约（Plan 2 的 Tauri 壳直接复用）。"""
from __future__ import annotations

import uuid
from enum import IntEnum
from typing import Literal

from pydantic import BaseModel, Field


class RiskLevel(IntEnum):
    L0_READONLY = 0
    L1_LOW = 1
    L2_MEDIUM = 2
    L3_HIGH = 3
    L4_CRITICAL = 4


class Action(BaseModel):
    id: str = Field(default_factory=lambda: _new_id("act"))
    skill_id: str
    params: dict = Field(default_factory=dict)
    description: str = ""
    risk: RiskLevel = RiskLevel.L1_LOW
    label: str = ""  # 过程展示短标签（invoker 从 skill.label 填，回退 skill_id）


class ActionResult(BaseModel):
    success: bool
    data: dict = Field(default_factory=dict)
    error: str = ""
    screenshot_path: str | None = None
    panel: str | None = None  # 面板引用「plugin_id:name」：执行成功时带上，壳侧渲染对应面板


EventKind = Literal[
    "thought",
    "action_proposed",
    "confirmation_needed",
    "action_result",
    "final_reply",
    "final_reply_chunk",
    "interrupted",
    "error",
    "listening",
    "listening_done",
    "speaking",
    "speaking_done",
    "panel",
    "reminder",  # 主动提醒触发（server 调度循环发出）
    "notice",  # 轻提示（插件展开等，§12-2 要知情；前端居中淡色小字）
    "run_metrics",  # 一次 run 结束的 token/费用/耗时统计（final_reply 后发出）
]


class RunMetrics(BaseModel):
    """一次 run 的统计（kind="run_metrics" 时 payload 放它）。

    usage 为整轮所有 LLM 调用的累加（工具轮多次调用合并）；cost 按模型定价计算（元）。
    elapsed 为整轮耗时（秒，含工具调用）。model 为本次使用的模型名。
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0
    cost: float | None = None  # 元；未知模型/计费不可靠为 None
    elapsed_ms: int = 0
    model: str = ""


class Event(BaseModel):
    kind: EventKind
    text: str = ""
    action: Action | None = None
    # Task 2：confirmation_needed 攒批载荷——一轮多 CONFIRM 的全部 action。
    # 旧前端仍读 action（= actions[0]）；Task 4/5 切 actions 后保留 action 作过渡兼容。
    actions: list[Action] | None = None
    result: ActionResult | None = None
    confirmation_id: str | None = None
    payload: dict = Field(default_factory=dict)  # kind="panel" 时放 {panel, schema, data}
    metrics: RunMetrics | None = None  # kind="run_metrics" 时携带统计


def _new_id(prefix: str) -> str:
    """全局唯一 id：带随机段，sidecar 重启后也不会与 audit.db 旧记录冲突。"""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"
