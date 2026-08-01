import asyncio

from yibao_brain.proactive import ProactiveDispatcher


class _Feed:
    def __init__(self):
        self.items = []

    def add(self, kind, text, meta):
        self.items.append((kind, text, meta))


class _Voice:
    def __init__(self):
        self.spoken = []

    def speak(self, text):
        self.spoken.append(text)


def test_dispatcher_always_records_but_quiet_suppresses_ui_and_voice():
    async def run():
        feed, voice, messages = _Feed(), _Voice(), []
        dispatcher = ProactiveDispatcher(
            settings={"proactive.level": "quiet", "proactive_voice": True},
            feed=feed, write_msg=messages.append, voice=voice,
            run_state={"task": None},
        )
        await dispatcher.dispatch({"kind": "reminder", "text": "take a break"})
        assert feed.items and feed.items[0][1] == "take a break"
        assert messages == []
        assert voice.spoken == []

    asyncio.run(run())


def test_dispatcher_bubble_emits_without_voice_and_full_respects_busy_state():
    async def run():
        feed, voice, messages = _Feed(), _Voice(), []
        settings = {"proactive.level": "bubble", "proactive_voice": True}
        state = {"task": None}
        dispatcher = ProactiveDispatcher(
            settings=settings, feed=feed, write_msg=messages.append, voice=voice,
            run_state=state,
        )
        await dispatcher.dispatch({"kind": "reminder", "text": "bubble"})
        assert messages[-1]["event"]["level"] == "bubble"
        assert voice.spoken == []

        settings["proactive.level"] = "full"
        state["task"] = type("Busy", (), {"done": lambda self: False})()
        await dispatcher.dispatch({"kind": "reminder", "text": "busy"})
        assert voice.spoken == []
        state["task"] = None
        await dispatcher.dispatch({"kind": "reminder", "text": "speak"})
        assert voice.spoken == ["speak"]

    asyncio.run(run())


class _FeedWithFeedback(_Feed):
    def count_feedback_by_type(self, mtype, feedback, since):
        return sum(1 for (_k, _t, m) in self.items
                   if m.get("type") == mtype and m.get("feedback") == feedback)


def test_dispatcher_downgrades_when_type_got_two_downvotes():
    """同类 24h≥2 👎 → 本次 reminder 降级 quiet（Feed 仍记账，write_msg 不出）。"""
    async def run():
        feed, messages = _FeedWithFeedback(), []
        feed.add("reminder", "x", {"type": "health_nudge", "feedback": "down"})
        feed.add("reminder", "y", {"type": "health_nudge", "feedback": "down"})
        dispatcher = ProactiveDispatcher(
            settings={"proactive.level": "full"}, feed=feed, write_msg=messages.append,
        )
        await dispatcher.dispatch({"kind": "reminder", "text": "break", "type": "health_nudge"})
        assert messages == []                    # 降级 quiet：不弹不播
        assert feed.items[-1][1] == "break"      # 但仍记账
        assert feed.items[-1][2].get("type") == "health_nudge"  # meta.type 并入

    asyncio.run(run())


def test_dispatcher_below_threshold_delivers_normally():
    """只有 1 个 👎 → 正常按 level 投递。"""
    async def run():
        feed, messages = _FeedWithFeedback(), []
        feed.add("reminder", "x", {"type": "health_nudge", "feedback": "down"})
        dispatcher = ProactiveDispatcher(
            settings={"proactive.level": "bubble"}, feed=feed, write_msg=messages.append,
        )
        await dispatcher.dispatch({"kind": "reminder", "text": "break", "type": "health_nudge"})
        assert messages and messages[-1]["event"]["level"] == "bubble"

    asyncio.run(run())
