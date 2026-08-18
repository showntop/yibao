"""CodexCliRunner + 双驱动接线：事件归一/usage 差分/cancel kill/argv 构建/_runner_for/
attach_codex/drivers/容缺回归（process_factory 注入 fake，不跑真 codex CLI；对齐 CC client_factory 范式）。"""
from __future__ import annotations
import asyncio, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# 插件 skills 不在 src 下，单独加路径（同 test_coding_plugin）
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugins", "coding", "skills"))
import coding as codingmod  # noqa: E402
from coding import _stream, StartSkill, SendSkill, RewindSkill  # noqa: E402
from _codex_runner import CodexCliRunner, normalize_event, item_events, _diff_usage  # noqa: E402
from yibao_brain.ipc import RiskLevel  # noqa: E402


def _run(coro): return asyncio.run(coro)


# ---------- 子进程 fake（鸭式 asyncio.subprocess.Process）----------
class _FakeStdin:
    def __init__(self): self.buf = b""; self.closed = False
    def write(self, data): self.buf += data
    async def drain(self): pass
    def close(self): self.closed = True


class _FakeStderr:
    """鸭式 StreamReader.read(n)：预置字节分块吐，读完 EOF（b""）。"""
    def __init__(self, data): self._data = data if isinstance(data, bytes) else str(data).encode()
    async def read(self, n=-1):
        if not self._data:
            return b""
        if n is None or n <= 0:
            n = len(self._data)
        chunk, self._data = self._data[:n], self._data[n:]
        return chunk


class _FakeProc:
    """stdout 预置行异步吐出（自身兼作 stdout 流）；terminate/kill 记录；
    wait_hangs=True 时 wait 在 kill 前永不返回（测 SIGTERM 超时升级 SIGKILL）；
    rc 预设正常退出码（默认 0）；stderr 预置字节（测 returncode 守御的错误文案）。"""
    def __init__(self, lines, *, wait_hangs=False, rc=0, stderr=b""):
        self.stdin = _FakeStdin()
        self.stderr = _FakeStderr(stderr)
        self._lines = list(lines)
        self.terminated = False
        self.killed = False
        self.returncode = None
        self._wait_hangs = wait_hangs
        self._rc = rc

    def terminate(self): self.terminated = True
    def kill(self): self.killed = True

    async def wait(self):
        if self._wait_hangs and not self.killed:
            await asyncio.sleep(3600)
        self.returncode = -9 if self.killed else (-15 if self.terminated else self._rc)
        return self.returncode

    @property
    def stdout(self): return self

    def __aiter__(self): return self._gen()

    async def _gen(self):
        for ln in self._lines:
            yield ln if isinstance(ln, (bytes, bytearray)) else (ln + "\n").encode()


def _factory(proc, seen=None):
    """process_factory：记录 argv/cwd，返回预置 _FakeProc。"""
    async def f(argv, cwd):
        if seen is not None:
            seen.update({"argv": argv, "cwd": cwd})
        return proc
    return f


class _SetEvent:
    """is_set() 恒 True 的 cancel_event 替身（同 test_coding_plugin）。"""
    def is_set(self): return True
    def set(self): pass


# ---------- argv 构建：模式映射 + resume ----------
def test_argv_default_and_plan_mode():
    r = CodexCliRunner()
    argv = r._build_argv("/tmp/p", None, "acceptEdits")
    assert argv == ["codex", "exec", "--json", "-s", "workspace-write", "-C", "/tmp/p",
                    "--skip-git-repo-check", "-"]
    argv_plan = r._build_argv("/tmp/p", None, "plan")
    assert "-s" in argv_plan and argv_plan[argv_plan.index("-s") + 1] == "read-only"
    assert argv_plan[-1] == "-"                       # prompt 走 stdin


def test_argv_resume_uses_subcommand_and_config_sandbox():
    """resume 无 -s/-C flag（0.137.0 resume --help 实测）：sandbox 经 -c sandbox_mode 覆盖，
    cwd 随 thread 还原不进 argv。"""
    r = CodexCliRunner()
    argv = r._build_argv("/tmp/p", "t-123", "plan")
    assert argv[:3] == ["codex", "exec", "resume"]
    assert argv[3] == "t-123"
    assert "-s" not in argv and "-C" not in argv
    assert "-c" in argv and 'sandbox_mode="read-only"' in argv
    assert "--json" in argv and "--skip-git-repo-check" in argv and argv[-1] == "-"


# ---------- 纯映射：normalize_event / item_events ----------
def test_item_agent_message_only_completed():
    """v1 不做流式 diff：updated 丢弃，completed 全量一条 text_delta。"""
    assert item_events("updated", {"type": "agent_message", "text": "部分"}) == []
    ev = item_events("completed", {"type": "agent_message", "text": "整段回答"})
    assert ev == [{"kind": "text_delta", "text": "整段回答"}]
    assert item_events("completed", {"type": "agent_message", "text": ""}) == []   # 空文本不发


