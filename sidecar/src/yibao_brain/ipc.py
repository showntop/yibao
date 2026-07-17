"""IPC schema：译宝 shell ↔ 脑 的契约（Plan 2 的 Tauri 壳直接复用）。"""
from __future__ import annotations

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
]


class Event(BaseModel):
    kind: EventKind
    text: str = ""
    action: Action | None = None
    result: ActionResult | None = None
    confirmation_id: str | None = None


_id_counter = 0


def _new_id(prefix: str) -> str:
    global _id_counter
    _id_counter += 1
    return f"{prefix}_{_id_counter}"
