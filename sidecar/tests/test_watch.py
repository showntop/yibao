"""watch mode slice 1 单测：quiet_hours 解析 + 健康节律行为 + 快照构造。不触真定时/perception。"""
import time

from yibao_brain.watch import (
    Ambient,
    Budget,
    HealthNudge,
    LateNightNudge,
    ProactiveChat,
    WatchCtx,
    WatchSnapshot,
    _proactive_look,
    build_behaviors,
    in_quiet_hours,
    snapshot_from_perception,
)


def _lt(h: int, mi: int) -> float:
    """今天本地 h:mi 的 time.time() 值。"""
    now = time.localtime()
    return time.mktime((now.tm_year, now.tm_mon, now.tm_mday, h, mi, 0, 0, 0, -1))


def _snap(now, state=None, seconds=0.0, seg=1):
    return WatchSnapshot(
        now=now,
        activity=({"state": state, "seconds": seconds, "segment_id": seg} if state else None),
    )


# ---------- in_quiet_hours ----------
def test_quiet_hours_cross_midnight_inside():
    assert in_quiet_hours(_lt(2, 0), "23:00-07:00") is True
    assert in_quiet_hours(_lt(23, 0), "23:00-07:00") is True  # 起点 inclusive


def test_quiet_hours_cross_midnight_outside():
    assert in_quiet_hours(_lt(10, 0), "23:00-07:00") is False
    assert in_quiet_hours(_lt(7, 0), "23:00-07:00") is False  # 终点 exclusive
    assert in_quiet_hours(_lt(22, 30), "23:00-07:00") is False


def test_quiet_hours_same_day():
    assert in_quiet_hours(_lt(12, 0), "09:00-18:00") is True
    assert in_quiet_hours(_lt(8, 59), "09:00-18:00") is False


def test_quiet_hours_empty_or_bad():
    assert in_quiet_hours(_lt(2, 0), "") is False
    assert in_quiet_hours(_lt(2, 0), "bad") is False
    assert in_quiet_hours(_lt(2, 0), "23-07") is False


# ---------- HealthNudge ----------
def test_health_nudge_fires_when_active_long_enough():
    n = HealthNudge(idle_warn_minutes=45, quiet_hours="")
    ev = n.tick(_snap(1000, "active", 46 * 60, seg=1), WatchCtx())
    assert ev and ev["kind"] == "reminder" and "起来" in ev["text"]


def test_health_nudge_no_repeat_same_segment():
    n = HealthNudge(idle_warn_minutes=45, quiet_hours="")
    n.tick(_snap(1000, "active", 46 * 60, seg=1), WatchCtx())
    assert n.tick(_snap(2000, "active", 47 * 60, seg=1), WatchCtx()) is None


def test_health_nudge_below_threshold():
    n = HealthNudge(idle_warn_minutes=45, quiet_hours="")
    assert n.tick(_snap(1000, "active", 30 * 60, seg=1), WatchCtx()) is None


def test_health_nudge_idle_never():
    n = HealthNudge(idle_warn_minutes=45, quiet_hours="")
    assert n.tick(_snap(1000, "idle", 99 * 60, seg=1), WatchCtx()) is None


def test_health_nudge_respects_quiet_hours():
    n = HealthNudge(idle_warn_minutes=45, quiet_hours="23:00-07:00")
    snap = WatchSnapshot(now=_lt(2, 0), activity={"state": "active", "seconds": 46 * 60, "segment_id": 1})
    assert n.tick(snap, WatchCtx()) is None


def test_health_nudge_new_segment_re_allows():
    n = HealthNudge(idle_warn_minutes=45, quiet_hours="")
    n.tick(_snap(1000, "active", 46 * 60, seg=1), WatchCtx())
    ev = n.tick(_snap(3000, "active", 46 * 60, seg=2), WatchCtx())  # 新活动段
    assert ev and ev["kind"] == "reminder"


