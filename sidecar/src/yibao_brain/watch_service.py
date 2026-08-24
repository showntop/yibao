"""Lifecycle owner for watch behaviors."""
from __future__ import annotations

from . import config
from .log import log
import asyncio
import os
import sys
import time

from .watch import Budget, WatchCtx, WatchSnapshot, build_behaviors, snapshot_from_perception



class WatchService:
    def __init__(
        self,
        *,
        store,
        settings: dict,
        dispatcher,
        host=None,
        vision=None,
        frontmost=None,
        live_state=None,
        snapshot=snapshot_from_perception,
        min_cadence: float = 10.0,
    ) -> None:
        self.store = store
        self.settings = settings
        self.dispatcher = dispatcher
        self.host = host
        self.vision = vision
        self.frontmost = frontmost
        self.live_state = live_state
        self.snapshot = snapshot
        self.min_cadence = min_cadence
        self._task: asyncio.Task | None = None
        self._signature: tuple | None = None
        self._last_error = ""

    async def apply_settings(self) -> None:
        desired = bool(self.settings.get("watch.enabled") or self.settings.get("watch.screen_enabled"))
        signature = self._settings_signature()
        if desired and self._task is not None and not self._task.done() and signature == self._signature:
            return
        await self.stop()
        if not desired:
            return
        self._signature = signature
        self._task = asyncio.create_task(self._run(), name="yibao-watch-service")

    async def stop(self) -> None:
        task, self._task = self._task, None
        self._signature = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def _settings_signature(self) -> tuple:
        keys = (
            "watch.enabled", "watch.screen_enabled", "watch.cadence",
            "watch.idle_warn_minutes", "watch.quiet_hours", "watch.look_min_gap",
            "watch.look_max_per_hour", "watch.look_max_per_day",
            "perception.master", "perception.app", "perception.activity",
        )
        values = [self.settings.get(key) for key in keys]
        values.append(tuple(self.settings.get("watch.observe_apps") or []))
        return tuple(values)

    def status(self) -> dict:
        return {
            "running": self._task is not None and not self._task.done(),
            "health_enabled": bool(self.settings.get("watch.enabled")),
            "health_available": bool(
                self.store and self.settings.get("perception.master") and self.settings.get("perception.activity")
            ),
            "screen_enabled": bool(self.settings.get("watch.screen_enabled")),
            "screen_available": bool(
                self.store and self.host and self.vision and self.frontmost
                and self.settings.get("perception.master") and self.settings.get("perception.app")
            ),
            "last_error": self._last_error,
        }

    async def _run(self) -> None:
        cadence = max(self.min_cadence, float(self.settings.get("watch.cadence", 60) or 60))
        behavior_settings = dict(self.settings)
        if not self.settings.get("watch.screen_enabled"):
            behavior_settings["watch.observe_apps"] = []
        budget = Budget(
            int(self.settings.get("watch.look_max_per_hour", 6)),
            int(self.settings.get("watch.look_max_per_day", 50)),
        )
        behaviors = build_behaviors(
            behavior_settings,
            host=self.host,
            vision=self.vision,
            budget=budget,
            emit=self.dispatcher.emit,
            frontmost=self.frontmost,
            ambient_state_path=os.path.join(config.data_dir(), "ambient_state.json"),
        )
        if not self.settings.get("watch.enabled"):
            behaviors = [behavior for behavior in behaviors if behavior.name == "proactive_chat"]
        while True:
            try:
                now = time.time()
                snap = self._snapshot(now, max_age=max(15.0, cadence * 2.5))
                ctx = WatchCtx(
                    settings=self.settings,
                    host=self.host,
                    vision=self.vision,
                    budget=budget,
                    emit=self.dispatcher.emit,
                    frontmost=self.frontmost,
                )
                for behavior in behaviors:
                    try:
                        event = behavior.tick(snap, ctx)
                        if event:
                            self.dispatcher.emit(event)
                    except Exception as exc:
                        self._last_error = f"{behavior.name}: {exc}"
                        log(f"watch 行为 {behavior.name} 报错（跳过）：{exc}")
                await asyncio.sleep(cadence)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = str(exc)
                log(f"watch tick 异常（继续）：{exc}")
                await asyncio.sleep(cadence)

    def _snapshot(self, now: float, *, max_age: float) -> WatchSnapshot:
        if callable(self.live_state):
            state = self.live_state() or {}
            sampled_at = state.get("sampled_at")
            try:
                fresh = sampled_at is not None and 0 <= now - float(sampled_at) <= max_age
            except (TypeError, ValueError):
                fresh = False
            snap = WatchSnapshot(now=now)
            if not fresh or not self.settings.get("perception.master", False):
                return snap
            if self.settings.get("perception.app", False):
                snap.app = str(state.get("app") or "") or None
                snap.app_id = str(state.get("app_id") or "") or None
            if self.settings.get("perception.activity", False) and state.get("activity") in {"active", "idle"}:
                started = float(state.get("activity_started_at") or sampled_at)
                snap.activity = {
                    "state": state["activity"],
                    "seconds": max(0.0, now - started),
                    "segment_id": started,
                }
            return snap
        # Compatibility path for tests/injected stores; settings still prevent stale disabled sources.
        return self.snapshot(self.store, now, settings=self.settings)
