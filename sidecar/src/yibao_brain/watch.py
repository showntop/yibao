"""watch mode：周期观察 + 可插拔主动行为。

行为只读快照、返回主动事件（或 None）；事件由 server 的 watch 循环经
_gate_proactive_event（proactive.level）出口。含健康节律（久坐提醒）、深夜劝睡、
在场陪伴（Ambient：回归问候/每日首活跃问候/专注里程碑，确定性信号零 LLM）。
无 LLM、无屏幕上云。spec：
docs/superpowers/specs/2026-07-31-watch-mode-core-design.md
"""
from __future__ import annotations

from .log import log
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


class LateNightNudge:
    """深夜劝睡（反应式宠物 C）：静默时段内仍连续活跃 ≥ 阈值 → 打哈欠劝睡。
    每晚最多 max_per_night 次、间隔 ≥ cooldown_s；出静默时段清零重计。
    与 HealthNudge 互斥天然成立：久坐提醒在静默时段被抑制，本行为只在静默时段触发。"""
    name = "late_night"

    def __init__(self, active_minutes: int = 45, quiet_hours: str = "23:00-07:00",
                 max_per_night: int = 2, cooldown_s: float = 3600.0):
        self._active_s = max(1, int(active_minutes)) * 60
        self._quiet_hours = quiet_hours
        self._max_per_night = max(0, int(max_per_night))
        self._cooldown = float(cooldown_s)
        self._fired: list[float] = []  # 当晚已触发时刻（snapshot.now 系）

    def tick(self, snapshot: WatchSnapshot, ctx: WatchCtx) -> dict | None:
        if not in_quiet_hours(snapshot.now, self._quiet_hours):
            if self._fired:
                self._fired = []  # 天亮出静默时段：清零，明晚重新计
            return None
        act = snapshot.activity
        if not act or act.get("state") != "active":
            return None
        if float(act.get("seconds", 0)) < self._active_s:
            return None
        if len(self._fired) >= self._max_per_night:
            return None
        if self._fired and snapshot.now - self._fired[-1] < self._cooldown:
            return None
        self._fired.append(snapshot.now)
        text = ("很晚了，还在忙吗？早点休息 🌙" if len(self._fired) == 1
                else "深夜了还在忙，收尾就睡吧 😴")
        return {"kind": "reminder", "type": "late_night", "text": text}


class Ambient:
    """在场陪伴：三条确定性信号，模板文案，零 LLM 零视觉（reactive-pet 设计 C）。

    - 回归问候：idle ≥ return_after_minutes 后回到 active，每日 ≤ welcome_max 次
    - 每日首活跃问候：当天首次进入 active（静默时段不算，留到出静默再问候），每日 1 次
    - 专注里程碑：同一活动段连续 active 满 focus_hours 小时夸一句，每日 1 次
    任一事件后 cooldown_hours 小时内不再出声；静默时段整体不触发。
    与 HealthNudge（45min 久坐提醒）正交：信号不同源，互不互斥。"""
    name = "ambient"

    def __init__(self, *, return_after_minutes: int = 30, focus_hours: float = 2.0,
                 welcome_max: int = 2, cooldown_hours: float = 2.0,
                 quiet_hours: str = "23:00-07:00"):
        self._return_after_s = max(1, int(return_after_minutes)) * 60
        self._focus_s = float(focus_hours) * 3600
        self._welcome_max = max(0, int(welcome_max))
        self._cooldown_s = float(cooldown_hours) * 3600
        self._quiet_hours = quiet_hours
        self._milestone_text = f"已经连续专注{focus_hours:g}小时了，真厉害 ✨"
        self._date: tuple | None = None     # 当前计数的本地日期（跨天清零）
        self._welcomes = 0                  # 当日回归问候次数
        self._greeted = False               # 当日首活跃问候是否已发
        self._milestone_segment: object = None  # 已夸过的活动段 id
        self._milestone_done = False        # 当日专注里程碑是否已发
        self._last_fired = 0.0              # 最近一次 ambient 事件时刻（snapshot.now 系）
        self._prev_state: str | None = None
        self._prev_idle_seconds = 0.0       # 上一段 idle 期间观测到的最大持续秒

    def _roll_day(self, now: float) -> None:
        lt = time.localtime(now)
        date = (lt.tm_year, lt.tm_mon, lt.tm_mday)
        if date != self._date:
            self._date = date
            self._welcomes = 0
            self._greeted = False
            self._milestone_done = False
            self._milestone_segment = None  # 跨天清零：即便段 id 理论撞车也重新可夸

    def _fire(self, now: float, text: str, signal: str) -> dict:
        self._last_fired = now
        # signal：三信号标识（greeting 首活跃/welcome 回归/milestone 专注里程碑），
        # 壳侧按它配不同的宠物反应（反应式渲染，对齐 task.status 先例）
        return {"kind": "reminder", "type": "ambient", "signal": signal, "text": text}

    def tick(self, snapshot: WatchSnapshot, ctx: WatchCtx) -> dict | None:
        self._roll_day(snapshot.now)
        act = snapshot.activity
        state = act.get("state") if act else None
        seconds = float(act.get("seconds", 0)) if act else 0.0
        if state == "idle":
            # 记录这段 idle 的持续时长，供「回归问候」判断是否离开够久
            self._prev_idle_seconds = max(self._prev_idle_seconds, seconds)
        prev_state, prev_idle = self._prev_state, self._prev_idle_seconds
        self._prev_state = state
        if state != "idle":
            self._prev_idle_seconds = 0.0

        if state != "active":
            return None
        if in_quiet_hours(snapshot.now, self._quiet_hours):
            return None
        if snapshot.now - self._last_fired < self._cooldown_s:
            return None

        # 每日首活跃问候（静默时段被抑制时不记账，出静默后仍可问候）
        if not self._greeted:
            self._greeted = True
            return self._fire(snapshot.now, "你来啦，新的一天开始吧 ☀️", "greeting")
        # 回归问候：刚从 ≥30 分钟的 idle 回到 active
        if (prev_state == "idle" and prev_idle >= self._return_after_s
                and self._welcomes < self._welcome_max):
            self._welcomes += 1
            return self._fire(snapshot.now, "回来啦，接着忙吧 👋", "welcome")
        # 专注里程碑：同一活动段连续 active 满阈值，每日一句
        seg = act.get("segment_id") if act else None
        if (not self._milestone_done and seconds >= self._focus_s
                and seg is not None and seg != self._milestone_segment):
            self._milestone_segment = seg
            self._milestone_done = True
            return self._fire(snapshot.now, self._milestone_text, "milestone")
        return None


def _b64_of(path: str) -> str | None:
    """截图文件 → data:image/png;base64,... （视觉 API 入参格式，与 core_tools._b64 一致）。"""
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
        log(f"主动搭话视觉调用失败（跳过）：{e}")


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
        LateNightNudge(
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
