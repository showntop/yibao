"""风险分级授权：分类器 + 闸门（纯策略逻辑）。"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from .ipc import Action, RiskLevel
from .skills import Skill


class Decision(str, Enum):
    AUTO = "auto"
    CONFIRM = "confirm"
    DENY = "deny"


class GatePolicy(BaseModel):
    auto_below_or_equal: RiskLevel = RiskLevel.L2_MEDIUM  # <= 该级自动执行
    confirm_below_or_equal: RiskLevel = RiskLevel.L4_CRITICAL  # <= 该级可经确认执行
    allow_critical: bool = True  # False 时 L4 直接 DENY


_DEFAULT_DANGEROUS = [
    "delete", "remove", "rm ", "format", "payment", "pay", "send message",
    "email", "install", "sudo", "chmod", "reg add", "defaults write",
]


class RiskClassifier:
    """风险 = max(skill 默认级, 关键词命中升级级)。"""

    def __init__(self, dangerous_keywords: list[str] | None = None, escalate_to: RiskLevel = RiskLevel.L3_HIGH):
        self.keywords = [k.lower() for k in (dangerous_keywords or _DEFAULT_DANGEROUS)]
        self.escalate_to = escalate_to

    def classify(self, action: Action, skill: Skill | None) -> RiskLevel:
        base = skill.default_risk if skill is not None else action.risk
        text = " ".join(str(v) for v in action.params.values()).lower()
        if any(k in text for k in self.keywords):
            return max(base, self.escalate_to)
        return base


class Gate:
    def __init__(self, policy: GatePolicy):
        self.policy = policy

    def decide(self, action: Action) -> Decision:
        r = action.risk
        if r <= self.policy.auto_below_or_equal:
            return Decision.AUTO
        if r == RiskLevel.L4_CRITICAL and not self.policy.allow_critical:
            return Decision.DENY
        if r <= self.policy.confirm_below_or_equal:
            return Decision.CONFIRM
        return Decision.DENY
