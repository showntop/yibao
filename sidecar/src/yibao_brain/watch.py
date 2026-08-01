"""watch mode：周期观察 + 可插拔主动行为（slice 1）。

行为只读快照、返回主动事件（或 None）；事件由 server 的 watch 循环经
_gate_proactive_event（proactive.level）出口。本期含健康节律（久坐提醒，纯定时）
+ 在场陪伴（占位，不出声）。无 LLM、无屏幕上云。spec：
docs/superpowers/specs/2026-07-31-watch-mode-core-design.md
"""
from __future__ import annotations

import base64
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class WatchSnapshot:
    """一次 tick 的感知快照。activity=None 表示无活动数据。"""
    now: float
    app: str | None = None
    app_id: str | None = None
    activity: dict | None = None  # {"state":"active"|"idle","seconds":float,"segment_id":...}


@dataclass
class WatchCtx:
    """行为 tick 依赖。settings=当前设置（行为读 watch.*；出口 gating 由循环做）。"""
    settings: dict = field(default_factory=dict)
    host: Any = None       # 截图基座（ProactiveChat 用）
    vision: Any = None     # ComputerUseClient.observe（None=无视觉）
    budget: Any = None     # Budget 预算闸
    emit: Any = None       # gated 主动事件通道（后台线程直发）
    frontmost: Any = None  # 实时前台 bundle id 读取器


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


class Budget:
    """调用预算闸：滑动窗口限每小时/每日次数。allow() 命中即计入；超限返 False。"""
    def __init__(self, max_per_hour: int, max_per_day: int, *, clock=time.time):
        self._max_hour = max(0, int(max_per_hour))
        self._max_day = max(0, int(max_per_day))
        self._clock = clock
        self._stamps: list[float] = []
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            now = self._clock()
            self._stamps = [t for t in self._stamps if t > now - 86400]  # 留一天内
            if self._max_hour <= 0 or self._max_day <= 0:
                return False
            in_hour = sum(1 for t in self._stamps if t > now - 3600)
            if len(self._stamps) >= self._max_day or in_hour >= self._max_hour:
                return False
            self._stamps.append(now)
            return True


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
        return {"kind": "reminder", "type": "health_nudge", "text": "坐久了，起来活动一下吧 🧘"}


class Ambient:
    """在场陪伴（slice 1）：占位，不出声。预留 snapshot_history 供 slice 3 主动搭话。"""
    name = "ambient"

    def __init__(self) -> None:
        self.snapshot_history: list[WatchSnapshot] = []

    def tick(self, snapshot: WatchSnapshot, ctx: WatchCtx) -> dict | None:
        return None


def _b64_of(path: str) -> str | None:
    """截图文件 → data:image/png;base64,... （视觉 API 入参格式，与 skills_real._b64 一致）。"""
    try:
        with open(path, "rb") as f:
            return "data:image/png;base64," + base64.b64encode(f.read()).decode()
    except Exception:
        return None


def _proactive_look(host, vision, app: str, app_id: str, emit, *, frontmost, budget) -> None:
    """后台安全观察：前台身份前后相同且在已批准 app 时才把截图交给视觉模型。"""
    try:
        if frontmost() != app_id:
            return
        b64 = _b64_of(host.screenshotter.capture())
        if not b64:
            return
        if frontmost() != app_id:
            return
        if budget is None or not budget.allow():
            return
        res = vision.observe(b64, app)
        if isinstance(res, dict) and res.get("speak") and res.get("text"):
            emit({"kind": "reminder", "type": "proactive_chat", "text": str(res["text"])})
    except Exception as e:
        print(f"[yibao] 主动搭话视觉调用失败（跳过）：{e}", file=sys.stderr)