def test_item_reasoning_truncated_500():
    long_text = "想" * 600
    ev = item_events("completed", {"type": "reasoning", "text": long_text})
    assert ev == [{"kind": "thinking", "text": long_text[:500]}]


def test_item_command_execution_started_and_completed():
    started = item_events("started", {"type": "command_execution", "command": "ls -la"})
    assert started == [{"kind": "tool_use", "tool": "Bash", "input": {"command": "ls -la"}}]
    completed = item_events("completed", {
        "type": "command_execution", "command": "ls",
        "aggregated_output": "输" * 900, "exit_code": 0, "status": "completed"})
    assert completed == [{"kind": "tool_result", "text": "输" * 800, "is_error": False}]
    failed = item_events("completed", {
        "type": "command_execution", "aggregated_output": "err", "exit_code": 2})
    assert failed == [{"kind": "tool_result", "text": "err", "is_error": True}]


def test_item_file_change_multi_paths():
    """多文件逐张发（一张卡一个路径）；无 diff 内容 → old/new=None 降级。"""
    ev = item_events("completed", {"type": "file_change", "changes": {
        "/a.py": {"type": "update"}, "/b.py": {"type": "add"}}})
    assert ev == [{"kind": "file_edit", "tool": "Edit", "path": "/a.py", "old": None, "new": None},
                  {"kind": "file_edit", "tool": "Edit", "path": "/b.py", "old": None, "new": None}]
    assert item_events("completed", {"type": "file_change", "changes": []}) == []


def test_item_mcp_web_todo():
    mcp = item_events("completed", {"type": "mcp_tool_call", "server": "ctx7",
                                    "tool": "lookup", "arguments": {"q": "x"}})
    assert mcp == [{"kind": "tool_use", "tool": "ctx7.lookup", "input": {"q": "x"}}]
    web = item_events("completed", {"type": "web_search", "query": "codex cli"})
    assert web == [{"kind": "tool_use", "tool": "WebSearch", "input": {"query": "codex cli"}}]
    todo = item_events("completed", {"type": "todo_list", "items": [
        {"text": "做 A", "completed": True}, {"text": "做 B", "completed": False}]})
    assert todo == [{"kind": "tool_use", "tool": "TodoWrite", "input": {"todos": [
        {"content": "做 A", "status": "completed"}, {"content": "做 B", "status": "pending"}]}}]


def test_normalize_turn_failed_and_error():
    failed = normalize_event({"type": "turn.failed", "error": {"message": "限额到了"}})
    assert failed == [{"kind": "error", "text": "限额到了"}]
    err = normalize_event({"type": "error", "message": "网络断"})
    assert err == [{"kind": "error", "text": "网络断"}]
    assert normalize_event({"type": "turn.started"}) == []        # 无面板对应物
    assert normalize_event({"type": "mystery"}) == []             # 未知类型忽略
    assert normalize_event("not-a-dict") == []


def test_diff_usage_delta_and_clamp():
    entry: dict = {}
    d1 = _diff_usage({"input_tokens": 1000, "cached_input_tokens": 800, "output_tokens": 200}, entry)
    assert d1 == {"input_tokens": 1000, "output_tokens": 200}      # 首轮 baseline=0
    assert entry["usage_baseline"] == {"input_tokens": 1000, "output_tokens": 200}
    d2 = _diff_usage({"input_tokens": 1500, "output_tokens": 260}, entry)
    assert d2 == {"input_tokens": 500, "output_tokens": 60}        # 增量=累计−baseline
    d3 = _diff_usage({"input_tokens": 100, "output_tokens": 10}, entry)
    assert d3 == {"input_tokens": 0, "output_tokens": 0}           # 累计回退 → 负值钳 0
    # session_entry None 容忍（对齐 CC 对 None 的判空）：baseline 按 0，不炸
    d4 = _diff_usage({"input_tokens": 7, "output_tokens": 3}, None)
    assert d4 == {"input_tokens": 7, "output_tokens": 3}


# ---------- runner：流式 / thread_id / usage 差分 / stdin ----------
def test_runner_streams_full_session():
    """全事件链：thread.started 捕获返回；工具卡/思考/文本逐类归一；turn.completed → done 带差分 usage。"""
    lines = [
        {"type": "thread.started", "thread_id": "t-abc"},
        {"type": "turn.started"},
        {"type": "item.started", "item": {"id": "i1", "type": "command_execution",
                                          "command": "ls", "status": "in_progress"}},
        {"type": "item.completed", "item": {"id": "i1", "type": "command_execution",
                                            "command": "ls", "aggregated_output": "ok", "exit_code": 0}},
        {"type": "item.completed", "item": {"id": "i2", "type": "reasoning", "text": "想一想"}},
        {"type": "item.completed", "item": {"id": "i3", "type": "agent_message", "text": "做完了"}},
        {"type": "turn.completed", "usage": {"input_tokens": 1200, "cached_input_tokens": 900,
                                             "output_tokens": 88}},
    ]
    events, entry = [], {}
    proc = _FakeProc([json.dumps(x) for x in lines])
    runner = CodexCliRunner(process_factory=_factory(proc))
    tid = _run(runner.run("干活", "/tmp/p", on_event=events.append,
                          cancel_event=asyncio.Event(), session_entry=entry))
    assert tid == "t-abc"
    kinds = [e["kind"] for e in events]
    assert kinds == ["tool_use", "tool_result", "thinking", "text_delta", "done"]
    done = events[-1]
    assert done["usage"]["input_tokens"] == 1200 and done["usage"]["output_tokens"] == 88
    assert done["usage"]["cost_usd"] is None                        # codex 无成本 → None（前端容缺）
    assert done["usage"]["duration_ms"] >= 0
    assert proc.stdin.buf == "干活".encode() and proc.stdin.closed  # prompt 走 stdin 且关闭