# ---------- Ambient（在场陪伴：首活跃问候/回归问候/专注里程碑）----------
def _day(y, mo, d, h, mi) -> float:
    """指定日期 h:mi 的 time.time() 值（跨天重置测试用）。"""
    return time.mktime((y, mo, d, h, mi, 0, 0, 0, -1))


def test_ambient_first_active_greeting_once_per_day():
    """每日首活跃问候：当天首次 active 发一次；同日不再发。"""
    a = Ambient(quiet_hours="")
    ev = a.tick(_snap(_lt(9, 0), "active", 1, seg=1), WatchCtx())
    assert ev and ev["type"] == "ambient" and "你来啦" in ev["text"]
    assert a.tick(_snap(_lt(10, 0), "active", 2, seg=2), WatchCtx()) is None  # 同日第二次


def test_ambient_ignores_non_active():
    a = Ambient(quiet_hours="")
    assert a.tick(_snap(_lt(9, 0), "idle", 9999, 1), WatchCtx()) is None
    assert a.tick(WatchSnapshot(now=_lt(9, 0), activity=None), WatchCtx()) is None


def test_ambient_silent_in_quiet_hours_greeting_not_consumed():
    """静默时段整体抑制且不记账：出静默后首活跃问候仍可发。"""
    a = Ambient(quiet_hours="23:00-07:00")
    late = WatchSnapshot(now=_lt(23, 30), activity={"state": "active", "seconds": 10, "segment_id": 1})
    assert a.tick(late, WatchCtx()) is None
    assert a._greeted is False  # 静默被抑制时不记账
    day = WatchSnapshot(now=_lt(10, 0), activity={"state": "active", "seconds": 10, "segment_id": 2})
    ev = a.tick(day, WatchCtx())
    assert ev and "你来啦" in ev["text"]


def test_ambient_cooldown_suppresses_events():
    """任一 ambient 事件后 cooldown 内不再出声（含新段场景）。"""
    a = Ambient(quiet_hours="", cooldown_hours=2.0)
    a.tick(_snap(_lt(9, 0), "active", 1, seg=1), WatchCtx())  # 首问候 → _last_fired=09:00
    assert a.tick(_snap(_lt(10, 0), "active", 1, seg=2), WatchCtx()) is None  # 1h 后：冷却内
    assert a.tick(_snap(_lt(10, 0), "idle", 30, seg=2), WatchCtx()) is None


def test_ambient_return_greeting_after_long_idle():
    """回归问候：idle ≥30min 回 active 发「回来啦」。"""
    a = Ambient(quiet_hours="", return_after_minutes=30)
    a.tick(_snap(_lt(9, 0), "active", 1, seg=1), WatchCtx())   # 首问候
    a.tick(_snap(_lt(12, 0), "idle", 1800, seg=1), WatchCtx())  # idle 30min
    ev = a.tick(_snap(_lt(12, 30), "active", 1, seg=2), WatchCtx())
    assert ev and "回来啦" in ev["text"]


def test_ambient_short_idle_no_return_greeting():
    """短暂离开不触发回归问候。"""
    a = Ambient(quiet_hours="", return_after_minutes=30)
    a.tick(_snap(_lt(9, 0), "active", 1, seg=1), WatchCtx())
    a.tick(_snap(_lt(12, 0), "idle", 60, seg=1), WatchCtx())  # 只离开 1 分钟
    assert a.tick(_snap(_lt(12, 30), "active", 1, seg=2), WatchCtx()) is None


def test_ambient_return_greeting_respects_daily_cap():
    """回归问候每日上限：达 welcome_max 后不再发。"""
    a = Ambient(quiet_hours="", return_after_minutes=30, welcome_max=1)
    a.tick(_snap(_lt(9, 0), "active", 1, seg=1), WatchCtx())
    a.tick(_snap(_lt(12, 0), "idle", 1800, seg=1), WatchCtx())
    ev = a.tick(_snap(_lt(12, 30), "active", 1, seg=2), WatchCtx())
    assert ev and "回来啦" in ev["text"]  # 第 1 次回归（上限 1）
    a.tick(_snap(_lt(15, 0), "idle", 2000, seg=2), WatchCtx())
    assert a.tick(_snap(_lt(15, 30), "active", 1, seg=3), WatchCtx()) is None  # 达上限