class ProactiveChat:
    """场景化主动搭话（slice 3）：活动段切换+节流+白名单+预算 通过时，
    起后台线程截图交视觉模型判断是否搭话。tick 恒返 None（后台线程直发 emit）。"""
    name = "proactive_chat"

    def __init__(self, *, observe_apps, look_min_gap: float = 300.0,
                 vision=None, host=None, budget=None, emit=None, frontmost=None,
                 clock=time.time):
        self._apps = {a for a in (observe_apps or []) if a}
        self._gap = float(look_min_gap)
        self._vision = vision
        self._host = host
        self._budget = budget
        self._emit = emit
        self._frontmost = frontmost
        self._clock = clock
        self._last_key: object = None
        self._last_look = 0.0

    def tick(self, snapshot: WatchSnapshot, ctx: WatchCtx) -> dict | None:
        # 缺依赖或白名单空 → 永不看（opt-in）
        if (not self._apps or not self._vision or not self._host or self._budget is None
                or self._emit is None or self._frontmost is None):
            return None
        app = snapshot.app
        app_id = snapshot.app_id
        if not app or not app_id or app_id not in self._apps:
            return None
        act = snapshot.activity
        seg = act.get("segment_id") if act else None
        key = (app_id, seg)
        if seg is None or key == self._last_key:
            return None
        now = snapshot.now
        if now - self._last_look < self._gap:  # 节流
            return None
        self._last_key = key
        self._last_look = now
        threading.Thread(
            target=_proactive_look,
            args=(self._host, self._vision, app, app_id, self._emit),
            kwargs={"frontmost": self._frontmost, "budget": self._budget},
            daemon=True, name="yibao-proactive-look",
        ).start()
        return None


def build_behaviors(settings: dict, *, host=None, vision=None, budget=None, emit=None,
                    frontmost=None) -> list:
    """按 settings 构造 watch 行为集：健康节律 + 在场陪伴 +（视觉可用且白名单非空时）主动搭话。"""
    behaviors = [
        HealthNudge(
            idle_warn_minutes=int(settings.get("watch.idle_warn_minutes", 45)),
            quiet_hours=str(settings.get("watch.quiet_hours", "23:00-07:00")),
        ),
        Ambient(),
    ]
    observe_apps = settings.get("watch.observe_apps") or []
    if (vision is not None and host is not None and budget is not None and emit is not None
            and frontmost is not None and observe_apps):
        behaviors.append(ProactiveChat(
            observe_apps=observe_apps,
            look_min_gap=float(settings.get("watch.look_min_gap", 300)),
            vision=vision, host=host, budget=budget, emit=emit, frontmost=frontmost,
        ))
    return behaviors


def snapshot_from_perception(
    store,
    now: float,
    *,
    settings: dict | None = None,
    max_age: float | None = None,
) -> WatchSnapshot:
    """从 perception store 构造快照：前台 app + 当前活动段（state/已持续秒/段 id）。

    段 id 用最新 activity 观测的 ts——活动状态切换会产生新 ts=新段，用于「同段不重复提醒」。
    store 无对应数据时字段为 None。无 store（测试/未启用）→ 仅 now。
    """
    snap = WatchSnapshot(now=now)
    if store is None or (settings is not None and not settings.get("perception.master", False)):
        return snap
    app_enabled = settings is None or settings.get("perception.app", False)
    activity_enabled = settings is None or settings.get("perception.activity", False)

    def fresh(obs: dict | None) -> bool:
        if not obs:
            return False
        if max_age is None:
            return True
        try:
            return 0 <= now - float(obs["ts"]) <= max_age
        except (KeyError, TypeError, ValueError):
            return False

    app_obs = store.latest_before("app", now) if app_enabled else None
    if fresh(app_obs) and app_obs.get("payload", {}).get("app"):
        snap.app = str(app_obs["payload"]["app"])
        bundle_id = app_obs["payload"].get("bundle_id")
        snap.app_id = str(bundle_id) if bundle_id else None
    act_obs = store.latest_before("activity", now) if activity_enabled else None
    if fresh(act_obs) and act_obs.get("kind") in ("active", "idle"):
        payload = act_obs.get("payload", {})
        seg_ts = float(payload.get("segment_started_at", act_obs.get("ts", now)))
        snap.activity = {
            "state": act_obs["kind"],
            "seconds": max(0.0, now - seg_ts),
            "segment_id": seg_ts,
        }
    return snap
