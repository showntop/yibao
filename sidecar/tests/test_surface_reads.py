import asyncio

import pytest

from yibao_brain.surface_reads import (
    SurfaceReadGuard,
    SurfaceReadPolicy,
    SurfaceReadThrottled,
)


def test_sequential_reads_use_short_cache_and_return_copies():
    async def scenario():
        calls = 0
        guard = SurfaceReadGuard()

        async def load():
            nonlocal calls
            calls += 1
            return {"rows": [{"id": "one"}]}

        key = guard.key("demo.list", {})
        first, source1 = await guard.run(key, load)
        first["rows"][0]["id"] = "mutated"
        second, source2 = await guard.run(key, load)
        assert calls == 1
        assert source1 == "origin" and source2 == "cache"
        assert second == {"rows": [{"id": "one"}]}

    asyncio.run(scenario())


def test_concurrent_reads_share_one_in_flight_execution():
    async def scenario():
        calls = 0
        started = asyncio.Event()
        release = asyncio.Event()
        guard = SurfaceReadGuard()

        async def load():
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return {"ok": True}

        key = guard.key("demo.list", {"page": 1})
        one = asyncio.create_task(guard.run(key, load))
        await started.wait()
        two = asyncio.create_task(guard.run(key, load))
        release.set()
        results = await asyncio.gather(one, two)
        assert calls == 1
        assert {source for _, source in results} == {"origin", "singleflight"}

    asyncio.run(scenario())


def test_burst_opens_circuit_and_serves_recent_snapshot():
    async def scenario():
        now = 10.0
        calls = 0
        guard = SurfaceReadGuard(
            SurfaceReadPolicy(cache_ttl=-1, stale_ttl=30, burst_limit=2, cooldown=2),
            clock=lambda: now,
        )

        async def load():
            nonlocal calls
            calls += 1
            return {"version": calls}

        key = guard.key("demo.list", {})
        first, _ = await guard.run(key, load)
        second, _ = await guard.run(key, load)
        third, source = await guard.run(key, load)
        assert first == {"version": 1} and second == {"version": 2}
        assert third == {"version": 2} and source == "stale"
        assert calls == 2

    asyncio.run(scenario())


def test_open_circuit_without_snapshot_is_explicit():
    async def scenario():
        guard = SurfaceReadGuard(SurfaceReadPolicy(burst_limit=0))
        key = guard.key("demo.list", {})
        with pytest.raises(SurfaceReadThrottled, match="读取请求过密"):
            await guard.run(key, lambda: asyncio.sleep(0, result={}))

    asyncio.run(scenario())
