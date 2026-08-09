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
        # 会话内免确认集合：用户勾选「本会话不再询问」并批准后由确认链路写入；
        # 只活在内存（重启即失效）。命中即 AUTO，连 confirmation_needed 事件都不发。
        self.session_allowed: set[str] = set()
        # 参数敏感技能的精确动作指纹；同样只在内存中存在，不保存原始参数。
        self.session_allowed_actions: set[str] = set()

    def decide(self, action: Action) -> Decision:
        if action.skill_id in self.session_allowed:
            return Decision.AUTO
        r = action.risk
        if r <= self.policy.auto_below_or_equal:
            return Decision.AUTO
        if r == RiskLevel.L4_CRITICAL and not self.policy.allow_critical:
            return Decision.DENY
        if r <= self.policy.confirm_below_or_equal:
            return Decision.CONFIRM
        return Decision.DENY