def test_ambient_focus_milestone_uses_parameterized_text():
    """专注里程碑：文案跟随 focus_hours 参数（_milestone_text 被真实使用）。"""
    a = Ambient(quiet_hours="", focus_hours=1.5)
    a.tick(_snap(_lt(9, 0), "active", 1, seg=1), WatchCtx())
    ev = a.tick(_snap(_lt(12, 30), "active", 1.5 * 3600 + 10, seg=1), WatchCtx())
    assert ev and "1.5小时" in ev["text"]


def test_ambient_focus_milestone_once_per_segment_and_day():
    """专注里程碑：同段不重复、跨段每日一次。"""
    a = Ambient(quiet_hours="", focus_hours=2.0)
    a.tick(_snap(_lt(9, 0), "active", 1, seg=1), WatchCtx())
    assert a.tick(_snap(_lt(12, 30), "active", 2 * 3600 + 5, seg=1), WatchCtx())  # 触发
    assert a.tick(_snap(_lt(14, 0), "active", 3 * 3600, seg=1), WatchCtx()) is None  # 同段不重复
    assert a.tick(_snap(_lt(20, 0), "active", 2 * 3600 + 5, seg=2), WatchCtx()) is None  # 新段当日已夸


def test_ambient_rolls_over_at_midnight():
    """跨天重置：昨日的问候/里程碑/回归计数全部清零。"""
    y = time.localtime().tm_year
    a = Ambient(quiet_hours="")
    a.tick(_snap(_day(y, 1, 1, 9, 0), "active", 10, seg=1), WatchCtx())  # 1/1 首问候
    a.tick(_snap(_day(y, 1, 1, 20, 0), "active", 2 * 3600, seg=1), WatchCtx())  # 1/1 里程碑
    a.tick(_snap(_day(y, 1, 1, 22, 0), "idle", 1800, seg=1), WatchCtx())  # 1/1 idle 30min
    ev = a.tick(_snap(_day(y, 1, 2, 9, 0), "active", 1, seg=2), WatchCtx())  # 1/2 重置后可再问候
    assert ev and "你来啦" in ev["text"]


def test_build_behaviors_slice1():
    bs = build_behaviors({"watch.idle_warn_minutes": 30, "watch.quiet_hours": "00:00-06:00"})
    assert [b.name for b in bs] == ["health_nudge", "late_night", "ambient"]


# ---------- LateNightNudge（深夜劝睡）----------
def test_late_night_fires_in_quiet_hours_when_active_long():
    n = LateNightNudge(active_minutes=45, quiet_hours="23:00-07:00")
    snap = WatchSnapshot(now=_lt(23, 30), activity={"state": "active", "seconds": 46 * 60, "segment_id": 1})
    ev = n.tick(snap, WatchCtx())
    assert ev and ev["kind"] == "reminder" and ev["type"] == "late_night"
    assert "早点休息" in ev["text"]


def test_late_night_silent_outside_quiet_hours():
    n = LateNightNudge(active_minutes=45, quiet_hours="23:00-07:00")
    snap = WatchSnapshot(now=_lt(15, 0), activity={"state": "active", "seconds": 99 * 60, "segment_id": 1})
    assert n.tick(snap, WatchCtx()) is None


def test_late_night_below_active_threshold_and_idle():
    n = LateNightNudge(active_minutes=45, quiet_hours="23:00-07:00")
    short = WatchSnapshot(now=_lt(23, 30), activity={"state": "active", "seconds": 20 * 60, "segment_id": 1})
    assert n.tick(short, WatchCtx()) is None
    idle = WatchSnapshot(now=_lt(23, 30), activity={"state": "idle", "seconds": 99 * 60, "segment_id": 1})
    assert n.tick(idle, WatchCtx()) is None


