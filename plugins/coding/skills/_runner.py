"""AgentRunner：程序化驱动 coding agent 流式跑。v1 实装 ClaudeCodeRunner（claude-agent-sdk）。"""
from __future__ import annotations
import asyncio, itertools, json, sys, threading
from typing import Protocol, Callable, Any

_FILE_EDIT_TOOLS = {"Write", "Edit", "MultiEdit"}

# can_use_tool 权限桥：rid → {"event": threading.Event, "allow": bool|None}。
# 回调（runner 线程的 asyncio loop）发 permission_request 后在 asyncio.to_thread 里等
# event.wait（不堵 loop）；面板审批卡按钮 → coding.decide（DecideSkill）写 allow + set。
_PERM: dict = {}
_perm_seq = itertools.count(1)


def release_pending_permissions(sid: str) -> int:
    """放行该会话所有挂起的权限等待（按 deny 收场），返回放行数。
    会话停止时调用：否则 cancel 要等权限 60s 超时才被消费，停止响应最长延迟 60s。
    等待方（make_permission_callback 的 _cb）被 set 唤醒后走 allow=False 分支，
    permission_done(deny) 照常发、注册表照常清——面板审批卡收敛「✗ 已拒绝」。"""
    n = 0
    prefix = f"perm_{sid}_"
    for rid, entry in list(_PERM.items()):
        if rid.startswith(prefix) and entry.get("allow") is None:
            entry["allow"] = False
            entry["event"].set()
            n += 1
    return n


def _summarize_tool_input(tool_name, input) -> str:
    """审批摘要：Bash→command；文件工具→file_path/path；其余→json。单行截 80 字。"""
    d = input if isinstance(input, dict) else {}
    text = d.get("command") or d.get("file_path") or d.get("path") or json.dumps(d, ensure_ascii=False)
    return str(text).replace("\n", " ").strip()[:80]


def _public_params(input) -> dict:
    """确认卡展示的公开参数（前端只读与决策有关的字段）：command/file_path/path 取其一，截 200 字。"""
    d = input if isinstance(input, dict) else {}
    for k in ("command", "file_path", "path"):
        if d.get(k):
            return {k: str(d[k])[:200]}
    return {}


def _emit_perm_confirmation(emit_event, rid: str, tool_name, input) -> None:
    """L2 确认体系入队：confirmation_needed（actions 攒批载荷，格式对齐 loop.py 攒批事件；
    action.id=rid，confirm_batched 按 rid 路由兑现）。emit 异常吞掉——面板
    permission_request 通道仍在，审批链不受影响。"""
    if emit_event is None:
        return
    label = str(tool_name)
    action = {
        "id": rid,
        "skill_id": "coding",
        "label": label,
        "description": _summarize_tool_input(tool_name, input) or label,
        "params": _public_params(input),
        "risk": 1,
        "surface": "panel:coding",
    }
    try:
        emit_event({"kind": "confirmation_needed",
                    "text": f"编码会话等待审批：{label}",
                    "actions": [action], "action": action, "confirmation_id": rid})
    except Exception:
        pass


def _emit_perm_outcome(emit_event, rid: str, tool_name, outcome: str) -> None:
    """裁决结局广播 action_result：前端确认条/收件箱按 action.id 出队（brain.ts 约定）。
    outcome ∈ allow/deny/timeout；stop 放行（release_pending_permissions，deny 收场）时
    由等待方 _cb 走到这里，同样补出队。emit 异常吞掉（出队失败只留卡片，不伤审批链）。"""
    if emit_event is None:
        return
    text = {"allow": "已允许", "deny": "已拒绝", "timeout": "超时未批准"}[outcome]
    try:
        emit_event({"kind": "action_result",
                    "text": f"编码审批{text}：{tool_name}",
                    "action": {"id": rid, "skill_id": "coding", "label": str(tool_name)},
                    "result": {"success": outcome == "allow",
                               "error": "" if outcome == "allow" else text}})
    except Exception:
        pass


