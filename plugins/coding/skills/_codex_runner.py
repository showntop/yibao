"""CodexCliRunner：codex CLI（codex exec --json）子进程驱动，AgentRunner 第二实现。

与 ClaudeCodeRunner（SDK 进程内流）的关键差异：
- 传输是子进程 stdout JSONL：`codex exec --json -s <sandbox> -C <cwd> --skip-git-repo-check -`
  （prompt 走 stdin，`-` 触发，防 argv 长度上限/特殊字符转义）；
- resume 走子命令 `codex exec resume <thread_id> --json --skip-git-repo-check
  -c sandbox_mode=<sandbox> -`（resume 无 -s/-C flag——cwd 随 thread 落库还原，
  sandbox 只能经 -c 配置覆盖；实测 0.137.0 resume --help）；
- usage 是 thread 累计值（turn.completed.usage.input_tokens/output_tokens，另有
  cached_input_tokens 不参与计费差分）→ session_entry["usage_baseline"] 差分；
- 取消 = SIGTERM（3s 宽限）→ SIGKILL；发 stopped 不发 done（对齐 CC 语义）；
- headless 无运行中审批钩子（approval 恒 never）：can_use_tool/mode_pending/
  rewind_pending 三个协议参数收下但忽略（mode 下轮 run 生效——SendSkill 读库 mode
  传入新 sandbox；L2 确认条对 Codex 会话天然不触发，符合调研降级预期）。

事件字段名以 0.137.0 实测 + 二进制符号对照为准：
- thread.started → {thread_id}（实测）；turn.started（实测）；error → {message}（实测）；
  turn.failed → {error:{message}}（实测）；
- turn.completed → {usage:{input_tokens,cached_input_tokens,output_tokens} 累计值}
  （二进制字段名；账号限额未实测到成功 turn）；
- item.started/updated/completed → {item:{id,type,...}}：item 判别键是 "type"
  （二进制中无 item_type 字符串）；item.type ∈ agent_message/reasoning/
  command_execution/file_change/mcp_tool_call/web_search/todo_list。
"""
from __future__ import annotations
import asyncio, json, sys, time
from typing import Any, Callable

# permission_mode → codex sandbox（plan=只读规划 / acceptEdits=工作区可写；未知按可写兜）
_SANDBOX_BY_MODE = {"plan": "read-only", "acceptEdits": "workspace-write"}

_KILL_GRACE_S = 3.0   # cancel 后 SIGTERM → SIGKILL 宽限（对齐 CC cancel 尽快真杀的语义）


def _change_paths(changes: Any) -> list[str]:
    """file_change 的 changes → 路径列表。0.137.0 形态 {path: {type:add/delete/update}}；
    容错 list 形态（取每项 path 键）。拿不到 → []。"""
    if isinstance(changes, dict):
        return [str(p) for p in changes.keys()]
    if isinstance(changes, list):
        out = []
        for c in changes:
            if isinstance(c, dict) and c.get("path"):
                out.append(str(c["path"]))
        return out
    return []


def item_events(phase: str, item: Any) -> list[dict]:
    """item.started/updated/completed 的 item → coding 事件列表（载荷逐字对齐 _runner.py 清单）。

    phase ∈ started/updated/completed。流式增量 v1 不做 diff：文本类只取 completed
    全量一条（体感=整段到达，留档）；command_execution started/completed 各出一条
    （工具卡开场+结局）；其余工具类只取 completed。
    """
    if not isinstance(item, dict):
        return []
    itype = item.get("type")
    if itype == "agent_message":
        if phase == "completed":
            text = str(item.get("text") or "")
            return [{"kind": "text_delta", "text": text}] if text else []
    elif itype == "reasoning":
        if phase == "completed":
            text = str(item.get("text") or "")[:500]
            return [{"kind": "thinking", "text": text}] if text else []
    elif itype == "command_execution":
        if phase == "started":
            return [{"kind": "tool_use", "tool": "Bash",
                     "input": {"command": str(item.get("command") or "")}}]
        if phase == "completed":
            exit_code = item.get("exit_code")
            return [{"kind": "tool_result",
                     "text": str(item.get("aggregated_output") or "")[:800],
                     "is_error": exit_code not in (0, None)}]
    elif itype == "file_change":
        if phase == "completed":
            # 无 diff 内容可展示（item 不带 old/new）→ 卡片只显示路径，old/new 降级 None；
            # 多文件逐张发（一张卡一个路径，对齐 CC 单文件事件粒度）
            return [{"kind": "file_edit", "tool": "Edit", "path": p, "old": None, "new": None}
                    for p in _change_paths(item.get("changes"))]
    elif itype == "mcp_tool_call":
        if phase == "completed":
            server = str(item.get("server") or "")
            tool = str(item.get("tool") or item.get("tool_name") or "")
            name = f"{server}.{tool}" if server and tool else (tool or server or "mcp")
            args = item.get("arguments")
            return [{"kind": "tool_use", "tool": name,
                     "input": args if isinstance(args, dict) else {"raw": args}}]
    elif itype == "web_search":
        if phase == "completed":
            return [{"kind": "tool_use", "tool": "WebSearch",
                     "input": {"query": str(item.get("query") or "")}}]
    elif itype == "todo_list":
        if phase == "completed":
            todos = []
            for t in item.get("items") or []:
                if isinstance(t, dict):
                    todos.append({"content": str(t.get("text") or ""),
                                  "status": "completed" if t.get("completed") else "pending"})
                else:
                    todos.append({"content": str(t), "status": "pending"})
            # 对齐 CC TodoWrite 卡片消费的 input.todos 形态（content/status）
            return [{"kind": "tool_use", "tool": "TodoWrite", "input": {"todos": todos}}] if todos else []
    return []


