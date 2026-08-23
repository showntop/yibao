"""MCP 适配器（P2）：stdio JSON-RPC client + 工具翻译 + use_mcp 展开 + 生命周期。

mock server 是一个 newline-delimited JSON-RPC 子进程（python -c），
走真实 McpClient 的 stdin/stdout 通道，验证完整链路。
"""
import sys

import pytest

from yibao_brain.tools.mcp import McpManager, UseMcpTool
from yibao_brain.tools import ToolRegistry

MOCK_SERVER = r'''
import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    m = msg.get("method")
    rid = msg.get("id")
    if m == "initialize":
        print(json.dumps({"jsonrpc":"2.0","id":rid,"result":{"protocolVersion":"2025-03-26","capabilities":{"tools":{}},"serverInfo":{"name":"mock","version":"1"}}}), flush=True)
    elif m == "notifications/initialized":
        continue
    elif m == "tools/list":
        print(json.dumps({"jsonrpc":"2.0","id":rid,"result":{"tools":[
            {"name":"hello","description":"向 who 打招呼","inputSchema":{"type":"object","properties":{"who":{"type":"string"}},"required":["who"]}},
            {"name":"boom","description":"故意失败","inputSchema":{"type":"object","properties":{}}}
        ]}}), flush=True)
    elif m == "tools/call":
        args = msg["params"]["arguments"] or {}
        if msg["params"]["name"] == "boom":
            print(json.dumps({"jsonrpc":"2.0","id":rid,"result":{"isError":True,"content":[{"type":"text","text":"boom failed"}]}}), flush=True)
        else:
            print(json.dumps({"jsonrpc":"2.0","id":rid,"result":{"content":[{"type":"text","text":"hello " + args.get("who","world")}]}}), flush=True)
'''


@pytest.fixture
def env(tmp_path):
    reg = ToolRegistry()
    active = set()
    mgr = McpManager(str(tmp_path / "mcp_servers.json"), reg, active)
    mgr.add_server("mock", sys.executable, ["-c", MOCK_SERVER])
    return reg, active, mgr


def test_mcp_connect_registers_tools(env):
    reg, active, mgr = env
    tool_ids = mgr.connect("mock")
    assert "mcp.mock.hello" in tool_ids
    assert "mcp.mock.boom" in tool_ids
    assert reg.get("mcp.mock.hello") is not None
    assert "mcp.mock" not in active  # 默认隐藏，未展开


def test_mcp_call_tool_returns_text(env):
    reg, _, mgr = env
    mgr.connect("mock")
    res = reg.get("mcp.mock.hello").run({"who": "yibao"}, None)
    assert res.success
    assert res.data["text"] == "hello yibao"


def test_mcp_call_tool_error_flag(env):
    reg, _, mgr = env
    mgr.connect("mock")
    res = reg.get("mcp.mock.boom").run({}, None)
    assert not res.success
    assert "boom failed" in res.error


def test_use_mcp_expands_then_visible(env):
    reg, active, mgr = env
    mgr.connect("mock")
    res = UseMcpTool(mgr, active).run({"server": "mock"}, None)
    assert res.success
    assert "mcp.mock" in active
    names = {s["function"]["name"] for s in reg.openai_tools(active_plugins=active)}
    assert "mcp_mock_hello" in names  # llm_name：点号转下划线


def test_mcp_disconnect_unregisters_but_keeps_config(env):
    reg, active, mgr = env
    mgr.connect("mock")
    assert reg.get("mcp.mock.hello") is not None
    removed = mgr.disconnect("mock")
    assert removed == 2
    assert "mcp.mock.hello" not in [t.id for t in reg.list()]
    assert "mcp.mock" not in active
    mgr.connect("mock")  # 配置保留，可重连
    assert reg.get("mcp.mock.hello") is not None


def test_mcp_list_shows_config_and_state(env):
    _, _, mgr = env
    rows = mgr.list_servers()
    assert rows[0]["name"] == "mock"
    assert rows[0]["connected"] is False
    mgr.connect("mock")
    rows2 = mgr.list_servers()
    assert rows2[0]["connected"] is True
    assert rows2[0]["tools"] == 2
