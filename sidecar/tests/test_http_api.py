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
    assert json.loads(frames[0][2])["surface"] == "mobile"  # 信封字段并入帧 data（spec §4.3）
    assert json.loads(frames[1][2]) == {"id": "r1"}  # run_done 无信封字段时保持原样


def test_tap_event_frame_carries_envelope_fields():
    """经 tap 的 event 帧 data 并入信封字段（有才带）：客户端按 surface/conversation_id
    挑着渲染（spec §4.3）；run_done 分支同样并入；内层 event 缺省字段不造键。"""
    import json

    tap = EventTap(lambda m: None)
    tap({"type": "event", "surface": "pet", "conversation_id": "c7",
         "event": {"kind": "final_reply_chunk", "text": "hi"}})
    tap({"type": "run_done", "id": "r2", "surface": "mobile", "conversation_id": "c7"})
    tap({"type": "event", "event": {"kind": "thinking"}})  # 无信封：帧 data 不含这些键
    f1, f2, f3 = tap.replay(0)
    assert json.loads(f1[2]) == {"kind": "final_reply_chunk", "text": "hi",
                                 "surface": "pet", "conversation_id": "c7"}
    assert json.loads(f2[2]) == {"id": "r2", "surface": "mobile", "conversation_id": "c7"}
    d3 = json.loads(f3[2])
    assert d3 == {"kind": "thinking"}


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


def test_tap_close_sends_sentinel_and_publish_after_close_is_noop():
    """close 语义（进程退出路径）：订阅者收到 None 哨兵立即退流；close 后 publish
    不投递订阅者也不炸；close 幂等；stdio 透传（__call__）不受影响。"""
    async def main():
        stdio = []
        tap = EventTap(lambda m: stdio.append(m))
        q = tap.subscribe()
        tap.close()
        assert await q.get() is None  # 哨兵：SSE handler 据此立即收尾
        tap.close()  # 幂等：再关一次不炸、不再投哨兵
        assert q.empty()
        tap.publish("chunk", {"i": 9})  # close 后 publish：不投递订阅者、不炸
        assert q.empty()
        tap({"type": "event", "event": {"kind": "chunk", "text": "hi"}})  # stdio 透传照常
        assert stdio and stdio[0]["event"]["kind"] == "chunk"

        # 满队列也能收到哨兵：丢最旧保送达（exit 时慢消费者不得拖住收尾）
        small = EventTap(lambda m: None)
        sq = small.subscribe()
        for _ in range(256):
            small.publish("chunk", {"x": 1})
        small.close()
        drained = [sq.get_nowait() for _ in range(sq.qsize())]  # 排空到哨兵为止
        assert drained[-1] is None and None not in drained[:-1]

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


def test_v1_confirm_and_state_routes():
    async def main():
        def confirm(cid, approved, remember):
            return cid != "already-done"

        def state():
            return {"running": {"surface": "mobile"},
                    "pending": [{"id": "pa_1", "skill_id": "danger", "summary": "rm -rf", "risk": 3, "created_at": 1}]}

        deps = MobileDeps(confirm=confirm, state=state)
        app = build_app(bridge_token="btok", mobile_token="mtok",
                        tap=EventTap(lambda m: None), deps=deps)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            r = await client.post("/v1/confirm", headers={"X-Yibao-Token": "mtok"},
                                  json={"id": "pa_1", "approved": True, "remember": False})
            assert r.status == 200
            r = await client.post("/v1/confirm", headers={"X-Yibao-Token": "mtok"},
                                  json={"id": "already-done", "approved": True})
            assert r.status == 404  # 已处理/未知
            r = await client.post("/v1/confirm", headers={"X-Yibao-Token": "mtok"}, json={"id": ""})
            assert r.status == 400
            r = await client.get("/v1/state", headers={"X-Yibao-Token": "mtok"})
            body = await r.json()
            assert body["running"] == {"surface": "mobile"}
            assert body["pending"][0]["id"] == "pa_1"
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


