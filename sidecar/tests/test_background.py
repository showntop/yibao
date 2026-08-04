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
