"""watch mode：周期观察 + 可插拔主动行为（slice 1）。

行为只读快照、返回主动事件（或 None）；事件由 server 的 watch 循环经
_gate_proactive_event（proactive.level）出口。本期含健康节律（久坐提醒，纯定时）
+ 在场陪伴（占位，不出声）。无 LLM、无屏幕上云。spec：
docs/superpowers/specs/2026-07-31-watch-mode-core-design.md
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class WatchSnapshot:
    """一次 tick 的感知快照。activity=None 表示无活动数据。"""
    now: float
    app: str | None = None
    activity: dict | None = None  # {"state":"active"|"idle","seconds":float,"segment_id":...}


@dataclass
class WatchCtx:
    """行为 tick 依赖。settings=当前设置（行为读 watch.*；出口 gating 由循环做）。"""
    settings: dict = field(default_factory=dict)


class WatchBehavior(Protocol):
    name: str

    def tick(self, snapshot: WatchSnapshot, ctx: WatchCtx) -> dict | None: ...


def in_quiet_hours(now: float, quiet_hours: str) -> bool:
    """now（time.time()）是否落在 quiet_hours（"HH:MM-HH:MM"，空串/非法=关）。支持跨午夜。用本地时间。"""
    spec = (quiet_hours or "").strip()
    if not spec or "-" not in spec:
        return False
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*", spec)
    if not m:
        return False
    start_min = int(m.group(1)) * 60 + int(m.group(2))
    end_min = int(m.group(3)) * 60 + int(m.group(4))
    lt = time.localtime(now)
    cur = lt.tm_hour * 60 + lt.tm_min
    if start_min <= end_min:
        return start_min <= cur < end_min  # 同日区间，如 09:00-18:00
    return cur >= start_min or cur < end_min  # 跨午夜，如 23:00-07:00


class HealthNudge:
    """健康节律：连续 active ≥ 阈值 且 非静默时段 且 同段未提醒 → 久坐提醒。"""
    name = "health_nudge"

    def __init__(self, idle_warn_minutes: int = 45, quiet_hours: str = "23:00-07:00"):
        self._idle_warn_minutes = max(1, int(idle_warn_minutes))
        self._quiet_hours = quiet_hours
        self._warned_segment: object = None  # 已提醒过的活动段 id

    def tick(self, snapshot: WatchSnapshot, ctx: WatchCtx) -> dict | None:
        act = snapshot.activity
        if not act or act.get("state") != "active":
            return None
        if float(act.get("seconds", 0)) < self._idle_warn_minutes * 60:
            return None
        seg = act.get("segment_id")
        if seg is not None and seg == self._warned_segment:
            return None
        if in_quiet_hours(snapshot.now, self._quiet_hours):
            return None
        self._warned_segment = seg
        return {"kind": "reminder", "text": "坐久了，起来活动一下吧 🧘"}


class Ambient:
    """在场陪伴（slice 1）：占位，不出声。预留 snapshot_history 供 slice 3 主动搭话。"""
    name = "ambient"

    def __init__(self) -> None:
        self.snapshot_history: list[WatchSnapshot] = []

    def tick(self, snapshot: WatchSnapshot, ctx: WatchCtx) -> dict | None:
        return None


def build_behaviors(settings: dict) -> list:
    """按 settings 构造 watch 行为集（slice 1 固定：健康节律 + 在场陪伴）。"""
    return [
        HealthNudge(
            idle_warn_minutes=int(settings.get("watch.idle_warn_minutes", 45)),
            quiet_hours=str(settings.get("watch.quiet_hours", "23:00-07:00")),
        ),
        Ambient(),
    ]


def snapshot_from_perception(store, now: float) -> WatchSnapshot:
    """从 perception store 构造快照：前台 app + 当前活动段（state/已持续秒/段 id）。

    段 id 用最新 activity 观测的 ts——活动状态切换会产生新 ts=新段，用于「同段不重复提醒」。
    store 无对应数据时字段为 None。无 store（测试/未启用）→ 仅 now。
    """
    snap = WatchSnapshot(now=now)
    if store is None:
        return snap
    app_obs = store.latest_before("app", now)
    if app_obs and app_obs.get("payload", {}).get("app"):
        snap.app = str(app_obs["payload"]["app"])
    act_obs = store.latest_before("activity", now)
    if act_obs and act_obs.get("kind") in ("active", "idle"):
        seg_ts = float(act_obs.get("ts", now))
        snap.activity = {
            "state": act_obs["kind"],
            "seconds": max(0.0, now - seg_ts),
            "segment_id": seg_ts,
        }
    return snap