def normalize_event(obj: Any) -> list[dict]:
    """一行 codex exec --json JSONL → coding 事件列表（0..n 条）。

    只覆盖纯映射（无状态）事件；thread.started/turn.completed 由 runner 自己处理
    （要捕获 thread_id / 做 usage 差分）。非 dict/未知类型 → []。
    """
    if not isinstance(obj, dict):
        return []
    etype = obj.get("type")
    if etype in ("item.started", "item.updated", "item.completed"):
        return item_events(etype.rsplit(".", 1)[1], obj.get("item"))
    if etype == "turn.failed":
        err = obj.get("error")
        msg = err.get("message") if isinstance(err, dict) else None
        return [{"kind": "error", "text": str(msg or "codex turn 失败")}]
    if etype == "error":
        return [{"kind": "error", "text": str(obj.get("message") or "codex 错误")}]
    return []


def _diff_usage(usage_raw: Any, session_entry: dict | None) -> dict:
    """turn.completed.usage（thread 累计值）→ 本轮增量；更新 session_entry["usage_baseline"]。

    负增量钳 0（thread 重置/compaction 等异常不产负 token）；session_entry None 时
    baseline 按 0（对齐 CC 对 session_entry 的 None 容忍：只读不炸）。cached_input_tokens
    不参与（面板只显示 input/output 增量）。
    """
    raw = usage_raw if isinstance(usage_raw, dict) else {}
    cur = {"input_tokens": int(raw.get("input_tokens") or 0),
           "output_tokens": int(raw.get("output_tokens") or 0)}
    base = session_entry.get("usage_baseline") if isinstance(session_entry, dict) else None
    base = base if isinstance(base, dict) else {}
    delta = {k: max(0, cur[k] - int(base.get(k) or 0)) for k in cur}
    if isinstance(session_entry, dict):
        session_entry["usage_baseline"] = cur
    return delta


