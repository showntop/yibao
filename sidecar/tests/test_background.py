"""background.py 后台循环的单 tick 体测试（循环壳不测）。"""
from __future__ import annotations
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from yibao_brain.background import _perception_cleanup_tick  # noqa: E402


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