def test_late_night_cooldown_and_nightly_cap():
    n = LateNightNudge(active_minutes=45, quiet_hours="23:00-07:00", max_per_night=2, cooldown_s=3600)
    s = lambda mi: WatchSnapshot(now=_lt(23, 30) + mi * 60, activity={"state": "active", "seconds": 99 * 60, "segment_id": 1})
    assert n.tick(s(0), WatchCtx()) is not None       # 第 1 次
    assert n.tick(s(30), WatchCtx()) is None          # 30 分钟后再触发：冷却内
    ev = n.tick(s(70), WatchCtx())                    # 70 分钟后：第 2 次
    assert ev and "收尾就睡" in ev["text"]
    assert n.tick(s(200), WatchCtx()) is None         # 第 3 次：每晚上限 2
    assert n.tick(s(260), WatchCtx()) is None


def test_late_night_resets_after_quiet_hours():
    n = LateNightNudge(active_minutes=45, quiet_hours="23:00-07:00", max_per_night=2)
    late = WatchSnapshot(now=_lt(23, 30), activity={"state": "active", "seconds": 99 * 60, "segment_id": 1})
    assert n.tick(late, WatchCtx()) is not None
    n.tick(late, WatchCtx())  # 可能被冷却拦，无所谓
    day = WatchSnapshot(now=_lt(12, 0), activity={"state": "active", "seconds": 99 * 60, "segment_id": 2})
    assert n.tick(day, WatchCtx()) is None            # 白天不触发，并清零当晚计数
    late2 = WatchSnapshot(now=_lt(12, 0) + 12 * 3600, activity={"state": "active", "seconds": 99 * 60, "segment_id": 3})
    assert n.tick(late2, WatchCtx()) is not None      # 第二天深夜重新可触发


def test_build_behaviors_includes_late_night():
    bs = build_behaviors({"watch.idle_warn_minutes": 30, "watch.quiet_hours": "00:00-06:00"})
    assert [b.name for b in bs] == ["health_nudge", "late_night", "ambient"]


# ---------- snapshot_from_perception ----------
class _FakeStore:
    def __init__(self, app=None, activity=None):
        self._app = app
        self._activity = activity

    def latest_before(self, source, ts):
        if source == "app":
            return self._app
        if source == "activity":
            return self._activity
        return None


def test_snapshot_from_perception():
    store = _FakeStore(
        app={"payload": {"app": "Safari"}},
        activity={"kind": "active", "ts": 100.0},
    )
    snap = snapshot_from_perception(store, now=100.0 + 50 * 60)
    assert snap.app == "Safari"
    assert snap.activity["state"] == "active"
    assert abs(snap.activity["seconds"] - 50 * 60) < 1
    assert snap.activity["segment_id"] == 100.0


def test_snapshot_no_data():
    snap = snapshot_from_perception(_FakeStore(), now=1000.0)
    assert snap.app is None and snap.activity is None


def test_snapshot_no_store():
    snap = snapshot_from_perception(None, now=1000.0)
    assert snap.app is None and snap.activity is None and snap.now == 1000.0


def test_snapshot_respects_perception_switches_and_freshness():
    store = _FakeStore(
        app={"ts": 990.0, "payload": {"app": "Code", "bundle_id": "com.microsoft.VSCode"}},
        activity={"ts": 990.0, "kind": "active", "payload": {"idle_seconds": 0}},
    )
    off = snapshot_from_perception(
        store,
        now=1000.0,
        settings={"perception.master": False, "perception.app": True, "perception.activity": True},
        max_age=15.0,
    )
    assert off.app is None and off.app_id is None and off.activity is None

    stale = snapshot_from_perception(
        store,
        now=1010.1,
        settings={"perception.master": True, "perception.app": True, "perception.activity": True},
        max_age=15.0,
    )
    assert stale.app is None and stale.app_id is None and stale.activity is None

    fresh = snapshot_from_perception(
        store,
        now=1000.0,
        settings={"perception.master": True, "perception.app": True, "perception.activity": True},
        max_age=15.0,
    )
    assert fresh.app == "Code" and fresh.app_id == "com.microsoft.VSCode"
    assert fresh.activity and fresh.activity["state"] == "active"


