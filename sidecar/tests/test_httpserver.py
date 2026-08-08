"""httpserver 微 HTTP：ephemeral 端口真 socket 请求（同步测试 + asyncio.run，仓内无 pytest-asyncio）。"""
import asyncio
import json

from yibao_brain.httpserver import serve


def _run(coro):
    return asyncio.run(coro)  # 仿 test_server.py 的 _run_async 惯例


async def _request(port: int, raw: bytes) -> bytes:
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write(raw)
    await w.drain()
    data = await r.read(-1)
    w.close()
    return data


def test_options_preflight_204_with_cors():
    async def main():
        async def handler(m, p, h, b):
            return 200, {"ok": True}

        srv = await serve("127.0.0.1", 0, handler)
        port = srv.sockets[0].getsockname()[1]
        resp = await _request(port, b"OPTIONS /save HTTP/1.1\r\nHost: x\r\n\r\n")
        srv.close()
        head = resp.decode("latin-1")
        assert "204" in head.split("\r\n")[0]
        assert "Access-Control-Allow-Headers" in head
        assert "X-Yibao-Token" in head

    _run(main())


def test_post_json_passed_to_handler_and_json_response():
    seen = {}

    async def main():
        async def handler(m, p, h, b):
            seen.update({"method": m, "path": p, "token": h.get("x-yibao-token"), "body": b})
            return 200, {"ok": True, "echo": b.get("x")}

        srv = await serve("127.0.0.1", 0, handler)
        port = srv.sockets[0].getsockname()[1]
        body = json.dumps({"x": 42}).encode()
        raw = (b"POST /save HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
               b"X-Yibao-Token: t0\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
        resp = await _request(port, raw)
        srv.close()
        assert seen == {"method": "POST", "path": "/save", "token": "t0", "body": {"x": 42}}
        assert b'"ok":true' in resp.replace(b" ", b"")
        assert b'"echo":42' in resp.replace(b" ", b"")

    _run(main())


def test_bad_json_body_400():
    async def main():
        async def handler(m, p, h, b):
            return 200, {"ok": True}

        srv = await serve("127.0.0.1", 0, handler)
        port = srv.sockets[0].getsockname()[1]
        raw = b"POST /save HTTP/1.1\r\nHost: x\r\nContent-Length: 3\r\n\r\nnot"
        resp = await _request(port, raw)
        srv.close()
        assert "400" in resp.decode("latin-1").split("\r\n")[0]

    _run(main())


def test_post_without_content_length_gets_empty_body():
    seen = {}

    async def main():
        async def handler(m, p, h, b):
            seen["body"] = b
            return 200, {"ok": True}

        srv = await serve("127.0.0.1", 0, handler)
        port = srv.sockets[0].getsockname()[1]
        resp = await _request(port, b"POST /save HTTP/1.1\r\nHost: x\r\n\r\n")
        srv.close()
        assert seen["body"] == {}
        assert "200" in resp.decode("latin-1").split("\r\n")[0]

    _run(main())


def test_handler_exception_becomes_500():
    async def main():
        async def handler(m, p, h, b):
            raise RuntimeError("boom")

        srv = await serve("127.0.0.1", 0, handler)
        port = srv.sockets[0].getsockname()[1]
        resp = await _request(port, b"POST /save HTTP/1.1\r\nHost: x\r\nContent-Length: 2\r\n\r\n{}")
        srv.close()
        assert "500" in resp.decode("latin-1").split("\r\n")[0]

    _run(main())
