"""One delivery path for reminders, watch nudges and background-job events."""
from __future__ import annotations

import asyncio
import sys


def proactive_level(settings: dict) -> str:
    value = settings.get("proactive.level", "full")
    return value if value in {"quiet", "bubble", "full"} else "full"


class ProactiveDispatcher:
    def __init__(self, *, settings: dict, feed, write_msg, voice=None, run_state=None, loop=None):
        self.settings = settings
        self.feed = feed
        self.write_msg = write_msg
        self.voice = voice
        self.run_state = run_state or {"task": None}
        self.loop = loop

    def emit(self, event: dict) -> None:
        """Thread-safe entrypoint used by plugins, watch workers and background jobs."""
        loop = self.loop
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
        try:
            loop.call_soon_threadsafe(lambda: loop.create_task(self.dispatch(event)))
        except RuntimeError:
            pass  # event loop is already shutting down

    async def dispatch(self, event: dict) -> None:
        if not isinstance(event, dict):
            return
        text = str(event.get("text", ""))
        task_meta = event.get("task") if isinstance(event.get("task"), dict) else {}
        try:
            self.feed.add("task" if task_meta else "event", text, task_meta)
        except Exception:
            pass
        if event.get("kind") != "reminder":
            self.write_msg({"type": "event", "surface": None, "event": event})
            return
        level = proactive_level(self.settings)
        if level == "quiet":
            return
        delivered = {**event, "level": level}
        self.write_msg({"type": "event", "surface": "pet", "event": delivered})
        task = self.run_state.get("task")
        idle = task is None or task.done()
        if (
            level == "full"
            and self.voice is not None
            and self.settings.get("proactive_voice", True)
            and idle
            and text
        ):
            try:
                await asyncio.to_thread(self.voice.speak, text)
            except Exception as exc:
                print(f"[yibao] 主动播报失败：{exc}", file=sys.stderr)