def make_permission_callback(sid: str, on_event, *, timeout_s: float = 60.0, emit_event=None):
    """can_use_tool 回调桥：向面板发 permission_request，并（emit_event 非 None 时）发
    L2 confirmation_needed 统一进确认体系；等 _PERM[rid] 被任一通道兑现——
    server confirm_batched 按 "perm_" 前缀路由直写 _PERM，coding.decide 备用，
    双通道幂等先到先得；超时默认 deny。
    请求发送失败 → deny；等待被取消/中断 → deny（fail-closed）；注册表清理、
    permission_done 与 action_result 出队事件在任何结局下都保证执行
    （emit 自身异常不再穿透回 SDK）。返回 SDK 期望的 async callable。"""
    async def _cb(tool_name, input, context=None):
        rid = f"perm_{sid}_{next(_perm_seq)}"
        entry = {"event": threading.Event(), "allow": None,
                 "tool": str(tool_name), "summary": _summarize_tool_input(tool_name, input),
                 "params": _public_params(input)}   # review 栏快照源（coding.perm_pending 直读）
        _PERM[rid] = entry
        try:
            on_event({"kind": "permission_request", "rid": rid,
                      "tool": str(tool_name), "input": input if isinstance(input, dict) else {}})
        except Exception as e:
            print(f"[yibao/coding] 权限请求事件发送失败（deny）：{e}", file=sys.stderr)
            _PERM.pop(rid, None)
            return _deny(f"请求发送失败：{e}")
        _emit_perm_confirmation(emit_event, rid, tool_name, input)
        try:
            got = await asyncio.to_thread(entry["event"].wait, timeout_s)
        except BaseException:  # 取消/中断穿透（含 CancelledError，BaseException 系）：按拒绝收场（fail-closed），清理照做
            got = False
        allow = entry["allow"] if got else None
        _PERM.pop(rid, None)
        outcome = "allow" if allow is True else ("deny" if allow is False else "timeout")
        try:
            on_event({"kind": "permission_done", "rid": rid, "allow": allow is True})
        except Exception:
            pass  # 面板流已断：deny 照返，不再多错
        _emit_perm_outcome(emit_event, rid, tool_name, outcome)
        if allow is True:
            return _allow()
        return _deny("用户拒绝" if allow is False else "超时未批准")
    return _cb


def _allow():
    from claude_agent_sdk import PermissionResultAllow  # lazy：与 _default_factory 同款，测试不依赖真 SDK 顶层
    return PermissionResultAllow()


def _deny(message: str):
    from claude_agent_sdk import PermissionResultDeny   # lazy：同上
    return PermissionResultDeny(message=message)


def normalize(msg: Any) -> list[dict]:
    """把一条 SDK 消息归一成 coding_event 列表（一条 AssistantMessage 可拆出多块 → 多事件）。

    优先匹配真 claude-agent-sdk（0.2.x）消息形态：
      - AssistantMessage（.content: list[ContentBlock]）→ 逐块归一：
          · ThinkingBlock（.thinking）→ {"kind":"thinking","text":...}（截 500 字）
          · TextBlock（.text）→ {"kind":"text_delta","text":...}
          · ToolUseBlock（.name+.input）→
              name ∈ {Write,Edit,MultiEdit} → {"kind":"file_edit","tool","path","old","new"}
              其余（Read/Bash/Glob/Grep…）        → {"kind":"tool_use","tool","input"}
      - ResultMessage（type 名含 "Result"，或 duck-typed .subtype+.is_error）→
          [{"kind":"done","usage":{...}}]（duration_ms/total_cost_usd/usage 鸭子类型，拿不到就空 dict 降级）
      - UserMessage（类名含 "User"）→ 先 _user_text 提取纯文本（replay-user-messages 回流的
          用户消息，str content 或 text 块列表）→ {"kind":"user_msg","uuid","text"}；
          空则回退 ToolResultBlock 逐块提取 → {"kind":"tool_result","text","is_error"}
      - SystemMessage / 未知 → []（忽略）
    None → []。
    末尾保留 duck-typed 扁平 fallback（.text / .tool+.path / .type∈{result,done}），
    仅为兼容历史 trivial fake；真 SDK 走上面的分支。
    """
    if msg is None:
        return []
    mtype = type(msg).__name__
    # UserMessage：提取 tool_result（工具输出可见是透明底线）；SystemMessage 仍忽略
    # （SystemMessage 也有 .subtype，须在 ResultMessage 判定前排除）
    if "User" in mtype:
        text = _user_text(msg)
        if text:
            return [{"kind": "user_msg", "uuid": str(getattr(msg, "uuid", "") or ""), "text": text}]
        return _tool_result_events(msg)
    if "System" in mtype:
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

    # 2) ResultMessage-like：终态（usage 鸭子类型，拿不到就空 dict 降级）
    if "Result" in mtype or (hasattr(msg, "subtype") and hasattr(msg, "is_error")):
        usage: dict = {}
        for src, dst in (("duration_ms", "duration_ms"), ("total_cost_usd", "cost_usd")):
            v = getattr(msg, src, None)
            if v is not None:
                usage[dst] = v
        raw = getattr(msg, "usage", None)
        if isinstance(raw, dict):
            for k in ("input_tokens", "output_tokens"):
                if raw.get(k) is not None:
                    usage[k] = raw[k]
        return [{"kind": "done", "usage": usage}]

    # 3) duck-typed fallback（trivial fakes）
    ev = _normalize_flat(msg)
    return [ev] if ev is not None else []


