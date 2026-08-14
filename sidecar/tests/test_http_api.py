"""http_api（aiohttp HTTP 面）：EventTap / RateLimiter / build_app 单元测试。
函数级 + asyncio.run 风格（仓内无 pytest-asyncio）；路由级测试用 aiohttp TestServer。"""
import asyncio
import time

from aiohttp.test_utils import TestClient, TestServer

from yibao_brain.http_api import EventTap, MobileDeps, build_app


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


def _mkapp():
    return build_app(bridge_token="btok", mobile_token="mtok", tap=EventTap(lambda m: None))


def test_health_with_bridge_token():
    async def main():
        client = TestClient(TestServer(_mkapp()))
        await client.start_server()
        try:
            r = await client.get("/health", headers={"X-Yibao-Token": "btok"})
            assert r.status == 200 and (await r.json())["service"] == "yibao-bridge"
            r = await client.get("/v1/health", headers={"X-Yibao-Token": "mtok"})
            assert r.status == 200 and (await r.json())["service"] == "yibao"
        finally:
            await client.close()

    asyncio.run(main())


def test_tokens_are_isolated():
    async def main():
        client = TestClient(TestServer(_mkapp()))
        await client.start_server()
        try:
            # 扩展 token 打移动端 → 401（隔离）
            r = await client.get("/v1/health", headers={"X-Yibao-Token": "btok"})
            assert r.status == 401
            # 移动 token 打扩展桥 → 401
            r = await client.get("/health", headers={"X-Yibao-Token": "mtok"})
            assert r.status == 401
            # 移动端允许 token 走 query（EventSource 不能设 header）
            r = await client.get("/v1/health", params={"token": "mtok"})
            assert r.status == 200
        finally:
            await client.close()

    asyncio.run(main())


def test_v1_chat_and_interrupt_routes():
    async def main():
        calls = []

        def submit_run(text, conversation_id):
            calls.append((text, conversation_id))
            return {"ok": True, "run_id": "mob_1", "conversation_id": conversation_id}

        def interrupt():
            return True

        deps = MobileDeps(submit_run=submit_run, interrupt=interrupt)
        app = build_app(bridge_token="btok", mobile_token="mtok",
                        tap=EventTap(lambda m: None), deps=deps)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            r = await client.post("/v1/chat", headers={"X-Yibao-Token": "mtok"},
                                  json={"text": "你好", "conversation_id": "c1"})
            assert r.status == 200
            assert await r.json() == {"ok": True, "run_id": "mob_1", "conversation_id": "c1"}
            assert calls == [("你好", "c1")]
            r = await client.post("/v1/chat", headers={"X-Yibao-Token": "mtok"}, json={"text": "  "})
            assert r.status == 400
            r = await client.post("/v1/interrupt", headers={"X-Yibao-Token": "mtok"}, json={})
            assert r.status == 200 and (await r.json()) == {"ok": True, "interrupted": True}
        finally:
            await client.close()

    asyncio.run(main())


def test_auth_lockout_after_5_fails():
    async def main():
        from yibao_brain.http_api import RateLimiter

        app = build_app(bridge_token="btok", mobile_token="mtok",
                        tap=EventTap(lambda m: None), limiter=RateLimiter(fails=5, window=60, lock=60))
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            for _ in range(5):
                r = await client.get("/v1/health", headers={"X-Yibao-Token": "bad"})
                assert r.status == 401
            r = await client.get("/v1/health", headers={"X-Yibao-Token": "mtok"})  # 对 token 也被锁
            assert r.status == 429
        finally:
            await client.close()

    asyncio.run(main())


def test_v1_events_streams_frames_and_replays():
    async def main():
        tap = EventTap(lambda m: None)
        app = build_app(bridge_token="btok", mobile_token="mtok", tap=tap)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            resp = await client.get("/v1/events", params={"token": "mtok"})
            assert resp.status == 200
            assert resp.headers["Content-Type"].startswith("text/event-stream")
            tap.publish("chunk", {"text": "你好"})  # 已连接后发布
            line = await asyncio.wait_for(resp.content.readline(), 2)
            assert line == b"id: 1\n"
            event_line = await asyncio.wait_for(resp.content.readline(), 2)
            assert event_line == b"event: chunk\n"
            data_line = await asyncio.wait_for(resp.content.readline(), 2)
            assert data_line == 'data: {"text": "你好"}\n'.encode()
            empty_line = await asyncio.wait_for(resp.content.readline(), 2)
            assert empty_line == b"\n"
            resp.close()  # 断开（EventSource 会自动重连）

            tap.publish("chunk", {"text": "断线期间"})  # 断线期间继续发布
            resp2 = await client.get("/v1/events", params={"token": "mtok"},
                                     headers={"Last-Event-ID": "1"})
            line2 = await asyncio.wait_for(resp2.content.readline(), 2)
            assert line2 == b"id: 2\n"  # 从断点补发
            event_line2 = await asyncio.wait_for(resp2.content.readline(), 2)
            assert event_line2 == b"event: chunk\n"
            data_line2 = await asyncio.wait_for(resp2.content.readline(), 2)
            assert data_line2 == 'data: {"text": "断线期间"}\n'.encode()
            empty_line2 = await asyncio.wait_for(resp2.content.readline(), 2)
            assert empty_line2 == b"\n"
            resp2.close()
        finally:
            await client.close()

    asyncio.run(main())


def test_v1_events_requires_token():
    async def main():
        app = build_app(bridge_token="btok", mobile_token="mtok", tap=EventTap(lambda m: None))
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            r = await client.get("/v1/events")  # 无 token
            assert r.status == 401
        finally:
            await client.close()

    asyncio.run(main())