def test_runner_skips_non_json_lines():
    events = []
    proc = _FakeProc(["不是 json 的诊断噪声", "", json.dumps({"type": "thread.started", "thread_id": "t-1"}),
                      "{broken json", json.dumps({"type": "turn.completed", "usage": {}})])
    runner = CodexCliRunner(process_factory=_factory(proc))
    tid = _run(runner.run("p", "/tmp", on_event=events.append, cancel_event=asyncio.Event()))
    assert tid == "t-1"
    assert [e["kind"] for e in events] == ["done"]


def test_runner_usage_baseline_across_rounds():
    """同一 session_entry 跨轮：第二轮 done 只报增量（turn.completed.usage 是 thread 累计值）。"""
    entry: dict = {}
    proc1 = _FakeProc([json.dumps({"type": "turn.completed",
                                   "usage": {"input_tokens": 1000, "output_tokens": 100}})])
    _run(CodexCliRunner(process_factory=_factory(proc1)).run(
        "r1", "/tmp", on_event=lambda e: None, cancel_event=asyncio.Event(), session_entry=entry))
    done2 = []
    proc2 = _FakeProc([json.dumps({"type": "turn.completed",
                                   "usage": {"input_tokens": 1400, "output_tokens": 130}})])
    _run(CodexCliRunner(process_factory=_factory(proc2)).run(
        "r2", "/tmp", on_event=lambda e: done2.append(e) if e["kind"] == "done" else None,
        cancel_event=asyncio.Event(), session_entry=entry))
    assert done2[0]["usage"]["input_tokens"] == 400 and done2[0]["usage"]["output_tokens"] == 30


def test_runner_bare_done_fallback_at_eof():
    """流尽未遇 turn.completed → 裸 done 兜底（对齐 CC：async-for 结束也发 done，面板不挂）。"""
    events = []
    proc = _FakeProc([json.dumps({"type": "item.completed",
                                  "item": {"type": "agent_message", "text": "半截"}})])
    _run(CodexCliRunner(process_factory=_factory(proc)).run(
        "p", "/tmp", on_event=events.append, cancel_event=asyncio.Event()))
    assert [e["kind"] for e in events] == ["text_delta", "done"]


def test_runner_turn_failed_emits_error():
    events = []
    proc = _FakeProc([json.dumps({"type": "turn.failed", "error": {"message": "boom"}})])
    tid = _run(CodexCliRunner(process_factory=_factory(proc)).run(
        "p", "/tmp", on_event=events.append, cancel_event=asyncio.Event()))
    assert any(e["kind"] == "error" and "boom" in e["text"] for e in events)
    assert tid is None


def test_runner_error_isolated():
    """factory 自身抛（如 codex 二进制不存在）→ error 事件，绝不外抛；返回 None。"""
    async def bad_factory(argv, cwd): raise FileNotFoundError("codex")
    events = []
    tid = _run(CodexCliRunner(process_factory=bad_factory).run(
        "p", "/tmp", on_event=events.append, cancel_event=asyncio.Event()))
    assert tid is None and any(e["kind"] == "error" for e in events)


# ---------- runner：returncode 守御（静默失败不误报「完成」）----------
def test_runner_nonzero_exit_emits_error_with_stderr_tail():
    """真机形态：`codex exec resume <不存在 thread_id>` → 退出码 1、错误只在 stderr、
    stdout 零事件。守御：发 error（stderr 尾部 + 退出码），绝不发裸 done；thread_id None。"""
    events = []
    proc = _FakeProc([], rc=1,
                     stderr="ERROR: No conversation found with session ID: t-ghost\n")
    tid = _run(CodexCliRunner(process_factory=_factory(proc)).run(
        "继续", "/tmp", on_event=events.append, cancel_event=asyncio.Event(),
        resume_session_id="t-ghost"))
    assert tid is None
    assert [e["kind"] for e in events] == ["error"]          # 无裸 done
    text = events[0]["text"]
    assert "退出码 1" in text and "No conversation found" in text


