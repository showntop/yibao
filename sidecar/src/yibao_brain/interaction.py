"""用户优先的前台交互租约。

视觉模型推理前记录最近一次系统输入；真正注入动作前再次采样。只要期间发生过
键鼠输入，或桌面尚未安静到阈值，就让出控制。平台层只需注入“距最近输入秒数”。
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class InputLease:
    last_input_at: float | None


class UserInputLeaseGuard:
    def __init__(
        self,
        seconds_since_input: Callable[[], float],
        *,
        clock: Callable[[], float] = time.monotonic,
        idle_seconds: float = 0.8,
        available: bool | Callable[[], bool] = True,
    ) -> None:
        self._seconds_since_input = seconds_since_input
        self._clock = clock
        self._idle_seconds = max(0.0, float(idle_seconds))
        self._available = available

    def _is_available(self) -> bool:
        try:
            return bool(self._available() if callable(self._available) else self._available)
        except Exception:
            return False

    def _sample(self) -> tuple[float, float] | None:
        if not self._is_available():
            return None
        try:
            age = float(self._seconds_since_input())
            now = float(self._clock())
        except Exception:
            return None
        if not math.isfinite(age) or age < 0:
            return None
        return now - age, age

    def checkpoint(self) -> InputLease:
        sample = self._sample()
        return InputLease(last_input_at=sample[0] if sample is not None else None)

    def permit(self, lease: InputLease) -> tuple[bool, str | None]:
        sample = self._sample()
        if lease.last_input_at is None or sample is None:
            return False, "未获得输入监控权限，已停止 AI 前台操作"
        last_input_at, age = sample
        # 两次系统时钟采样会有微小抖动；50ms 内视为同一输入事件。
        if last_input_at > lease.last_input_at + 0.05 or age < self._idle_seconds:
            return False, "检测到用户正在操作，AI 已让出控制"
        return True, None