def _normalize_block(block: Any) -> dict | None:
    """归一单个 ContentBlock（ThinkingBlock / TextBlock / ToolUseBlock；其余返回 None）。"""
    if block is None:
        return None
    btype = type(block).__name__
    # ThinkingBlock 先于 name/input 判断（它既没有 name/input，鸭子类型也要兜住 type=="thinking"）
    if "Thinking" in btype or getattr(block, "type", None) == "thinking":
        thinking = getattr(block, "thinking", None) or getattr(block, "text", "")
        return {"kind": "thinking", "text": str(thinking)[:500]}
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


def _user_text(msg: Any) -> str:
    """UserMessage 的纯文本提取（str content 或 text 块列表）；空 → 走 tool_result 分支。

    只取 `getattr(b, "text")`：ToolResultBlock 的内容在 .content 而非 .text，不会误伤。
    """
    content = getattr(msg, "content", None)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [str(getattr(b, "text", "") or "") for b in content]
        return "\n".join(p for p in parts if p).strip()
    return ""


def _tool_result_events(msg: Any) -> list[dict]:
    """UserMessage 的 ToolResultBlock → tool_result 事件（截 800 字；content 为 str 或块列表，鸭子类型）。"""
    content = getattr(msg, "content", None)
    if not isinstance(content, list):
        return []
    out: list[dict] = []
    for block in content:
        is_err = bool(getattr(block, "is_error", False))
        bc = getattr(block, "content", None)
        if bc is None:
            continue
        if isinstance(bc, list):  # 块列表：拼 text 段
            text = "".join(str(getattr(b, "text", "")) for b in bc)
        else:
            text = str(bc)
        out.append({"kind": "tool_result", "text": text[:800], "is_error": is_err})
    return out


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
                  resume_session_id: str | None = None,
                  permission_mode: str = "acceptEdits", can_use_tool=None,
                  session_entry: dict | None = None) -> str | None: ...


