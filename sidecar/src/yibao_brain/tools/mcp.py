"""MCP 适配器（P2）：运行时挂载 MCP server（stdio JSON-RPC），工具级翻译成 Tool。

设计（capability-unified-design spec §E McpManager / §J use_mcp）：
- 零新依赖：newline-delimited JSON-RPC over stdio 的最小 client；
- 台账：MCP server 一行（登记单位，mcp.<server>），工具展开（mcp.<server>.<tool>）；
- 路由暴露：MCP 工具注册为 plugin 归属「mcp.<server>」，与 use_plugin 共享 active 集合，
  经 use_mcp 展开后对 LLM 可见（openai_tools 按 active 过滤自动生效）；
- 安全：连接 = L3 确认（拉起外部进程）；工具调用默认 L2（远程执行，比本地 LLM 严格）。
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from ..ipc import ActionResult, RiskLevel
from .core import Tool

_MCP_PROTOCOL_VERSION = "2025-03-26"
_RPC_TIMEOUT = 15.0


class McpClient:
    """最小 MCP stdio client：initialize → tools/list → tools/call。每行一个 JSON-RPC 消息。"""

    def __init__(self, command: str, args: list[str] | None = None, env: dict | None = None) -> None:
        self._proc = subprocess.Popen(
            [command, *(args or [])],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, env=env, bufsize=1,
        )
        self._q: queue.Queue = queue.Queue()
        self._id = 0
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self._q.put(json.loads(line))
            except json.JSONDecodeError:
                continue

    def _request(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        rid = self._id
        req = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            req["params"] = params
        self._proc.stdin.write(json.dumps(req) + "\n")
        self._proc.stdin.flush()
        deadline = time.monotonic() + _RPC_TIMEOUT
        while time.monotonic() < deadline:
            try:
                msg = self._q.get(timeout=0.1)
            except queue.Empty:
                continue
            if msg.get("id") != rid:
                continue  # 跳过 notification 与无关响应
            if "error" in msg:
                raise RuntimeError(msg["error"].get("message", str(msg["error"])))
            return msg.get("result") or {}
        raise TimeoutError(f"MCP {method} 超时（{_RPC_TIMEOUT}s）")

    def initialize(self) -> dict:
        result = self._request(
            "initialize",
            {
                "protocolVersion": _MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "yibao", "version": "0.1.0"},
            },
        )
        # initialized 通知（无 id，服务器不响应）
        self._proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        )
        self._proc.stdin.flush()
        return result

    def list_tools(self) -> list[dict]:
        return self._request("tools/list").get("tools", [])

    def call_tool(self, name: str, arguments: dict) -> dict:
        return self._request("tools/call", {"name": name, "arguments": arguments or {}})

    def close(self) -> None:
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()


def _extract_text(result: dict) -> str:
    """tools/call 结果 → 人读文本（content 的 text 块；有 structuredContent 也带上）。"""
    parts: list[str] = []
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(item.get("text", ""))
    if result.get("structuredContent") is not None:
        parts.append(json.dumps(result["structuredContent"], ensure_ascii=False))
    return "\n".join(p for p in parts if p).strip() or json.dumps(result, ensure_ascii=False)


class McpTool(Tool):
    """MCP 工具的 Tool 包装：id = mcp.<server>.<tool>，run 转发 tools/call。"""

    def __init__(self, server: str, client: McpClient, spec: dict) -> None:
        name = spec["name"]
        self.id = f"mcp.{server}.{name}"
        self.label = f"MCP:{server}.{name}"
        self.description = spec.get("description") or f"MCP「{server}」的工具 {name}"
        self.default_risk = RiskLevel.L2_MEDIUM  # 远程执行，默认收紧
        self._client = client
        self._name = name
        self._schema = spec.get("inputSchema") or {}

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self._schema.get("properties") or {},
                    "required": self._schema.get("required") or [],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        try:
            result = self._client.call_tool(self._name, params)
        except Exception as e:
            return ActionResult(success=False, error=f"MCP {self._name} 执行失败：{e}")
        if result.get("isError"):
            return ActionResult(success=False, error=_extract_text(result) or f"MCP {self._name} 报错")
        text = _extract_text(result)
        return ActionResult(
            success=True,
            data={"text": text, "human": f"已调用 MCP 工具 {self._name}"},
        )


class McpManager:
    """MCP server 生命周期：配置持久化（data_dir()/mcp_servers.json）+ 连接/断开 + 工具注册。"""

    def __init__(self, servers_file: str, registry: "ToolRegistry", active: set) -> None:
        self._file = Path(servers_file)
        self._registry = registry
        self._active = active
        self._clients: dict[str, McpClient] = {}
        self._summaries: dict[str, dict] = {}  # server -> {name, description, tools: [...]}

    # ---------- 配置 ----------

    def _load_config(self) -> dict:
        if not self._file.is_file():
            return {}
        try:
            return json.loads(self._file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_config(self, cfg: dict) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._file.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    def add_server(self, name: str, command: str, args: list[str] | None = None,
                   env: dict | None = None) -> None:
        if not name or not name.replace("_", "").isalnum():
            raise ValueError("server 名须为字母数字/下划线")
        if not command or shutil.which(command) is None and not Path(command).expanduser().is_file():
            raise ValueError(f"命令不存在：{command}")
        cfg = self._load_config()
        cfg[name] = {"command": command, "args": args or [], "env": env or {}}
        self._save_config(cfg)

    # ---------- 连接生命周期 ----------

    def connect(self, name: str) -> list[str]:
        """启动 MCP server → initialize → 拉工具表 → 逐工具注册为 mcp.<server>.<tool>。"""
        if name in self._clients:
            raise ValueError(f"MCP server「{name}」已在连接")
        cfg = self._load_config().get(name)
        if not cfg:
            raise ValueError(f"未配置 MCP server：{name}（先用 mcp_add）")
        client = McpClient(cfg["command"], cfg.get("args"), env=cfg.get("env") or None)
        try:
            client.initialize()
            specs = client.list_tools()
        except Exception:
            client.close()
            raise
        if not specs:
            client.close()
            raise ValueError(f"MCP server「{name}」没有暴露任何工具")
        tool_ids: list[str] = []
        for spec in specs:
            tool = McpTool(name, client, spec)
            self._registry.register(tool, plugin=f"mcp.{name}")
            tool_ids.append(tool.id)
        self._clients[name] = client
        self._summaries[name] = {
            "name": name,
            "description": f"MCP server（{len(tool_ids)} 个工具）",
            "tools": [t.rsplit(".", 1)[1] for t in tool_ids],
        }
        return tool_ids

    def disconnect(self, name: str) -> int:
        """注销该 server 的工具并终止进程（配置保留，可再 connect）。"""
        removed = 0
        for tid in list(self._registry.plugin_tools().get(f"mcp.{name}", [])):
            self._registry.unregister(tid)
            removed += 1
        client = self._clients.pop(name, None)
        if client is not None:
            client.close()
        self._summaries.pop(name, None)
        self._active.discard(f"mcp.{name}")
        return removed

    # ---------- 查询 ----------

    def summaries(self) -> dict[str, dict]:
        """已连接的 server 摘要（use_mcp 路由枚举用）。"""
        return {k: dict(v) for k, v in self._summaries.items()}

    def is_disabled(self, name: str) -> bool:
        """该 server 是否被停用（tool_disable 后不可展开）。"""
        return self._registry.source_disabled(f"mcp.{name}")

    def remove_server(self, name: str) -> int:
        """卸载 server（tool_uninstall）：断开连接 + 从配置删除；返回注销工具数。"""
        removed = self.disconnect(name)
        cfg = self._load_config()
        if name in cfg:
            del cfg[name]
            self._save_config(cfg)
        return removed

    # ---- SourceManager 对齐（management.py 统一接口）----

    def discover(self) -> list[dict]:
        """配置里的 server → 台账记录（对齐 Plugin/SkillManager 的 discover）。"""
        from .management import SourceRecord

        out: list[SourceRecord] = []
        for name, c in self._load_config().items():
            tools = [t for t in self._registry.plugin_tools().get(f"mcp.{name}", [])]
            out.append(SourceRecord(
                id=f"mcp.{name}", source_type="mcp",
                source={"server": name, "command": c.get("command")},
                tools=tools,
            ))
        return out

    def update(self, name: str) -> str:
        """更新 server：断开重连（拉最新工具表）。"""
        self.disconnect(name)
        tool_ids = self.connect(name)
        return f"ok（{len(tool_ids)} 工具）"

    def status(self, name: str) -> str:
        return "active" if name in self._clients else "missing"

    def list_servers(self) -> list[dict]:
        cfg = self._load_config()
        out = []
        for name, c in cfg.items():
            out.append({
                "name": name,
                "command": c.get("command"),
                "connected": name in self._clients,
                "tools": len(self._summaries[name]["tools"]) if name in self._summaries else 0,
            })
        return out


# ---------- 底座工具（server.py 注册） ----------


class UseMcpTool(Tool):
    """路由式展开 MCP 服务器：工具默认隐藏，展开后立即可用（与 use_plugin 同语义）。"""

    id = "use_mcp"
    label = "展开 MCP 服务器"
    default_risk = RiskLevel.L0_READONLY

    def __init__(self, manager: McpManager, active: set) -> None:
        self._mgr = manager
        self._active = active

    def openai_schema(self) -> dict:
        servers = list(self._mgr.summaries())
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": (
                    "展开一个已连接的 MCP 服务器（它的工具默认隐藏以省上下文，展开后立即可用）。"
                    "用户请求需要 MCP 工具而工具列表里没有时，先调本工具再继续。"
                    f"已连接服务器：{', '.join(servers) or '（无——先 mcp_list/mcp_connect）'}。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "server": {"type": "string", "enum": servers or ["(无已连接 MCP)"],
                                   "description": "MCP server 名"},
                    },
                    "required": ["server"],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        name = str(params.get("server") or "").strip()
        if name not in self._mgr.summaries():
            return ActionResult(
                success=False,
                error=f"未连接这个 MCP server：{name or '(空)'}（已连接：{', '.join(self._mgr.summaries()) or '无'}）",
            )
        if self._mgr.is_disabled(name):  # 停用来源：拒绝展开
            return ActionResult(success=False, error=f"MCP「{name}」已被停用（先 tool_enable 再展开）")
        group = f"mcp.{name}"
        if group in self._active:
            return ActionResult(success=True, data={"server": name, "already": True,
                                                    "human": f"MCP「{name}」本来就是展开状态"})
        self._active.add(group)
        tools = self._mgr.summaries()[name]["tools"]
        return ActionResult(
            success=True,
            data={"server": name, "already": False, "tools": tools,
                  "human": f"已展开 MCP「{name}」，{len(tools)} 个工具可用"},
        )


class McpListTool(Tool):
    id = "mcp_list"
    label = "列出 MCP 服务器"
    default_risk = RiskLevel.L0_READONLY
    description = "列出已配置的 MCP 服务器（命令、连接态、工具数）。"

    def __init__(self, manager: McpManager) -> None:
        self._mgr = manager

    def run(self, params: dict, ctx: Any) -> ActionResult:
        servers = self._mgr.list_servers()
        human = "没有配置 MCP 服务器（用 mcp_add 添加）" if not servers else \
            "\n".join(f"- {s['name']}（{s['command']}）：{'已连接' if s['connected'] else '未连接'}，{s['tools']} 工具" for s in servers)
        return ActionResult(success=True, data={"servers": servers, "human": human})


class McpConnectTool(Tool):
    id = "mcp_connect"
    label = "连接 MCP 服务器"
    default_risk = RiskLevel.L3_HIGH
    description = "启动并连接一个已配置的 MCP 服务器，拉取其工具（默认隐藏，用 use_mcp 展开）。"

    def __init__(self, manager: McpManager) -> None:
        self._mgr = manager

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {"name": {"type": "string", "description": "MCP server 名"}},
                    "required": ["name"],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        name = str(params.get("name") or "").strip()
        try:
            tool_ids = self._mgr.connect(name)
        except Exception as e:
            return ActionResult(success=False, error=f"MCP「{name}」连接失败：{e}")
        return ActionResult(success=True, data={"server": name, "tools": tool_ids,
                                                "human": f"已连接 MCP「{name}」，{len(tool_ids)} 个工具（用 use_mcp 展开）"})


class McpDisconnectTool(Tool):
    id = "mcp_disconnect"
    label = "断开 MCP 服务器"
    default_risk = RiskLevel.L2_MEDIUM
    description = "断开一个已连接的 MCP 服务器：注销其工具并终止进程（配置保留）。"

    def __init__(self, manager: McpManager) -> None:
        self._mgr = manager

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {"name": {"type": "string", "description": "MCP server 名"}},
                    "required": ["name"],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        name = str(params.get("name") or "").strip()
        removed = self._mgr.disconnect(name)
        return ActionResult(success=True, data={"server": name, "removed": removed,
                                                "human": f"已断开 MCP「{name}」，注销 {removed} 个工具"})


class McpAddTool(Tool):
    id = "mcp_add"
    label = "添加 MCP 服务器"
    default_risk = RiskLevel.L3_HIGH
    description = "把一个新的 MCP 服务器写入配置（command 为启动命令，可选 args）。之后用 mcp_connect 连接。"

    def __init__(self, manager: McpManager) -> None:
        self._mgr = manager

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "server 名（字母数字/下划线）"},
                        "command": {"type": "string", "description": "启动 MCP server 的命令（可执行文件路径或 PATH 命令）"},
                        "args": {"type": "array", "items": {"type": "string"},
                                 "description": "可选：命令行参数"},
                    },
                    "required": ["name", "command"],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        name = str(params.get("name") or "").strip()
        command = str(params.get("command") or "").strip()
        args = [str(a) for a in (params.get("args") or [])]
        try:
            self._mgr.add_server(name, command, args)
        except Exception as e:
            return ActionResult(success=False, error=f"添加失败：{e}")
        return ActionResult(success=True, data={"server": name,
                                                "human": f"已配置 MCP server「{name}」（{command}），用 mcp_connect 连接"})