def test_runner_nonzero_exit_stderr_tail_truncated_400():
    """stderr 尾部摘要在错误文案里截 400 字（收集上限 4KB，文案只露尾部）。"""
    events = []
    proc = _FakeProc([], rc=2, stderr="头" + "x" * 5000)
    _run(CodexCliRunner(process_factory=_factory(proc)).run(
        "p", "/tmp", on_event=events.append, cancel_event=asyncio.Event()))
    text = events[0]["text"]
    assert events[0]["kind"] == "error" and "退出码 2" in text
    assert "头" not in text                                   # 只留尾部
    assert "x" * 400 in text and "x" * 401 not in text       # 截 400 字


def test_runner_nonzero_exit_without_stderr_reports_code():
    """stderr 也空 → 错误文案仍带退出码（不靠 stderr 也有定位信息）。"""
    events = []
    proc = _FakeProc([], rc=3)
    _run(CodexCliRunner(process_factory=_factory(proc)).run(
        "p", "/tmp", on_event=events.append, cancel_event=asyncio.Event()))
    assert [e["kind"] for e in events] == ["error"]
    assert "退出码 3" in events[0]["text"]


def test_runner_nonzero_exit_after_turn_failed_no_duplicate_no_done():
    """turn.failed 已发 error + 进程非零退 → 不重复报 error，也不补裸 done（终态就是失败）。"""
    events = []
    proc = _FakeProc([json.dumps({"type": "turn.failed", "error": {"message": "boom"}})], rc=1)
    _run(CodexCliRunner(process_factory=_factory(proc)).run(
        "p", "/tmp", on_event=events.append, cancel_event=asyncio.Event()))
    assert [e["kind"] for e in events] == ["error"]
    assert events[0]["text"] == "boom"


def test_runner_zero_exit_bare_done_fallback_kept():
    """零退出：裸 done 兜底保持原样——stderr 有噪声也不误报 error（rc=0 不看 stderr）。"""
    events = []
    proc = _FakeProc([json.dumps({"type": "item.completed",
                                  "item": {"type": "agent_message", "text": "半截"}})],
                     rc=0, stderr="DeprecationWarning: blah\n")
    _run(CodexCliRunner(process_factory=_factory(proc)).run(
        "p", "/tmp", on_event=events.append, cancel_event=asyncio.Event()))
    assert [e["kind"] for e in events] == ["text_delta", "done"]


# ---------- runner：cancel → SIGTERM →（3s）SIGKILL → stopped ----------
def test_runner_cancel_terminates_and_emits_stopped():
    """取消：真杀子进程（terminate）+ 发 stopped 不发 done；thread_id 已捕获仍返回。"""
    lines = [
        json.dumps({"type": "thread.started", "thread_id": "t-kill"}),
        json.dumps({"type": "item.started", "item": {"type": "command_execution", "command": "sleep 99"}}),
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "不应到达"}}),
        json.dumps({"type": "turn.completed", "usage": {}}),
    ]
    cancel = asyncio.Event()
    events, proc = [], _FakeProc(lines)

    def on_event(e):
        events.append(e)
        if e["kind"] == "tool_use":
            cancel.set()

    runner = CodexCliRunner(process_factory=_factory(proc))
    tid = _run(runner.run("p", "/tmp", on_event=on_event, cancel_event=cancel))
    assert tid == "t-kill"                       # 取消也保留已捕获 thread_id（落库续聊用）
    kinds = [e["kind"] for e in events]
    assert "done" not in kinds and kinds[-1] == "stopped"
    assert proc.terminated is True               # SIGTERM 已发
    assert proc.killed is False                  # 宽限内退了，无需升级


def test_runner_cancel_escalates_to_sigkill():
    """SIGTERM 后进程不退（wait 挂起）→ 宽限超时升级 SIGKILL。"""
    proc = _FakeProc([json.dumps({"type": "turn.started"})] * 3, wait_hangs=True)
    runner = CodexCliRunner(process_factory=_factory(proc), kill_grace_s=0.05)
    events = []
    _run(runner.run("p", "/tmp", on_event=events.append, cancel_event=_SetEvent()))
    assert proc.terminated is True and proc.killed is True
    assert events[-1]["kind"] == "stopped"


# ---------- _runner_for 选择器 ----------
def test_runner_for_mapping():
    # isinstance 对 codingmod 上的类（_sibling 加载实例与直接 import 是双模块对象，不能混用）
    assert isinstance(codingmod._runner_for("claude-code"), codingmod.ClaudeCodeRunner)
    assert isinstance(codingmod._runner_for("cc"), codingmod.ClaudeCodeRunner)      # attach_cc 落库写法
    assert isinstance(codingmod._runner_for(None), codingmod.ClaudeCodeRunner)      # 老行缺省
    assert isinstance(codingmod._runner_for("codex"), codingmod.CodexCliRunner)
    try:
        codingmod._runner_for("cursor")
        raise AssertionError("应抛 ValueError")
    except ValueError as e:
        assert "cursor" in str(e) and "claude-code" in str(e)


