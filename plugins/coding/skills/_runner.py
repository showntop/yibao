"""AgentRunner：程序化驱动 coding agent 流式跑。v1 实装 ClaudeCodeRunner（claude-agent-sdk）。"""
from __future__ import annotations
import sys
from typing import Protocol, Callable, Any

_FILE_EDIT_TOOLS = {"Write", "Edit", "MultiEdit"}


def _deep_get(obj, path):
    """沿属性/键路径取值；任一步为 None 则返回 None。"""
    cur = obj
    for p in path:
        if cur is None:
            return None
        cur = getattr(cur, p, None) if not isinstance(cur, dict) else cur.get(p)
    return cur


def normalize(msg: Any) -> dict | None:
    """把 SDK 消息归一成 coding_event dict。duck-typed：按常见字段判别。

    返回 dict → runner 调 on_event；返回 None → 忽略本条。
    输入契约（FakeSDK 锁定）：
      - None（SDK 偶发空消息）→ None（忽略）
      - 文本类（.text 非空）→ {"kind":"text_delta","text":...}
      - 文件编辑工具（.tool ∈ {Write,Edit,MultiEdit} 或 .path 非空）→ {"kind":"file_edit","tool":...,"path":...}
      - 其余工具调用 → {"kind":"tool_use","tool":...}
    真实 SDK 消息（AssistantMessage/ToolUseBlock 等）字段形态在 C7 实装验收时微调，
    但本函数对 duck-typed 输入的判别契约由 test_coding_plugin.py 锁定。
    """
    if msg is None:
        return None
    tool = getattr(msg, "tool", None) or _deep_get(msg, ("tool", "name"))
    path = getattr(msg, "path", None) or _deep_get(msg, ("path",)) or _deep_get(msg, ("file_path",))
    if tool in _FILE_EDIT_TOOLS or path:
        return {"kind": "file_edit", "tool": tool, "path": path}
    text = getattr(msg, "text", None) or _deep_get(msg, ("text", "content"))
    if text:
        return {"kind": "text_delta", "text": str(text)}
    mtype = getattr(msg, "type", "")
    if mtype in ("result", "done"):
        return {"kind": "done"}
    # 其余（无关工具调用等）→ 归一成 tool_use（不丢信号）
    return {"kind": "tool_use", "tool": str(tool or mtype)}


class AgentRunner(Protocol):
    async def run(self, prompt: str, cwd: str, *,
                  on_event: Callable[[dict], None], cancel_event) -> None: ...


class ClaudeCodeRunner:
    """claude-agent-sdk 流式 runner。client_factory 可注入（测试用 fake，不触真 SDK）。"""

    def __init__(self, client_factory: Callable[..., Any] | None = None,
                 allowed_tools: list[str] | None = None):
        self._allowed_tools = allowed_tools or ["Read", "Write", "Edit", "MultiEdit", "Bash", "Glob", "Grep"]
        self._client_factory = client_factory  # None → 生产用真 SDK（lazy 导入）

    def _default_factory(self, cwd: str, tools: list[str]):
        from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions  # lazy：测试不依赖真 SDK
        options = ClaudeAgentOptions(cwd=cwd, permission_mode="acceptEdits", allowed_tools=tools)
        return ClaudeSDKClient(options=options)

    async def run(self, prompt: str, cwd: str, *, on_event, cancel_event) -> None:
        """流式跑 prompt；每条 SDK 消息 normalize 后 on_event；取消则早退；异常隔离成 error 事件。

        - 取消语义：在每条 SDK 消息前查 cancel_event.is_set() → True 则立即 return（不发 done）。
        - 容错语义：run 内任何异常 → on_event({"kind":"error","text":str(e)})，绝不向调用方抛。
        - 正常结束：on_event({"kind":"done"})。
        """
        factory = self._client_factory or self._default_factory
        try:
            client = factory(cwd, self._allowed_tools)
            async with client as c:
                await c.query(prompt)
                async for msg in c.receive_response():
                    if cancel_event.is_set():
                        return
                    ev = normalize(msg)
                    if ev is not None:
                        on_event(ev)
                        if ev.get("kind") == "done":
                            return
            on_event({"kind": "done"})
        except Exception as e:
            print(f"[yibao/coding] runner 失败：{e}", file=sys.stderr)
            on_event({"kind": "error", "text": str(e)})
