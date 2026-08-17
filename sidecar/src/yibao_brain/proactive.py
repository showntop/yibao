"""One delivery path for reminders, watch nudges and background-job events."""
from __future__ import annotations

import asyncio
import sys
import time


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

    def _downvotes(self, etype: str) -> int:
        """同类 24h 👎 数（feed 无计数方法的老 fake → 0，不降级）。"""
        counter = getattr(self.feed, "count_feedback_by_type", None)
        if not callable(counter):
            return 0
        try:
            return int(counter(etype, "down", time.time() - 86400))
        except Exception:
            return 0

    async def dispatch(self, event: dict) -> None:
        if not isinstance(event, dict):
            return
        if event.get("kind") == "panel_data":
            # 流式面板数据：直送 shell 转发对应 panel，绝不进 feed（否则每条 chunk 一条空 feed 记录）
            self.write_msg({"type": "event", "surface": None, "event": event})
            return
        if event.get("kind") in ("confirmation_needed", "action_result", "coding_sessions"):
            # 审批请求/裁决是确认条与收件箱的入出队信号：落 Feed 只剩噪音
            # （coding 每次审批两条且计 unread）；coding_sessions 是会话生命周期高频信号
            # （会话墙刷新触发源），同理不落账；仍照常广播 brain-event。
            self.write_msg({"type": "event", "surface": None, "event": event})
            return
        text = str(event.get("text", ""))
        task_meta = event.get("task") if isinstance(event.get("task"), dict) else {}
        try:
            meta = dict(task_meta)
            if event.get("type"):
                meta["type"] = event["type"]  # 信任仪表：反馈降频按 type 归组
            self.feed.add("task" if task_meta else "event", text, meta)
        except Exception:
            pass
        if event.get("kind") != "reminder":
            self.write_msg({"type": "event", "surface": None, "event": event})
            return
        etype = event.get("type")
        if etype and self._downvotes(etype) >= 2:
            return  # 同类 24h≥2 👎 → 降级 quiet（Feed 已记账，不弹不播）
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