# ---------- skills 接线 ----------
class _FakeDB:
    """极小鸭式 db（同 test_coding_plugin 约定）。"""
    def __init__(self): self.rows = {}; self.updates = []; self._tables = {}
    def insert(self, table, row):
        row = dict(row)
        if table == "sessions":
            self.rows[row["id"]] = row; return row["id"]
        row.setdefault("id", f"{table}-{len(self._tables.get(table, [])) + 1}")
        self._tables.setdefault(table, []).append(row)
        return row["id"]
    def update(self, table, rid, fields): self.updates.append((rid, fields)); self.rows.setdefault(rid, {}).update(fields)
    def query(self, table, where=None, order=None, limit=None, **_):
        out = list(self.rows.values()) if table == "sessions" else list(self._tables.get(table, []))
        if where:
            out = [r for r in out if all(r.get(k) == v for k, v in where.items())]
        if order:
            parts = order.split()
            out = sorted(out, key=lambda r: r.get(parts[0]) or 0,
                         reverse=len(parts) > 1 and parts[1].upper() == "DESC")
        return out[:limit] if limit else out


class _Ctx:
    def __init__(self, db): self.db = db; self.emit_event = lambda *a, **k: None


def test_start_codex_agent_uses_codex_runner(monkeypatch):
    """StartSkill agent=codex → CodexCliRunner + agent 透传 _spawn_stream；库行 agent=codex。"""
    db = _FakeDB()
    captured, made = {}, []
    monkeypatch.setattr(codingmod, "_spawn_stream",
                        lambda *a, **k: captured.update({"args": a, "kwargs": k}))
    monkeypatch.setattr(codingmod, "CodexCliRunner", lambda: made.append(1) or object())
    res = StartSkill().run({"cwd": "/tmp", "prompt": "p", "agent": "codex"}, _Ctx(db))
    assert res.success and made == [1]
    assert captured["kwargs"].get("agent") == "codex"
    assert db.rows[res.data["session_id"]]["agent"] == "codex"


