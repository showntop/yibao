"""watch mode slice 1 单测：quiet_hours 解析 + 健康节律行为 + 快照构造。不触真定时/perception。"""
import time

from yibao_brain.watch import (
    Ambient,
    HealthNudge,
    WatchCtx,
    WatchSnapshot,
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


# ---------- Ambient + build_behaviors ----------
def test_ambient_silent():
    assert Ambient().tick(_snap(1, "active", 9999, 1), WatchCtx()) is None


def test_build_behaviors_slice1():
    bs = build_behaviors({"watch.idle_warn_minutes": 30, "watch.quiet_hours": "00:00-06:00"})
    assert [b.name for b in bs] == ["health_nudge", "ambient"]


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