def test_snapshot_uses_activity_segment_start_from_heartbeat_payload():
    store = _FakeStore(
        activity={
            "ts": 130.0,
            "kind": "active",
            "payload": {"idle_seconds": 0, "segment_started_at": 100.0},
        },
    )
    snap = snapshot_from_perception(
        store,
        now=135.0,
        settings={"perception.master": True, "perception.app": False, "perception.activity": True},
        max_age=15.0,
    )
    assert snap.activity["seconds"] == 35.0
    assert snap.activity["segment_id"] == 100.0


# ---------- _watch_tick：行为→gating→出口 ----------
def test_watch_tick_emits_in_full_swallows_in_quiet():
    """健康节律事件经 _gate_proactive_event：full 放行、quiet 吞掉。"""
    from yibao_brain.server import _watch_tick

    long_active = lambda seg: WatchSnapshot(  # noqa: E731
        now=1000, activity={"state": "active", "seconds": 46 * 60, "segment_id": seg}
    )
    full = _watch_tick(
        build_behaviors({"watch.idle_warn_minutes": 45, "watch.quiet_hours": ""}),
        long_active(1), {"proactive.level": "full"},
    )
    assert full and full[0]["kind"] == "reminder"

    quiet = _watch_tick(
        build_behaviors({"watch.idle_warn_minutes": 45, "watch.quiet_hours": ""}),
        long_active(2), {"proactive.level": "quiet"},
    )
    assert quiet == []


def test_watch_tick_isolates_behavior_errors():
    """一个行为报错不影响其它行为/整轮。"""
    from yibao_brain.server import _watch_tick

    class _Boom:
        name = "boom"
        def tick(self, snap, ctx):
            raise RuntimeError("x")
    ok = HealthNudge(idle_warn_minutes=45, quiet_hours="")
    snap = WatchSnapshot(now=1000, activity={"state": "active", "seconds": 46 * 60, "segment_id": 1})
    out = _watch_tick([_Boom(), ok], snap, {"proactive.level": "full"})
    assert out and out[0]["kind"] == "reminder"  # _Boom 被跳过，ok 照常出


# ---------- Budget 预算闸 ----------
def test_budget_hour_cap_then_slide():
    now = [1000.0]
    b = Budget(max_per_hour=2, max_per_day=10, clock=lambda: now[0])
    assert b.allow() and b.allow()
    assert b.allow() is False
    now[0] += 3601
    assert b.allow()  # 一小时窗口滑过


def test_budget_day_cap():
    now = [0.0]
    b = Budget(max_per_hour=100, max_per_day=2, clock=lambda: now[0])
    assert b.allow(); now[0] += 10
    assert b.allow(); now[0] += 10
    assert b.allow() is False
    now[0] += 86401
    assert b.allow()  # 一天窗口滑过


def test_budget_zero_disabled():
    assert Budget(0, 10).allow() is False


# ---------- ProactiveChat 主动搭话 ----------
def _wait(events, timeout=2.0):
    for _ in range(int(timeout / 0.02)):
        if events:
            return
        time.sleep(0.02)


class _ScreenshotHost:
    def __init__(self, path):
        self.screenshotter = type("S", (), {"capture": staticmethod(lambda: str(path))})()


class _NoSpeakVision:
    def __init__(self):
        self.calls = []

    def observe(self, b64, app):
        self.calls.append(app)
        return {"speak": False, "text": ""}