def test_start_unknown_agent_clear_error(monkeypatch):
    db = _FakeDB()
    called = {"n": 0}
    monkeypatch.setattr(codingmod, "_spawn_stream",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    res = StartSkill().run({"cwd": "/tmp", "prompt": "p", "agent": "cursor"}, _Ctx(db))
    assert res.success is False and "cursor" in res.error
    assert called["n"] == 0 and db.rows == {}                # 未落库未起流


def test_send_codex_session_resumes_via_codex_runner(monkeypatch):
    """codex 会话 send → CodexCliRunner + resume_session_id=thread_id + agent 透传。"""
    db = _FakeDB()
    db.rows["s1"] = {"id": "s1", "cwd": "/tmp/p", "cc_session_id": "t-1",
                     "agent": "codex", "status": "done", "mode": "plan"}
    captured, made = {}, []
    monkeypatch.setattr(codingmod, "_spawn_stream",
                        lambda *a, **k: captured.update({"kwargs": k}))
    monkeypatch.setattr(codingmod, "CodexCliRunner", lambda: made.append(1) or object())
    res = SendSkill().run({"id": "s1", "prompt": "再来"}, _Ctx(db))
    assert res.success and made == [1]
    assert captured["kwargs"].get("resume_session_id") == "t-1"
    assert captured["kwargs"].get("agent") == "codex"
    assert captured["kwargs"].get("permission_mode") == "plan"   # 库 mode 沿用 → read-only sandbox


def test_rewind_rejects_codex_session():
    db = _FakeDB()
    db.rows["s1"] = {"id": "s1", "agent": "codex", "cc_session_id": "t-1", "status": "done"}
    res = RewindSkill().run({"id": "s1", "user_msg_id": "u1"}, _Ctx(db))
    assert res.success is False and "Claude Code" in res.error


# ---------- _stream：panel_data agent 透传 + usage 容缺 ----------
class _FakeRunner:
    """鸭式 runner：发 text_delta + done(usage 可配)，返回 cc_sid。"""
    def __init__(self, cc_sid="t-1", usage=None):
        self._cc_sid = cc_sid; self._usage = usage
    async def run(self, prompt, cwd, *, on_event, cancel_event, resume_session_id=None,
                  permission_mode="acceptEdits", can_use_tool=None, session_entry=None):
        on_event({"kind": "text_delta", "text": "hi"})
        on_event({"kind": "done", "usage": self._usage or {}})
        return self._cc_sid


def test_stream_panel_data_carries_agent():
    """panel_data data = {session_id, agent, event} 三键（面板按 agent 更新引擎徽标）。"""
    db = _FakeDB(); db.rows["s1"] = {"id": "s1", "status": "running"}
    emitted = []
    _run(_stream(db, "s1", "/tmp", "p", _FakeRunner(),
                 emit_event=emitted.append, cancel=__import__("threading").Event(),
                 agent="codex"))
    datas = [e["payload"]["data"] for e in emitted if e.get("kind") == "panel_data"]
    assert datas and all(d["agent"] == "codex" for d in datas)
    assert all(set(d.keys()) == {"session_id", "agent", "event"} for d in datas)


def test_stream_defaults_agent_claude_code():
    """CC 路径回归：不传 agent → data.agent=claude-code。"""
    db = _FakeDB(); db.rows["s1"] = {"id": "s1", "status": "running"}
    emitted = []
    _run(_stream(db, "s1", "/tmp", "p", _FakeRunner(),
                 emit_event=emitted.append, cancel=__import__("threading").Event()))
    datas = [e["payload"]["data"] for e in emitted if e.get("kind") == "panel_data"]
    assert datas and all(d["agent"] == "claude-code" for d in datas)


def test_stream_keeps_thread_id_when_codex_fails_silently():
    """codex 静默失败全链路：runner 发 error + 返回 None（未捕获 thread_id）→
    终态 failed，且 cc_session_id 列不被抹成 ""——老行 thread_id 保留，后续 send 仍能 resume。"""
    class _SilentFailRunner:
        async def run(self, prompt, cwd, *, on_event, cancel_event, **kw):
            on_event({"kind": "error", "text": "codex 异常退出（退出码 1）：No conversation found"})
            return None
    db = _FakeDB()
    db.rows["s1"] = {"id": "s1", "status": "running", "cc_session_id": "t-old", "agent": "codex"}
    _run(_stream(db, "s1", "/tmp", "p", _SilentFailRunner(),
                 emit_event=None, cancel=__import__("threading").Event(), agent="codex"))
    last = db.updates[-1][1]
    assert last["status"] == "failed"
    assert "cc_session_id" not in last                        # 不更新该列
    assert db.rows["s1"]["cc_session_id"] == "t-old"          # 老值保留


def test_resume_failure_falls_back_to_brief_new_session(monkeypatch):
    """codex resume 零事件失败（encrypted_content bug 形态：stdout 零事件 → 未捕获
    thread.started → runner 守御补发 error）→ 一次性自动 fallback：交接摘要新开会话续跑。
    二次调用 resume_session_id is None、prompt 含【交接上下文】+原 prompt、终态 done、
    cc_session_id 更新、messages 表有 fallback marker（且经 panel_data 进面板流）。"""
    calls = []

    class _FlakyResumeRunner:
        async def run(self, prompt, cwd, *, on_event, cancel_event, resume_session_id=None, **kw):
            calls.append({"prompt": prompt, "resume_session_id": resume_session_id})
            if resume_session_id is not None:
                on_event({"kind": "error", "text": "codex 异常退出（退出码 1）：encrypted_content"})
                return None                                   # 未捕获 thread.started
            on_event({"kind": "text_delta", "text": "接着做完了"})
            on_event({"kind": "done", "usage": {}})
            return "t-new"

    monkeypatch.setattr(codingmod, "_build_brief",
                        lambda llm, turns, git, src, dst: "交接摘要XYZ")
    monkeypatch.setattr(codingmod._codex, "git_summary", lambda cwd: "")
    db = _FakeDB()
    db.rows["s1"] = {"id": "s1", "status": "running", "cc_session_id": "t-old", "agent": "codex"}
    db.insert("messages", {"session_id": "s1", "role": "user", "text": "老任务",
                           "ts": 1, "seq": 1, "uuid": ""})
    emitted = []
    _run(_stream(db, "s1", "/tmp", "原任务", _FlakyResumeRunner(),
                 emit_event=emitted.append, cancel=__import__("threading").Event(),
                 resume_session_id="t-old", agent="codex", llm=object()))
    assert len(calls) == 2                                     # 首轮 resume + fallback 新会话
    assert calls[0]["resume_session_id"] == "t-old"
    assert calls[1]["resume_session_id"] is None               # fallback 全新会话
    p2 = calls[1]["prompt"]
    assert "【交接上下文】" in p2 and "交接摘要XYZ" in p2
    assert "【用户继续】" in p2 and "原任务" in p2
    last = db.updates[-1][1]
    assert last["status"] == "done"                            # 终态按重试结果定
    assert last["cc_session_id"] == "t-new"                    # 新 thread_id 落库
    markers = [r for r in db._tables.get("messages", []) if r["role"] == "marker"]
    assert any("resume 失败，已用交接摘要新开会话续跑" in m["text"] for m in markers)
    assert any(e.get("kind") == "panel_data"
               and e["payload"]["data"]["event"].get("kind") == "marker"
               and "resume 失败" in str(e["payload"]["data"]["event"].get("text") or "")
               for e in emitted)                               # marker 双写：也进面板流


def test_resume_fallback_clears_usage_baseline(monkeypatch):
    """fallback 新开会话沿用同一 session_entry：旧 thread 的 usage 差分基准先清掉，
    否则 fallback 轮差分被旧累计值钳 0 少报（S5-T2 评审留）。"""
    class _FlakyRunner:
        async def run(self, prompt, cwd, *, on_event, cancel_event, resume_session_id=None, **kw):
            if resume_session_id is not None:
                on_event({"kind": "error", "text": "codex 异常退出（退出码 1）：encrypted_content"})
                return None
            on_event({"kind": "done", "usage": {}})
            return "t-new"

    monkeypatch.setattr(codingmod, "_build_brief", lambda llm, turns, git, src, dst: "摘要")
    monkeypatch.setattr(codingmod._codex, "git_summary", lambda cwd: "")
    db = _FakeDB()
    db.rows["s1"] = {"id": "s1", "status": "running", "cc_session_id": "t-old", "agent": "codex"}
    entry = {"usage_baseline": {"input_tokens": 9000, "output_tokens": 900}}  # 旧 thread 累计值
    codingmod._SESSIONS["s1"] = entry
    try:
        _run(_stream(db, "s1", "/tmp", "p", _FlakyRunner(),
                     emit_event=None, cancel=__import__("threading").Event(),
                     resume_session_id="t-old", agent="codex", llm=object()))
        assert "usage_baseline" not in entry                     # 重跑前已清，不钳 fallback 轮差分
    finally:
        codingmod._SESSIONS.pop("s1", None)


def test_resume_failure_without_llm_keeps_failed():
    """无 llm（capability 未声明）→ 跳过 fallback 走原 failed 路径：runner 只调一次，
    终态 failed，老 thread_id 保留不抹。"""
    calls = []

    class _FailRunner:
        async def run(self, prompt, cwd, *, on_event, cancel_event, resume_session_id=None, **kw):
            calls.append(resume_session_id)
            on_event({"kind": "error", "text": "codex 异常退出（退出码 1）：encrypted_content"})
            return None

    db = _FakeDB()
    db.rows["s1"] = {"id": "s1", "status": "running", "cc_session_id": "t-old", "agent": "codex"}
    _run(_stream(db, "s1", "/tmp", "p", _FailRunner(),
                 emit_event=None, cancel=__import__("threading").Event(),
                 resume_session_id="t-old", agent="codex"))
    assert calls == ["t-old"]                                  # 未重试
    assert db.updates[-1][1]["status"] == "failed"
    assert db.rows["s1"]["cc_session_id"] == "t-old"


def test_usage_suffix_tolerates_none_cost():
    """codex usage：cost_usd=None → 后缀跳过成本段，只留耗时/token。"""
    s = codingmod._usage_suffix({"duration_ms": 1200, "cost_usd": None,
                                 "input_tokens": 100, "output_tokens": 50})
    assert s == "（耗时 1s · 150 tok）"
    assert codingmod._usage_suffix({"cost_usd": None}) == ""       # 全缺 → 无后缀


def test_report_final_done_without_cost_segment():
    """终态汇报容缺：cost_usd=None 时任务卡/气泡文案无 $ 段。"""
    emitted = []
    codingmod._report_final(emitted.append, "s1", "修 bug", "done",
                            {"duration_ms": 2000, "cost_usd": None, "input_tokens": 10,
                             "output_tokens": 5})
    reminders = [e for e in emitted if e.get("kind") == "reminder"]   # 只取终态汇报，按 kind 过滤
    assert len(reminders) == 1
    assert "$" not in reminders[0]["text"] and "15 tok" in reminders[0]["text"]


# ---------- coding.drivers ----------
def test_drivers_available_with_version(monkeypatch):
    class _Out:
        returncode = 0; stdout = "codex-cli 0.137.0\n"
    monkeypatch.setattr(codingmod.shutil, "which", lambda name: "/usr/local/bin/codex")
    monkeypatch.setattr(codingmod.subprocess, "run", lambda *a, **k: _Out())
    res = codingmod.DriversSkill().run({}, _Ctx(_FakeDB()))
    assert res.success
    drivers = {d["id"]: d for d in res.data["drivers"]}
    assert drivers["claude-code"] == {"id": "claude-code", "available": True}
    assert drivers["codex"] == {"id": "codex", "available": True, "version": "0.137.0"}


def test_drivers_codex_missing_and_probe_failure(monkeypatch):
    """二进制不存在 → unavailable；--version 异常/超时 → 容错 unavailable（绝不抛）。"""
    monkeypatch.setattr(codingmod.shutil, "which", lambda name: None)
    drivers = {d["id"]: d for d in codingmod.DriversSkill().run({}, _Ctx(_FakeDB())).data["drivers"]}
    assert drivers["codex"] == {"id": "codex", "available": False, "version": None}

    monkeypatch.setattr(codingmod.shutil, "which", lambda name: "/usr/local/bin/codex")
    def _boom(*a, **k): raise TimeoutError("timeout")
    monkeypatch.setattr(codingmod.subprocess, "run", _boom)
    drivers = {d["id"]: d for d in codingmod.DriversSkill().run({}, _Ctx(_FakeDB())).data["drivers"]}
    assert drivers["codex"]["available"] is False
    assert codingmod.DriversSkill.default_risk == RiskLevel.L0_READONLY


# ---------- coding.attach_codex ----------
def _write_rollout(root, rel, sid, cwd, ts, turns):
    """在 root 下造 codex rollout（session_meta 首行 + response_item 对话行）。"""
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    lines = [json.dumps({"type": "session_meta",
                         "payload": {"session_id": sid, "cwd": cwd, "timestamp": ts}})]
    for role, text in turns:
        lines.append(json.dumps({"type": "response_item",
                                 "payload": {"role": role, "content": text}}))
    with open(p, "w") as f:
        f.write("\n".join(lines) + "\n")
    return p


def test_attach_codex_imports_and_idempotent(tmp_path, monkeypatch):
    """登记落库：agent=codex/source=native/status=done/cc_session_id=thread_id/cwd 取 session_meta/
    prompt=首条 user 截 60；幂等按 cc_session_id 返回既有 id。"""
    root = str(tmp_path / "codex_sessions")
    long_first = "甲" * 70
    _write_rollout(root, "2026/08/16/r1.jsonl", "t-imp", "/tmp/proj",
                   "2026-08-16T10:00:00Z", [("user", long_first), ("assistant", "答")])
    _write_rollout(root, "2026/08/16/r2.jsonl", "t-other", "/tmp/proj",
                   "2026-08-16T11:00:00Z", [("user", "别的")])   # 不命中不误伤
    monkeypatch.setattr(codingmod, "_codex_sessions_root", lambda: root)
    db = _FakeDB()
    res = codingmod.AttachCodexSkill().run({"session_id": "t-imp"}, _Ctx(db))
    assert res.success
    sid = res.data["session_id"]
    row = db.rows[sid]
    assert row["agent"] == "codex" and row["source"] == "native" and row["status"] == "done"
    assert row["cc_session_id"] == "t-imp" and row["cwd"] == "/tmp/proj"
    assert row["prompt"] == long_first[:60]
    assert row["created_at"] > 0 and row["finished_at"] == row["created_at"]
    # 幂等：再导一次 → 同 id，不重复插
    res2 = codingmod.AttachCodexSkill().run({"session_id": "t-imp"}, _Ctx(db))
    assert res2.success and res2.data["session_id"] == sid and len(db.rows) == 1


def test_attach_codex_missing_and_bad_params(tmp_path, monkeypatch):
    monkeypatch.setattr(codingmod, "_codex_sessions_root", lambda: str(tmp_path / "none"))
    db = _FakeDB()
    res = codingmod.AttachCodexSkill().run({"session_id": "t-ghost"}, _Ctx(db))
    assert not res.success and "t-ghost" in res.error
    res2 = codingmod.AttachCodexSkill().run({}, _Ctx(db))
    assert not res2.success and "session_id" in res2.error
    res3 = codingmod.AttachCodexSkill().run({"session_id": "../escape"}, _Ctx(db))
    assert not res3.success                                  # 白名单挡路径逃逸（同 attach_cc）


def test_send_on_attached_codex_session_resumes_natively(tmp_path, monkeypatch):
    """全链路：attach_codex 登记 → send 按 agent=codex 走 codex resume（thread_id 透传）。"""
    root = str(tmp_path / "codex_sessions")
    _write_rollout(root, "2026/08/16/r.jsonl", "t-keep", "/tmp/proj",
                   "2026-08-16T10:00:00Z", [("user", "老任务")])
    monkeypatch.setattr(codingmod, "_codex_sessions_root", lambda: root)
    db = _FakeDB()
    sid = codingmod.AttachCodexSkill().run({"session_id": "t-keep"}, _Ctx(db)).data["session_id"]
    captured, made = {}, []
    monkeypatch.setattr(codingmod, "_spawn_stream",
                        lambda *a, **k: captured.update({"kwargs": k}))
    monkeypatch.setattr(codingmod, "CodexCliRunner", lambda: made.append(1) or object())
    res = SendSkill().run({"id": sid, "prompt": "接着干"}, _Ctx(db))
    assert res.success and made == [1]
    assert captured["kwargs"].get("resume_session_id") == "t-keep"   # exec resume 原生续
    assert captured["kwargs"].get("agent") == "codex"


# ---------- api.toml 契约 ----------
def test_api_toml_registers_drivers_and_attach_codex():
    """两方法 direct + quiet（chip/popover 内调用，不发 panel 事件）、无 panel 字段。"""
    import tomllib
    api_path = os.path.join(os.path.dirname(__file__), "..", "..", "plugins", "coding", "api.toml")
    api = tomllib.loads(open(api_path, encoding="utf-8").read())
    methods = {m["name"]: m for m in api["method"]}
    for name in ("drivers", "attach_codex"):
        m = methods[name]
        assert m["handler"] == f"coding.{name}"
        assert m["direct"] is True and m["quiet"] is True
        assert "panel" not in m
