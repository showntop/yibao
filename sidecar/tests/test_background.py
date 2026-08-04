"""background.py 后台循环的单 tick 体测试（循环壳不测）。"""
from __future__ import annotations
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from yibao_brain import background  # noqa: E402
from yibao_brain.background import _distiller_tick, _perception_cleanup_tick  # noqa: E402


class _Pstore:
    def __init__(self) -> None:
        self.purged = 0

    def purge(self) -> int:  # _offload 在 executor 线程调同步方法
        self.purged += 1
        return 1


class _Distiller:
    def __init__(self) -> None:
        self.store = self._Store()

    class _Store:
        def __init__(self) -> None:
            self.purged = 0

        def purge(self) -> int:
            self.purged += 1
            return 1


def test_perception_cleanup_tick_purges_both():
    p, d = _Pstore(), _Distiller()
    asyncio.run(_perception_cleanup_tick(p, d))
    assert p.purged == 1 and d.store.purged == 1


def test_perception_cleanup_tick_one_failure_does_not_block_other():
    p, d = _Pstore(), _Distiller()

    def boom() -> None:
        raise RuntimeError("x")

    p.purge = boom  # type: ignore[method-assign]
    asyncio.run(_perception_cleanup_tick(p, d))  # p 抛，d 仍清
    assert d.store.purged == 1


def test_perception_cleanup_tick_none_safe():
    asyncio.run(_perception_cleanup_tick(None, None))  # 两皆 None 不抛


# ---------- _distiller_tick ----------


def _fake_distiller(last_day=None):
    class D:
        def __init__(self) -> None:
            self.ran: list[str] = []
            self.store = type("S", (), {"last_auto_run_day": lambda self: last_day})()

        def run_yesterday(self, source: str) -> dict:
            self.ran.append(source)
            return {"status": "ok"}

    return D()


def test_distiller_tick_gate_off_no_run(monkeypatch):
    monkeypatch.setattr(background, "auto_run_due", lambda *a: True)  # 即便到期
    d = _fake_distiller()
    asyncio.run(_distiller_tick({"perception.master": False, "perception.distill": True}, d))
    assert d.ran == []
    asyncio.run(_distiller_tick({"perception.master": True, "perception.distill": False}, d))
    assert d.ran == []


def test_distiller_tick_runs_when_due(monkeypatch):
    monkeypatch.setattr(background, "auto_run_due", lambda *a: True)
    d = _fake_distiller(last_day="2026-01-01")
    asyncio.run(_distiller_tick({"perception.master": True, "perception.distill": True}, d))
    assert d.ran == ["auto"]


def test_distiller_tick_skips_when_not_due(monkeypatch):
    monkeypatch.setattr(background, "auto_run_due", lambda *a: False)
    d = _fake_distiller()
    asyncio.run(_distiller_tick({"perception.master": True, "perception.distill": True}, d))
    assert d.ran == []


def test_distiller_tick_distiller_none_safe():
    asyncio.run(_distiller_tick({"perception.master": True, "perception.distill": True}, None))


def test_distiller_tick_store_failure_no_raise(monkeypatch):
    monkeypatch.setattr(background, "auto_run_due", lambda *a: True)

    class Boom:
        ran: list[str] = []

        class store:
            @staticmethod
            def last_auto_run_day():
                raise RuntimeError("db")

        def run_yesterday(self, src: str) -> None:
            self.ran.append(src)

    b = Boom()
    asyncio.run(_distiller_tick({"perception.master": True, "perception.distill": True}, b))
    assert b.ran == []


from yibao_brain.background import _reminder_tick  # noqa: E402


def _run_reminder_tick(store, due_or_exc):
    """构造 store（pop_due 返 due_or_exc 或抛），跑一次 tick，返回 dispatch 调用 id 列表。"""
    calls: list = []

    class Store:
        def pop_due(self, now):
            if isinstance(due_or_exc, BaseException):
                raise due_or_exc
            return due_or_exc

    async def fake_dispatch(r, **kw):
        calls.append(r.get("id"))

    # 直接替换 background 命名空间里的 _dispatch_reminder
    import yibao_brain.background as bg
    orig = bg._dispatch_reminder
    bg._dispatch_reminder = fake_dispatch  # type: ignore[assignment]
    try:
        agent = type("A", (), {"history": None})()
        asyncio.run(_reminder_tick(store=Store(), agent=agent, settings={"proactive.level": "quiet"},
                                   feed=None, voice=None, run_state={}, write_msg=lambda m: None,
                                   dispatcher=None))
    finally:
        bg._dispatch_reminder = orig  # type: ignore[assignment]
    return calls


def test_reminder_tick_dispatches_each_due():
    calls = _run_reminder_tick(None, [{"id": 1, "text": "a"}, {"id": 2, "text": "b"}])
    assert calls == [1, 2]


def test_reminder_tick_pop_failure_no_dispatch():
    calls = _run_reminder_tick(None, RuntimeError("db"))
    assert calls == []