def test_proactive_chat_fires_on_segment_change(tmp_path):
    png = tmp_path / "s.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")  # 占位字节，_b64_of 能读即可

    class _Host:
        screenshotter = type("S", (), {"capture": staticmethod(lambda: str(png))})()

    class _Vis:
        def __init__(self):
            self.calls = []

        def observe(self, b64, app):
            self.calls.append(app)
            return {"speak": True, "text": "试试重启"}

    emitted = []
    v = _Vis()
    pc = ProactiveChat(observe_apps=["com.microsoft.VSCode"], look_min_gap=0, vision=v,
                       host=_Host(), budget=Budget(10, 100), emit=emitted.append,
                       frontmost=lambda: "com.microsoft.VSCode")
    pc.tick(WatchSnapshot(now=1000, app="VSCode", app_id="com.microsoft.VSCode",
                          activity={"state": "active", "seconds": 99, "segment_id": 1}), WatchCtx())
    _wait(emitted)
    assert emitted and emitted[0]["text"] == "试试重启"
    assert v.calls == ["VSCode"]


def test_proactive_chat_blocked_when_app_not_in_allowlist():
    pc = ProactiveChat(observe_apps=["VSCode"], vision=object(), host=object(),
                       budget=Budget(10, 10), emit=lambda e: None)
    snap = WatchSnapshot(now=1, app="Safari",
                         activity={"state": "active", "seconds": 9, "segment_id": 1})
    assert pc.tick(snap, WatchCtx()) is None


def test_proactive_chat_throttle_blocks_second_within_gap():
    import tempfile
    from pathlib import Path

    png = Path(tempfile.gettempdir()) / "yibao-watch-test.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    b = Budget(10, 100)
    v = _NoSpeakVision()
    pc = ProactiveChat(observe_apps=["a.bundle"], look_min_gap=100, vision=v,
                       host=_ScreenshotHost(png), budget=b, emit=lambda e: None,
                       frontmost=lambda: "a.bundle")
    pc.tick(WatchSnapshot(now=1000, app="A", app_id="a.bundle", activity={"state": "active", "seconds": 1, "segment_id": 1}), WatchCtx())
    _wait(v.calls)
    pc.tick(WatchSnapshot(now=1050, app="A", app_id="a.bundle", activity={"state": "active", "seconds": 2, "segment_id": 2}), WatchCtx())
    assert len(v.calls) == 1


def test_proactive_chat_app_switch_retriggers_in_same_active_segment(tmp_path):
    png = tmp_path / "s.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    current = ["a.bundle"]
    v = _NoSpeakVision()
    pc = ProactiveChat(
        observe_apps=["a.bundle", "b.bundle"], look_min_gap=0, vision=v,
        host=_ScreenshotHost(png), budget=Budget(10, 100), emit=lambda e: None,
        frontmost=lambda: current[0],
    )
    activity = {"state": "active", "seconds": 1, "segment_id": 7}
    pc.tick(WatchSnapshot(now=1000, app="A", app_id="a.bundle", activity=activity), WatchCtx())
    _wait(v.calls)
    current[0] = "b.bundle"
    pc.tick(WatchSnapshot(now=1001, app="B", app_id="b.bundle", activity=activity), WatchCtx())
    for _ in range(100):
        if len(v.calls) == 2:
            break
        time.sleep(0.02)
    assert v.calls == ["A", "B"]


def test_proactive_look_discards_capture_when_frontmost_changes(tmp_path):
    png = tmp_path / "s.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    current = iter(["a.bundle", "mail.bundle"])
    v = _NoSpeakVision()
    events = []
    budget = Budget(10, 100)

    _proactive_look(
        _ScreenshotHost(png), v, "A", "a.bundle", events.append,
        frontmost=lambda: next(current), budget=budget,
    )

    assert v.calls == []
    assert events == []
    assert budget._stamps == []


def test_proactive_chat_budget_exhausted_blocks():
    pc = ProactiveChat(observe_apps=["A"], look_min_gap=0, vision=object(),
                       host=object(), budget=Budget(0, 100), emit=lambda e: None)
    snap = WatchSnapshot(now=1, app="A", activity={"state": "active", "seconds": 1, "segment_id": 1})
    assert pc.tick(snap, WatchCtx()) is None


def test_proactive_chat_dormant_without_vision():
    pc = ProactiveChat(observe_apps=["A"], vision=None, host=object(),
                       budget=Budget(10, 10), emit=lambda e: None)
    snap = WatchSnapshot(now=1, app="A", activity={"state": "active", "seconds": 1, "segment_id": 1})
    assert pc.tick(snap, WatchCtx()) is None


