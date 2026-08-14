"""http_api（aiohttp HTTP 面）：EventTap / RateLimiter / build_app 单元测试。
函数级 + asyncio.run 风格（仓内无 pytest-asyncio）；路由级测试用 aiohttp TestServer。"""
import asyncio
import time

from yibao_brain.http_api import EventTap


def test_tap_passes_through_and_frames_event_and_run_done():
    out = []
    tap = EventTap(lambda m: out.append(m))
    tap({"type": "hello", "version": 1})  # 非 event/run_done：透传不进 SSE
    tap({"type": "event", "surface": "mobile", "event": {"kind": "final_reply_chunk", "text": "你好"}})
    tap({"type": "run_done", "id": "r1"})
    assert out[0] == {"type": "hello", "version": 1}  # stdio 原样照发
    frames = tap.replay(0)
    assert [f[1] for f in frames] == ["final_reply_chunk", "run_done"]
    assert frames[0][0] == 1 and frames[1][0] == 2  # seq 单调
    import json
    assert json.loads(frames[0][2])["kind"] == "final_reply_chunk"
    assert json.loads(frames[1][2]) == {"id": "r1"}


def test_tap_replay_from_last_seq_only():
    tap = EventTap(lambda m: None)
    for i in range(5):
        tap.publish("chunk", {"i": i})
    frames = tap.replay(3)  # 只要 seq>3
    assert [f[0] for f in frames] == [4, 5]


def test_tap_subscriber_gets_frames_and_slow_consumer_drops_oldest():
    async def main():
        tap = EventTap(lambda m: None)
        q = tap.subscribe()
        tap.publish("chunk", {"i": 1})
        assert (await q.get())[2]  # 收到帧
        tap.unsubscribe(q)
        tap.publish("chunk", {"i": 2})
        assert q.empty()  # 退订后不再收

        # 慢消费：小容量队列满后丢最旧保活（客户端靠 Last-Event-ID 重连补齐）
        small = EventTap(lambda m: None)
        sq = small.subscribe()
        small._subs.clear(); small._subs.add(sq)  # 复用订阅但直接压小容量
        # 直接构造满队列场景：容量 256 默认，改为手动灌满
        for _ in range(256):
            small.publish("chunk", {"x": 1})
        small.publish("chunk", {"x": 2})  # 第 257 帧 → 触发丢最旧
        assert sq.qsize() == 256  # 没炸、没断订阅

    asyncio.run(main())


def test_rate_limiter_locks_after_5_fails():
    from yibao_brain.http_api import RateLimiter

    rl = RateLimiter(fails=5, window=60.0, lock=60.0)
    for _ in range(4):
        assert rl.allow() is True
        rl.record_fail()
    assert rl.allow() is True  # 第 5 次尝试仍放行（失败后才锁）
    rl.record_fail()
    assert rl.allow() is False  # 已锁


def test_rate_limiter_lock_expires():
    from yibao_brain.http_api import RateLimiter

    rl = RateLimiter(fails=2, window=60.0, lock=0.05)
    rl.record_fail(); rl.record_fail()
    assert rl.allow() is False
    time.sleep(0.06)
    assert rl.allow() is True  # 锁过期