def test_v1_push_register_route():
    """推送设备登记路由测试"""
    async def main():
        saved = []

        def register_push(rid, platform):
            saved.append((rid, platform))

        deps = MobileDeps(register_push=register_push)
        app = build_app(bridge_token="btok", mobile_token="mtok",
                        tap=EventTap(lambda m: None), deps=deps)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            r = await client.post("/v1/push/register", headers={"X-Yibao-Token": "mtok"},
                                  json={"registration_id": "jp-abc", "platform": "ios"})
            assert r.status == 200 and (await r.json())["ok"] is True
            assert saved == [("jp-abc", "ios")]
            r = await client.post("/v1/push/register", headers={"X-Yibao-Token": "mtok"},
                                  json={"registration_id": "", "platform": "ios"})
            assert r.status == 400
        finally:
            await client.close()

    asyncio.run(main())


def test_cors_preflight_204_without_token():
    async def main():
        app = build_app(bridge_token="btok", mobile_token="mtok", tap=EventTap(lambda m: None))
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            r = await client.options("/v1/chat", headers={
                "Origin": "capacitor://localhost",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type, x-yibao-token"})
            assert r.status == 204  # 预检无自定义 token 头，不能被 auth 拦
            assert r.headers["Access-Control-Allow-Origin"] == "capacitor://localhost"
            assert "x-yibao-token" in r.headers["Access-Control-Allow-Headers"].lower()
        finally:
            await client.close()

    asyncio.run(main())


def test_cors_reflects_on_auth_failure_and_allows_localhost_any_port():
    async def main():
        app = build_app(bridge_token="btok", mobile_token="mtok", tap=EventTap(lambda m: None))
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            r = await client.get("/v1/health", headers={"Origin": "http://localhost:5173", "X-Yibao-Token": "bad"})
            assert r.status == 401
            assert r.headers["Access-Control-Allow-Origin"] == "http://localhost:5173"  # 401 也要带（客户端要能读到状态码）
        finally:
            await client.close()

    asyncio.run(main())


def test_cors_foreign_origin_gets_nothing():
    async def main():
        app = build_app(bridge_token="btok", mobile_token="mtok", tap=EventTap(lambda m: None))
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            r = await client.get("/v1/health", headers={"Origin": "http://evil.com", "X-Yibao-Token": "mtok"})
            assert r.status == 200
            assert "Access-Control-Allow-Origin" not in r.headers
        finally:
            await client.close()

    asyncio.run(main())


def test_cors_reflects_on_sse_stream():
    """SSE 流式响应：handler 内 prepare 后头部即落网线，事后中间件 update 是 no-op——
    反射必须走 on_response_prepare（头部写出前触发），手机 EventSource 才能过 CORS。"""
    async def main():
        tap = EventTap(lambda m: None)
        app = build_app(bridge_token="btok", mobile_token="mtok", tap=tap)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            resp = await client.get("/v1/events", params={"token": "mtok"},
                                    headers={"Origin": "capacitor://localhost"})
            assert resp.status == 200
            assert resp.headers["Access-Control-Allow-Origin"] == "capacitor://localhost"
            assert resp.headers["Vary"] == "Origin"
            tap.publish("chunk", {"text": "hi"})
            line = await asyncio.wait_for(resp.content.readline(), 2)
            assert line == b"id: 1\n"  # 读到首帧即证明流可用
            resp.close()
        finally:
            await client.close()

    asyncio.run(main())



def test_cors_allows_private_lan_origin():
    """局域网体验：手机浏览器经 http://<本机私网IP>:5173（vite --host）访问要放行；
    公网 IP / 域名 origin 依旧拒绝。"""
    from yibao_brain.http_api import _cors_allow

    assert _cors_allow("http://192.168.1.23:5173") == "http://192.168.1.23:5173"
    assert _cors_allow("http://10.0.0.5:5173") == "http://10.0.0.5:5173"
    assert _cors_allow("http://172.16.0.8:5173") == "http://172.16.0.8:5173"
    assert _cors_allow("http://127.0.0.1:5173") == "http://127.0.0.1:5173"
    assert _cors_allow("http://8.8.8.8:5173") is None  # 公网 IP 不放行
    assert _cors_allow("https://evil.com") is None
    assert _cors_allow("http://not-an-ip.example.com:5173") is None  # 非私网主机名不放行