class ClaudeCodeRunner:
    """claude-agent-sdk 流式 runner。client_factory 可注入（测试用 fake，不触真 SDK）。"""

    def __init__(self, client_factory: Callable[..., Any] | None = None,
                 allowed_tools: list[str] | None = None):
        self._allowed_tools = allowed_tools or ["Read", "Write", "Edit", "MultiEdit", "Bash", "Glob", "Grep"]
        self._client_factory = client_factory  # None → 生产用真 SDK（lazy 导入）

    def _default_factory(self, cwd: str, tools: list[str], resume: str | None = None,
                         permission_mode: str = "acceptEdits", can_use_tool=None):
        from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions  # lazy：测试不依赖真 SDK
        options = ClaudeAgentOptions(
            cwd=cwd, permission_mode=permission_mode, allowed_tools=tools, resume=resume,
            enable_file_checkpointing=True,            # rewind：CLI 侧文件检查点
            extra_args={"replay-user-messages": None},  # rewind：流中回 UserMessage（带 uuid 作回滚锚点）
            can_use_tool=can_use_tool,
        )
        return ClaudeSDKClient(options=options)

    async def run(self, prompt: str, cwd: str, *, on_event, cancel_event,
                  resume_session_id: str | None = None,
                  permission_mode: str = "acceptEdits", can_use_tool=None,
                  session_entry: dict | None = None) -> str | None:
        """流式跑 prompt；每条 SDK 消息 normalize 后 on_event；取消则发 stopped 终态后早退；异常隔离成 error 事件。

        - resume_session_id：非 None 时透传 ClaudeAgentOptions.resume，续上同一 CC 会话历史。
        - permission_mode：透传 ClaudeAgentOptions.permission_mode（如 acceptEdits/plan）。
        - can_use_tool：透传 ClaudeAgentOptions.can_use_tool 权限回调（None = SDK 默认）。
        - session_entry：live 会话 entry（coding.py `_SESSIONS[sid]`）；每条消息前检查并
          弹出 `mode_pending`（coding.mode 写入）→ client.set_permission_mode 运行中切换；
          同样弹出 `rewind_pending`（coding.rewind 写入）→ client.rewind_files 回滚文件检查点，
          成功发 rewind_ok、失败发 error 事件（鸭子类型，client 无此方法或调用失败均跳过，延迟 ≤1 条消息）。
        - cc_session_id 捕获：流中遇到 ResultMessage（duck-typed 带 .session_id）时缓存其值，
          run 结束返回（str | None）。失败时返回 None；取消时返回已捕获的 cc_sid。
        - 取消语义：在每条 SDK 消息前查 cancel_event.is_set() → True 则先 client.interrupt()
          真杀后台工具（旧行为只停读，后台还在跑；interrupt 鸭子类型，缺失/失败静默），
          再发 stopped 终态后立即 return（不发 done）。
        - 容错语义：run 内任何异常 → on_event({"kind":"error","text":str(e)})，绝不向调用方抛。
        - 正常结束：on_event({"kind":"done"})。
        """
        factory = self._client_factory or self._default_factory
        cc_sid: str | None = None
        try:
            client = factory(cwd, self._allowed_tools, resume=resume_session_id,
                             permission_mode=permission_mode, can_use_tool=can_use_tool)
            async with client as c:
                await c.query(prompt)
                async for msg in c.receive_response():
                    if cancel_event.is_set():
                        # 先 interrupt 真杀后台工具，再发 stopped 终态（否则面板永远停「运行中」、按钮锁死）
                        interrupt = getattr(c, "interrupt", None)
                        if interrupt is not None:
                            try:
                                await interrupt()
                            except Exception:
                                pass
                        on_event({"kind": "stopped", "text": "已中断"})
                        return cc_sid
                    # 运行中模式切换（coding.mode 写入 _SESSIONS mode_pending；下条消息生效，延迟 ≤1 条）
                    if session_entry is not None:
                        pending = session_entry.pop("mode_pending", None)
                        if pending is not None:
                            set_mode = getattr(c, "set_permission_mode", None)
                            if set_mode is not None:
                                try:
                                    await set_mode(pending)
                                except Exception as e:
                                    print(f"[yibao/coding] 运行中切换模式失败（已跳过）：{e}", file=sys.stderr)
                    # 运行中回滚（coding.rewind 写入 _SESSIONS rewind_pending；下条消息前执行 rewind_files）
                    if session_entry is not None:
                        rew = session_entry.pop("rewind_pending", None)
                        if rew is not None:
                            rewind_files = getattr(c, "rewind_files", None)
                            if rewind_files is not None:
                                try:
                                    await rewind_files(rew)
                                    on_event({"kind": "rewind_ok", "text": "已回滚到此前的文件状态"})
                                except Exception as e:
                                    on_event({"kind": "error", "text": f"回滚失败：{e}"})
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
