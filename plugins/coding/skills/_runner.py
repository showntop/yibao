"""AgentRunner：程序化驱动 coding agent 流式跑。v1 实装 ClaudeCodeRunner（claude-agent-sdk）。"""
from __future__ import annotations
import json, sys
from typing import Protocol, Callable, Any

_FILE_EDIT_TOOLS = {"Write", "Edit", "MultiEdit"}


def normalize(msg: Any) -> list[dict]:
    """把一条 SDK 消息归一成 coding_event 列表（一条 AssistantMessage 可拆出多块 → 多事件）。

    优先匹配真 claude-agent-sdk（0.2.x）消息形态：
      - AssistantMessage（.content: list[ContentBlock]）→ 逐块归一：
          · TextBlock（.text）→ {"kind":"text_delta","text":...}
          · ToolUseBlock（.name+.input）→
              name ∈ {Write,Edit,MultiEdit} → {"kind":"file_edit","tool","path","old","new"}
              其余（Read/Bash/Glob/Grep…）        → {"kind":"tool_use","tool","input"}
      - ResultMessage（type 名含 "Result"，或 duck-typed .subtype+.is_error）→ [{"kind":"done"}]
      - SystemMessage / UserMessage / 未知 → []（v1 忽略）
    None → []。
    末尾保留 duck-typed 扁平 fallback（.text / .tool+.path / .type∈{result,done}），
    仅为兼容历史 trivial fake；真 SDK 走上面的分支。
    """
    if msg is None:
        return []
    mtype = type(msg).__name__
    # v1 显式忽略：用户/系统消息（SystemMessage 也有 .subtype，须先排除）
    if "User" in mtype or "System" in mtype:
        return []

    events: list[dict] = []
    content = getattr(msg, "content", None)
    # 1) AssistantMessage-like：.content 是块列表
    if isinstance(content, list):
        for block in content:
            ev = _normalize_block(block)
            if ev is not None:
                events.append(ev)
        return events

    # 2) ResultMessage-like：终态
    if "Result" in mtype or (hasattr(msg, "subtype") and hasattr(msg, "is_error")):
        return [{"kind": "done"}]

    # 3) duck-typed fallback（trivial fakes）
    ev = _normalize_flat(msg)
    return [ev] if ev is not None else []


def _normalize_block(block: Any) -> dict | None:
    """归一单个 ContentBlock（TextBlock / ToolUseBlock；其余返回 None）。"""
    if block is None:
        return None
    name = getattr(block, "name", None)
    inp = getattr(block, "input", None)
    if name is not None and inp is not None:
        if name in _FILE_EDIT_TOOLS:
            return _file_edit_event(name, inp)
        return {"kind": "tool_use", "tool": name, "input": inp}
    text = getattr(block, "text", None)
    if text is not None:
        return {"kind": "text_delta", "text": str(text)}
    return None


def _file_edit_event(tool: str, inp: Any) -> dict:
    """Write/Edit/MultiEdit 的 input → file_edit 事件（带 path/old/new）。"""
    d = inp if isinstance(inp, dict) else {}
    path = d.get("file_path") or d.get("path")
    if tool == "Edit":
        old = d.get("old_string")
        new = d.get("new_string")
    elif tool == "Write":
        old = None
        new = d.get("content")
    else:  # MultiEdit
        old = None
        new = json.dumps(d.get("edits"), ensure_ascii=False) if d.get("edits") is not None else None
    return {"kind": "file_edit", "tool": tool, "path": path, "old": old, "new": new}


def _normalize_flat(msg: Any) -> dict | None:
    """duck-typed fallback：扁平 .text / .tool+.path / .type∈{result,done}。

    不再凭空捏造 tool_use（旧实现的 "(未知工具)" 正是 v1 bug 来源）；
    无真实工具信号时返回 None。
    """
    tool = getattr(msg, "tool", None)
    path = getattr(msg, "path", None)
    if tool in _FILE_EDIT_TOOLS or path:
        return {"kind": "file_edit", "tool": tool, "path": path, "old": None, "new": None}
    text = getattr(msg, "text", None)
    if text:
        return {"kind": "text_delta", "text": str(text)}
    mtype = getattr(msg, "type", "")
    if mtype in ("result", "done"):
        return {"kind": "done"}
    return None


class AgentRunner(Protocol):
    async def run(self, prompt: str, cwd: str, *,
                  on_event: Callable[[dict], None], cancel_event,
                  resume_session_id: str | None = None) -> str | None: ...


class ClaudeCodeRunner:
    """claude-agent-sdk 流式 runner。client_factory 可注入（测试用 fake，不触真 SDK）。"""

    def __init__(self, client_factory: Callable[..., Any] | None = None,
                 allowed_tools: list[str] | None = None):
        self._allowed_tools = allowed_tools or ["Read", "Write", "Edit", "MultiEdit", "Bash", "Glob", "Grep"]
        self._client_factory = client_factory  # None → 生产用真 SDK（lazy 导入）

    def _default_factory(self, cwd: str, tools: list[str], resume: str | None = None):
        from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions  # lazy：测试不依赖真 SDK
        options = ClaudeAgentOptions(
            cwd=cwd, permission_mode="acceptEdits", allowed_tools=tools, resume=resume,
        )
        return ClaudeSDKClient(options=options)

    async def run(self, prompt: str, cwd: str, *, on_event, cancel_event,
                  resume_session_id: str | None = None) -> str | None:
        """流式跑 prompt；每条 SDK 消息 normalize 后 on_event；取消则早退；异常隔离成 error 事件。

        - resume_session_id：非 None 时透传 ClaudeAgentOptions.resume，续上同一 CC 会话历史。
        - cc_session_id 捕获：流中遇到 ResultMessage（duck-typed 带 .session_id）时缓存其值，
          run 结束返回（str | None）。失败/取消时返回 None。
        - 取消语义：在每条 SDK 消息前查 cancel_event.is_set() → True 则立即 return（不发 done）。
        - 容错语义：run 内任何异常 → on_event({"kind":"error","text":str(e)})，绝不向调用方抛。
        - 正常结束：on_event({"kind":"done"})。
        """
        factory = self._client_factory or self._default_factory
        cc_sid: str | None = None
        try:
            client = factory(cwd, self._allowed_tools, resume=resume_session_id)
            async with client as c:
                await c.query(prompt)
                async for msg in c.receive_response():
                    if cancel_event.is_set():
                        return None
                    # ResultMessage 携 session_id：先从原 msg 读，再 normalize
                    sid = getattr(msg, "session_id", None)
                    if sid:
                        cc_sid = sid
                    for ev in normalize(msg):
                        on_event(ev)
                        if ev.get("kind") == "done":
                            return cc_sid
            on_event({"kind": "done"})
            return cc_sid
        except Exception as e:
            print(f"[yibao/coding] runner 失败：{e}", file=sys.stderr)
            on_event({"kind": "error", "text": str(e)})
            return None
