"""watch 主动行为验收（v1.1 Slice 0）：事件经 ProactiveDispatcher 落真实 FeedStore。

与 test_watch.py（行为单测）/test_proactive.py（fake feed gating）分工：
这里验「触发 → dispatcher → 真实 SQLite 记账」端到端契约，feed 用真库。
"""
import asyncio

from yibao_brain.background_jobs import BackgroundJobManager
from yibao_brain.feed import FeedStore
from yibao_brain.proactive import ProactiveDispatcher
from yibao_brain.watch import HealthNudge, WatchCtx, WatchSnapshot


def test_health_nudge_event_lands_in_real_feed(tmp_path):
    """久坐事件：full 档 → feed 记账（无 task meta → kind=event）+ write_msg 亮 pet 面。"""

    async def run():
        feed = FeedStore(str(tmp_path / "feed.db"))
        messages = []
        dispatcher = ProactiveDispatcher(
            settings={"proactive.level": "full", "proactive_voice": False},
            feed=feed, write_msg=messages.append,
        )
        nudge = HealthNudge(idle_warn_minutes=45, quiet_hours="")
        snap = WatchSnapshot(
            now=1000, activity={"state": "active", "seconds": 46 * 60, "segment_id": 1}
        )
        event = nudge.tick(snap, WatchCtx())
        assert event and event["kind"] == "reminder"
        await dispatcher.dispatch(event)
        items = feed.recent()
        feed.close()
        assert items and items[0]["text"] == "坐久了，起来活动一下吧 🧘"
        assert items[0]["kind"] == "event"
        assert messages and messages[0]["event"]["level"] == "full"

    asyncio.run(run())


def test_watch_command_completion_reports_into_feed_via_thread_emit(tmp_path):
    """后台命令完成：manager 工作线程 → dispatcher.emit（call_soon_threadsafe）
    → dispatch → feed 记账。quiet 档只记账不打扰（write_msg 为空）。"""

    async def run():
        feed = FeedStore(str(tmp_path / "feed.db"))
        messages = []
        dispatcher = ProactiveDispatcher(
            settings={"proactive.level": "quiet"},
            feed=feed, write_msg=messages.append,
            loop=asyncio.get_running_loop(),
        )
        manager = BackgroundJobManager()
        try:
            job = manager.start("exit 0", cwd=str(tmp_path), name="验收", emit=dispatcher.emit)
            for _ in range(100):
                if manager.status(job["task_id"])["status"] != "running":
                    break
                await asyncio.sleep(0.05)
            await asyncio.sleep(0.2)  # 让线程安全提交的 dispatch 协程跑完
        finally:
            manager.shutdown()
        items = feed.recent()
        feed.close()
        assert any("「验收」完成" in item["text"] for item in items), items
        assert messages == []  # quiet 档：记账但不打扰

    asyncio.run(run())