class CodexCliRunner:
    """codex CLI 子进程 runner。process_factory 可注入（测试用 fake，不触真 CLI）。

    factory 形态：async def factory(argv: list[str], cwd: str) -> proc，
    proc 鸭式对齐 asyncio.subprocess.Process：.stdin.write/.stdin.close、
    .stdout 异步迭代行（bytes）、.terminate()/.kill()/.wait()（后三 async）。
    """

    def __init__(self, process_factory: Callable[..., Any] | None = None,
                 codex_bin: str = "codex", kill_grace_s: float = _KILL_GRACE_S):
        self._process_factory = process_factory  # None → 生产用真子进程
        self._codex_bin = codex_bin
        self._kill_grace_s = kill_grace_s

    async def _default_factory(self, argv: list[str], cwd: str):
        return await asyncio.create_subprocess_exec(
            *argv, cwd=cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            # 静默失败排障：stderr 只留尾部 ~4KB 进 error 文案（后台排空，防 PIPE 缓冲堵死子进程）
            stderr=asyncio.subprocess.PIPE,
        )

    @staticmethod
    async def _read_stderr_tail(stream, limit: int = 4096) -> str:
        """后台排空 stderr，只留尾部 limit 字节（returncode 守御的错误文案用；读取绝不外抛）。"""
        buf = b""
        try:
            while True:
                chunk = await stream.read(1024)
                if not chunk:
                    break
                buf = (buf + bytes(chunk))[-limit:]
        except Exception:
            pass
        return buf.decode("utf-8", errors="replace")

    def _build_argv(self, cwd: str, resume_session_id: str | None,
                    permission_mode: str) -> list[str]:
        """argv 构建：模式映射（plan→read-only / acceptEdits→workspace-write）；
        resume 走子命令（无 -s/-C flag：cwd 随 thread 还原，sandbox 经 -c sandbox_mode 覆盖）。"""
        sandbox = _SANDBOX_BY_MODE.get(permission_mode, "workspace-write")
        if resume_session_id:
            return [self._codex_bin, "exec", "resume", resume_session_id,
                    "--json", "--skip-git-repo-check",
                    "-c", f'sandbox_mode="{sandbox}"', "-"]
        return [self._codex_bin, "exec",
                "--json", "-s", sandbox, "-C", cwd,
                "--skip-git-repo-check", "-"]

    async def _kill(self, proc) -> None:
        """SIGTERM →（宽限内不退）SIGKILL；进程已退/信号失败静默（取消路径绝不外抛）。"""
        for sig in (proc.terminate, proc.kill):
            try:
                sig()
            except Exception:
                continue
            try:
                await asyncio.wait_for(proc.wait(), timeout=self._kill_grace_s)
                return
            except Exception:
                continue   # 超时/等待失败 → 升级 SIGKILL

    async def run(self, prompt: str, cwd: str, *, on_event, cancel_event,
                  resume_session_id: str | None = None,
                  permission_mode: str = "acceptEdits", can_use_tool=None,
                  session_entry: dict | None = None) -> str | None:
        """子进程流式跑 prompt；逐行 JSONL 归一后 on_event；返回 thread_id（str|None）。

        - thread.started 捕获 thread_id 作返回值（落 sessions.cc_session_id，SendSkill resume 用）。
        - turn.completed → done{usage:{duration_ms,cost_usd:None,input_tokens,output_tokens}}：
          token 经 usage_baseline 差分；duration_ms 由本 runner time.monotonic 计；cost 无→None（前端容缺）。
        - turn.failed/error → error 事件；异常不外抛转 error 事件（对齐 CC 语义）。
        - 取消：每条事件前查 cancel_event → SIGTERM（3s）→ SIGKILL，发 stopped 终态（不发 done）。
        - EOF 后 returncode 守御：非零退出 = 失败终态——本轮未发过 error/done 时补发 error
          （stderr 尾部截 400 字 + 退出码），绝不发裸 done（静默失败不误报「完成」，
          如 resume 不存在 thread_id：退出码 1、错误只在 stderr、stdout 零事件）；
          零退出才走裸 done 兜底（对齐 CC：流尽未遇 ResultMessage 也发 done）。
        - can_use_tool/mode_pending/rewind_pending 忽略：headless 无运行中审批/回滚钩子
          （mode 下轮生效：SendSkill 读库 mode → 新 sandbox 进 argv）。
        """
        factory = self._process_factory or self._default_factory
        argv = self._build_argv(cwd, resume_session_id, permission_mode)
        t0 = time.monotonic()
        thread_id: str | None = None
        done_emitted = False
        error_emitted = False   # 本轮已发 error（turn.failed/error）→ 非零退出不再重复报
        try:
            proc = await factory(argv, cwd)
            proc.stdin.write(prompt.encode("utf-8"))
            drain = getattr(proc.stdin, "drain", None)
            if drain is not None:
                await drain()
            proc.stdin.close()   # `-` 从 stdin 读 prompt：写完即关，CLI 才开跑
            # stderr 后台排空（注入的 fake 无 stderr 属性 → None，不排）
            stderr_stream = getattr(proc, "stderr", None)
            stderr_task = (asyncio.ensure_future(self._read_stderr_tail(stderr_stream))
                           if stderr_stream is not None else None)
            async for raw in proc.stdout:
                if cancel_event.is_set():
                    await self._kill(proc)
                    if stderr_task is not None:
                        stderr_task.cancel()
                        try:
                            await stderr_task
                        except (asyncio.CancelledError, Exception):
                            pass
                    on_event({"kind": "stopped", "text": "已中断"})
                    return thread_id
                try:
                    line = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
                    obj = json.loads(line.strip())
                except (json.JSONDecodeError, ValueError):
                    continue   # 非 JSON 行（CLI 诊断噪声混进 stdout 时）跳过
                etype = obj.get("type") if isinstance(obj, dict) else None
                if etype == "thread.started":
                    tid = obj.get("thread_id")
                    if tid:
                        thread_id = str(tid)
                    continue
                if etype == "turn.completed":
                    delta = _diff_usage(obj.get("usage"), session_entry)
                    on_event({"kind": "done", "usage": {
                        "duration_ms": int((time.monotonic() - t0) * 1000),
                        "cost_usd": None,
                        **delta,
                    }})
                    done_emitted = True
                    continue
                for ev in normalize_event(obj):
                    if ev.get("kind") == "error":
                        error_emitted = True
                    on_event(ev)
            try:
                await asyncio.wait_for(proc.wait(), timeout=self._kill_grace_s)
            except Exception:
                await self._kill(proc)   # 流尽进程不退 → 按取消同款升级杀（防泄漏）
            stderr_tail = ""
            if stderr_task is not None:
                try:
                    stderr_tail = await asyncio.wait_for(stderr_task, timeout=1.0)
                except (asyncio.CancelledError, Exception):
                    stderr_task.cancel()
            rc = getattr(proc, "returncode", None)
            if not done_emitted and not cancel_event.is_set():
                if rc:
                    # 非零退出 = 失败终态：已发过 error 不再重复报，且绝不补裸 done
                    if not error_emitted:
                        tail = stderr_tail.strip()
                        detail = f"：{tail[-400:]}" if tail else "（stderr 无输出）"
                        on_event({"kind": "error",
                                  "text": f"codex 异常退出（退出码 {rc}）{detail}"})
                else:
                    on_event({"kind": "done"})
            return thread_id
        except Exception as e:
            print(f"[yibao/coding] codex runner 失败：{e}", file=sys.stderr)
            on_event({"kind": "error", "text": str(e)})
            return None
