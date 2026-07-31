import asyncio

from yibao_brain.watch import WatchSnapshot
from yibao_brain.watch_service import WatchService


def test_watch_service_applies_enable_disable_immediately():
    async def run():
        settings = {
            "watch.enabled": False,
            "watch.screen_enabled": False,
            "watch.cadence": 0.01,
            "watch.quiet_hours": "",
            "watch.idle_warn_minutes": 1,
        }
        emitted = []
        ticks = []

        class Dispatcher:
            def emit(self, event):
                emitted.append(event)

        service = WatchService(
            store=None,
            settings=settings,
            dispatcher=Dispatcher(),
            snapshot=lambda *_args, **_kwargs: (
                ticks.append(1)
                or WatchSnapshot(
                    now=100,
                    activity={"state": "active", "seconds": 120, "segment_id": 1},
                )
            ),
            min_cadence=0.01,
        )
        await service.apply_settings()
        assert service.status()["running"] is False
        settings["watch.enabled"] = True
        await service.apply_settings()
        await asyncio.sleep(0.03)
        assert service.status()["running"] is True
        assert ticks and emitted
        running_task = service._task
        settings["proactive.level"] = "quiet"
        await service.apply_settings()
        assert service._task is running_task
        settings["watch.enabled"] = False
        await service.apply_settings()
        assert service.status()["running"] is False
        await service.stop()

    asyncio.run(run())


def test_watch_service_uses_only_fresh_in_memory_sensor_state():
    settings = {
        "perception.master": True,
        "perception.app": True,
        "perception.activity": True,
    }
    state = {
        "sampled_at": 100.0,
        "app": "Code",
        "app_id": "com.microsoft.VSCode",
        "activity": "active",
        "activity_started_at": 80.0,
    }
    service = WatchService(
        store=object(), settings=settings, dispatcher=object(), live_state=lambda: state,
    )

    fresh = service._snapshot(110.0, max_age=15.0)
    stale = service._snapshot(116.0, max_age=15.0)

    assert fresh.app_id == "com.microsoft.VSCode"
    assert fresh.activity["seconds"] == 30.0
    assert stale.app is None and stale.activity is None