def test_build_behaviors_proactive_only_when_vision_and_allowlist():
    deps = dict(
        host=object(),
        vision=object(),
        budget=Budget(1, 1),
        emit=lambda e: None,
        frontmost=lambda: "X",
    )
    assert any(b.name == "proactive_chat" for b in build_behaviors({"watch.observe_apps": ["X"]}, **deps))
    assert not any(b.name == "proactive_chat" for b in build_behaviors({}, **deps))  # 白名单空
    assert not any(b.name == "proactive_chat" for b in build_behaviors({"watch.observe_apps": ["X"]}, host=object(), vision=None, budget=Budget(1, 1), emit=lambda e: None))


def test_ambient_events_carry_signal_for_shell_reaction():
    """三信号各带 signal 标识（壳侧按它配宠物反应：greeting/welcome→招手，milestone→星芒）。"""
    a = Ambient(quiet_hours="", return_after_minutes=30, focus_hours=1.0, cooldown_hours=0.0)
    ev = a.tick(_snap(_lt(9, 0), "active", 1, seg=1), WatchCtx())
    assert ev and ev["signal"] == "greeting"
    a.tick(_snap(_lt(10, 0), "idle", 1800, seg=1), WatchCtx())
    ev = a.tick(_snap(_lt(10, 30), "active", 1, seg=2), WatchCtx())
    assert ev and ev["signal"] == "welcome"
    ev = a.tick(_snap(_lt(11, 40), "active", 3600 + 10, seg=2), WatchCtx())
    assert ev and ev["signal"] == "milestone"


# ---------- Ambient 状态落盘（大脑重启不重发当日问候） ----------
def test_ambient_state_persists_across_restart(tmp_path):
    """当日问候/冷却时刻跨「重启」（新实例同路径）保留：不再重发首活跃问候。"""
    p = str(tmp_path / "ambient.json")
    a = Ambient(quiet_hours="", state_path=p)
    a.tick(_snap(_lt(9, 0), "active", 1, seg=1), WatchCtx())  # 首问候 → 落盘
    b = Ambient(quiet_hours="", state_path=p)  # 「重启」：新实例读同一文件
    assert b._greeted is True and b._last_fired > 0
    assert b.tick(_snap(_lt(9, 5), "active", 2, seg=2), WatchCtx()) is None  # 不重发


def test_ambient_state_rolls_day_after_reload(tmp_path):
    """跨天：落盘的昨日标记在新一天首次 tick 正常清零，当日问候照发。"""
    p = str(tmp_path / "ambient.json")
    a = Ambient(quiet_hours="", state_path=p)
    a.tick(_snap(_day(2026, 8, 23, 9, 0), "active", 1, seg=1), WatchCtx())
    b = Ambient(quiet_hours="", state_path=p)
    ev = b.tick(_snap(_day(2026, 8, 24, 9, 0), "active", 1, seg=2), WatchCtx())
    assert ev and ev["signal"] == "greeting"


def test_ambient_state_bad_file_tolerated(tmp_path):
    """坏状态文件静默回默认值（不炸行为、照常被闸）。"""
    p = tmp_path / "ambient.json"
    p.write_text("{oops", encoding="utf-8")
    a = Ambient(quiet_hours="", state_path=str(p))
    ev = a.tick(_snap(_lt(9, 0), "active", 1, seg=1), WatchCtx())
    assert ev and ev["signal"] == "greeting"


def test_ambient_greeting_text_by_hour():
    """首活跃问候按时段分文案（晚间重启不再喊「新的一天」）。"""
    a = Ambient(quiet_hours="")
    for h, frag in ((8, "新的一天"), (12, "中午好"), (15, "下午好"), (20, "晚上好"), (2, "夜深了")):
        assert frag in a._greeting_text(time.mktime((2026, 8, 24, h, 30, 0, 0, 0, -1))), h
