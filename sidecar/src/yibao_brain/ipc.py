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
]


class Event(BaseModel):
    kind: EventKind
    text: str = ""
    action: Action | None = None
    result: ActionResult | None = None
    confirmation_id: str | None = None
    payload: dict = Field(default_factory=dict)  # kind="panel" 时放 {panel, schema, data}


def _new_id(prefix: str) -> str:
    """全局唯一 id：带随机段，sidecar 重启后也不会与 audit.db 旧记录冲突。"""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"
