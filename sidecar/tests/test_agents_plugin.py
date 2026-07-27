"""agents 插件（智能体调度：dispatch/status/stop + 完成主动播报）端到端测试。

加载真实 plugins/agents/（数据目录重定向到 tmp）；subprocess.Popen 换 fake
（可控完成的假进程），daemon 线程的落库与 emit_event 播报走真实路径。
"""
import json
import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

import pytest

from yibao_brain.ipc import RiskLevel
from yibao_brain.llm import FakeProvider
from yibao_brain.memory import FakeMemory
from yibao_brain.plugins import LlmChat, get_api, get_panel, load_plugins
from yibao_brain.skills import SkillRegistry

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = REPO_ROOT / "plugins" / "agents"


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """把插件数据目录指到 tmp（db/log 落盘不碰真实用户目录）。"""
    d = tmp_path / "data"
    monkeypatch.setenv("YIBAO_DATA_DIR", str(d))
    return d


@pytest.fixture
def env(data_dir):
    """加载真实插件目录；返回 (registry, 加载结果, emit_event 收到的事件)。"""
    reg = SkillRegistry()

    class _Http:
        def get(self, url, **kw):
            return {}

        def post(self, url, **kw):
            return {}

    events: list[dict] = []
    results = load_plugins(
        REPO_ROOT / "plugins", reg,
        memory=FakeMemory(), http=_Http(), llm=LlmChat(FakeProvider()),
        emit_event=events.append,
    )
    return reg, results, events


def _run(reg, tid, params):
    t = reg.get(tid)
    return t.run(params, t.plugin_ctx)


def _mod_globals(reg):
    """插件模块命名空间（加载器不把模块挂进 sys.modules，走方法 __globals__ 拿，同 test_zimeiti）。"""
    return type(reg.get("agents.dispatch_task")).run.__globals__


def _wait_row(db, task_id, want_status, timeout=2.0):
    """轮询 db 等 daemon 线程落库到目标状态（最多 timeout 秒，超时返回最后一次读到的行）。"""
    deadline = time.time() + timeout
    row = None
    while time.time() < deadline:
        rows = db.query("tasks", where={"id": task_id})
        if rows:
            row = rows[0]
            if row["status"] == want_status:
                return row
        time.sleep(0.02)
    return row


def _wait_for(cond, timeout=2.0):
    deadline = time.time() + timeout
    while not cond() and time.time() < deadline:
        time.sleep(0.02)
    return cond()


# ---------- 假进程 ----------


class _FakeProc:
    """可控完成的假 Popen：finish(code) 前 wait 一直阻塞；terminate/kill 记录调用并结束。"""

    pid = 12345  # 假 pid：落库断言用（对账路径别拿它探活）

    def __init__(self):
        self.returncode = None
        self.terminated = False
        self.killed = False
        self._done = threading.Event()

    def finish(self, code=0):
        if self.returncode is None:
            self.returncode = code
        self._done.set()

    def wait(self, timeout=None):
        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.finish(-15)

    def kill(self):
        self.killed = True
        self.finish(-9)


class _PopenFactory:
    """fake Popen：记录 argv/cwd，往 log 文件写 log_text，返回可控假进程。"""

    def __init__(self):
        self.calls: list[dict] = []
        self.procs: list[_FakeProc] = []
        self.log_text = "hello result"

    def __call__(self, argv, cwd=None, stdin=None, stdout=None, stderr=None, text=None):
        self.calls.append({"argv": argv, "cwd": cwd, "stdin": stdin})
        if stdout is not None:
            stdout.write(self.log_text)
            stdout.flush()
        proc = _FakeProc()
        self.procs.append(proc)
        return proc


@pytest.fixture
def fake_popen(monkeypatch):
    """Popen/which 换成 fake：所有 CLI 智能体都存在，spawn 即写 log 并返回假进程。"""
    monkeypatch.setattr(shutil, "which", lambda b: f"/fake/bin/{b}")
    factory = _PopenFactory()
    monkeypatch.setattr(subprocess, "Popen", factory)
    return factory


# ---------- 加载 / 注册 ----------


def test_agents_loads_ok(env):
    reg, results, _ = env
    assert results["agents"] == "ok"
    expected = {
        "agents.dispatch_task": RiskLevel.L3_HIGH,
        "agents.task_status": RiskLevel.L0_READONLY,
        "agents.task_stop": RiskLevel.L2_MEDIUM,
        "agents.task_list": RiskLevel.L0_READONLY,
    }
    for tid, risk in expected.items():
        assert reg.get(tid).default_risk == risk, tid


def test_api_whitelist_and_panel_schema(env):
    _ = env  # 触发加载，get_api/get_panel 注册表才有内容
    for name in ("agents.task_list", "agents.task_status", "agents.task_stop"):
        api = get_api(name)
        assert api is not None and api.direct, name
    assert get_api("agents.task_stop").refresh == "agents.task_list"
    schema = json.loads((AGENTS_DIR / "panel/tasks.schema.json").read_text(encoding="utf-8"))
    assert schema["type"] == "list" and schema["bind"]["items"] == "$data.rows"
    assert schema["item"]["actions"], "面板没有条目 action"
    for a in schema["item"]["actions"]:  # 面板引用的 method 必须都在白名单（防手滑）
        assert get_api(a["method"]) is not None, a["method"]
    assert get_panel("agents:tasks") == schema


# ---------- 适配表（argv 构造 + 摘要解析）----------


def test_build_argv_claude_seatbelt(env, tmp_path):
    """claude 套 Seatbelt：sandbox-exec -f policy + 免权限参数；policy 落盘可审计。"""
    reg, _, _ = env
    g = _mod_globals(reg)
    logs = str(tmp_path / "logs")
    os.makedirs(logs)
    argv, err = g["_build_argv"]("claude", "/fake/bin/claude", "干活",
                                 str(tmp_path), logs, "t123")
    assert err is None
    policy_path = os.path.join(logs, "t123.sb")
    assert argv == ["/usr/sbin/sandbox-exec", "-f", policy_path, "/fake/bin/claude",
                    "-p", "干活", "--dangerously-skip-permissions", "--output-format", "json"] \
        or argv[0].endswith("sandbox-exec")  # which 路径随环境，关键看结构
    assert argv[2] == policy_path and argv[3] == "/fake/bin/claude"
    policy = Path(policy_path).read_text(encoding="utf-8")
    assert "(deny default)" in policy and "(allow network-outbound)" in policy  # claude 要调 API
    assert os.path.realpath(str(tmp_path)) in policy  # 写根含 cwd
    assert argv[argv.index("--dangerously-skip-permissions")]


def test_build_argv_claude_needs_macos(env, monkeypatch, tmp_path):
    """没有 sandbox-exec（非 macOS）→ 人话报错，不放行裸跑。"""
    reg, _, _ = env
    g = _mod_globals(reg)
    monkeypatch.setattr(shutil, "which", lambda b: None if b == "sandbox-exec" else f"/fake/bin/{b}")
    argv, err = g["_build_argv"]("claude", "/fake/bin/claude", "干活", str(tmp_path), str(tmp_path), "t1")
    assert argv is None and "沙箱" in err


def test_build_argv_codex_builtin_sandbox(env, tmp_path):
    """codex 用内建 workspace-write 沙箱，无需外套 Seatbelt。"""
    reg, _, _ = env
    g = _mod_globals(reg)
    argv, err = g["_build_argv"]("codex", "/fake/bin/codex", "干活", str(tmp_path), str(tmp_path), "t1")
    assert err is None
    assert argv == ["/fake/bin/codex", "exec", "--sandbox", "workspace-write",
                    "--skip-git-repo-check", "干活", "--json"]


def test_adapter_summaries(env):
    reg, _, _ = env
    g = _mod_globals(reg)
    claude = g["_AGENTS"]["claude"]["summarize"]
    log = json.dumps([
        {"type": "system", "subtype": "init"},
        {"type": "result", "result": "做了 A"},
        {"type": "result", "result": "做了 B"},
    ])
    assert claude(log) == "做了 B"  # 最后一个 type==result 项
    codex = g["_AGENTS"]["codex"]["summarize"]
    assert codex('{"type": "turn.started"}\n{"text": "最终答复"}') == "最终答复"
    assert codex('{"item": {"type": "agent_message", "text": "嵌套答复"}}') == "嵌套答复"
    # 能解析但没有文本字段 → 该行原文；完全解析不动 → _summarize 退化 log 尾部 500 字
    assert codex('not json\n{"usage": {"tokens": 3}}') == '{"usage": {"tokens": 3}}'
    assert g["_summarize"]("claude", "hello result") == "hello result"
    assert g["_summarize"]("claude", "x" * 1000) == "x" * 500


# ---------- dispatch_task 全链 ----------


def test_dispatch_full_chain(env, fake_popen, tmp_path):
    reg, results, events = env
    assert results["agents"] == "ok"
    fake_popen.log_text = json.dumps([
        {"type": "system", "subtype": "init"},
        {"type": "result", "result": "整理完成，共 3 个文件"},
    ])
    ctx = reg.get("agents.dispatch_task").plugin_ctx
    r = _run(reg, "agents.dispatch_task",
             {"agent": "claude", "prompt": "整理 README", "cwd": str(tmp_path), "timeout_min": 5})
    assert r.success
    task_id = r.data["task_id"]
    assert "已派给 claude" in r.data["human"] and task_id in r.data["human"]
    # argv / cwd / 日志落插件数据目录；claude 走 Seatbelt（policy 落盘可审计）
    call = fake_popen.calls[0]
    argv = call["argv"]
    assert argv[0].endswith("sandbox-exec") and argv[1] == "-f"
    assert argv[3:] == ["/fake/bin/claude", "-p", "整理 README",
                        "--dangerously-skip-permissions", "--output-format", "json"]
    assert Path(argv[2]).is_file(), "policy.sb 未落盘"
    assert call["cwd"] == os.path.realpath(str(tmp_path))
    assert call["stdin"] is subprocess.DEVNULL  # claude/codex 都会读 stdin，必须关掉
    row = ctx.db.query("tasks", where={"id": task_id})[0]
    log_path = Path(row["log_path"])
    assert log_path.is_file() and log_path.parent.name == "logs" and "agents" in str(log_path)
    # dispatch 立即返回：进程未结束时库里是 running 行（pid 落库供重启后对账）
    assert row["status"] == "running" and row["exit_code"] == -1 and row["created_at"] > 0
    assert row["pid"] == 12345
    assert task_id in _mod_globals(reg)["_PROCS"]
    # 进程完成 → daemon 线程落库 done + emit_event 播报（摘要走 claude result 解析）
    fake_popen.procs[0].finish(0)
    row = _wait_row(ctx.db, task_id, "done")
    assert row["status"] == "done" and row["exit_code"] == 0 and row["finished_at"] > 0
    assert _wait_for(lambda: len(events) == 1)
    assert _wait_for(lambda: task_id not in _mod_globals(reg)["_PROCS"])
    ev = events[0]
    assert ev["kind"] == "reminder"
    assert "✅" in ev["text"] and "整理 README" in ev["text"] and "整理完成，共 3 个文件" in ev["text"]


def test_dispatch_codex_argv(env, fake_popen, tmp_path):
    reg, _, _ = env
    r = _run(reg, "agents.dispatch_task", {"agent": "codex", "prompt": "查日志", "cwd": str(tmp_path)})
    assert r.success
    assert fake_popen.calls[0]["argv"] == [
        "/fake/bin/codex", "exec", "--sandbox", "workspace-write",
        "--skip-git-repo-check", "查日志", "--json"]
    fake_popen.procs[0].finish(0)  # 收尾，别留挂着 daemon 线程的假进程


def test_dispatch_failure_exit_code(env, fake_popen, tmp_path):
    reg, _, events = env
    ctx = reg.get("agents.dispatch_task").plugin_ctx
    r = _run(reg, "agents.dispatch_task", {"agent": "claude", "prompt": "必炸", "cwd": str(tmp_path)})
    task_id = r.data["task_id"]
    fake_popen.procs[0].finish(2)
    row = _wait_row(ctx.db, task_id, "failed")
    assert row["status"] == "failed" and row["exit_code"] == 2
    assert _wait_for(lambda: len(events) == 1)
    assert "❌" in events[0]["text"] and "hello result" in events[0]["text"]  # 非 json → 摘要退化 log 尾部


# ---------- dispatch 校验错误路径 ----------


def test_dispatch_rejects_unknown_agent(env, tmp_path):
    reg, _, _ = env
    r = _run(reg, "agents.dispatch_task", {"agent": "kimi", "prompt": "P", "cwd": str(tmp_path)})
    assert not r.success and "可用" in r.error and "claude" in r.error and "codex" in r.error


def test_dispatch_rejects_missing_binary(env, monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda b: None)
    reg, _, _ = env
    monkeypatch.setitem(_mod_globals(reg), "_CLI_FALLBACK_DIRS", ())  # 隔离真机上的 CLI 安装
    r = _run(reg, "agents.dispatch_task", {"agent": "claude", "prompt": "P", "cwd": str(tmp_path)})
    assert not r.success and "未安装或未登录" in r.error


def test_find_cli_fallback_dirs(env, monkeypatch, tmp_path):
    """PATH 找不到时补查 fallback 目录（GUI spawn 的大脑 PATH 很薄）。"""
    reg, _, _ = env
    g = _mod_globals(reg)
    monkeypatch.setattr(shutil, "which", lambda b: None)
    fake_bin = tmp_path / "claude"
    fake_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    fake_bin.chmod(0o755)
    monkeypatch.setitem(g, "_CLI_FALLBACK_DIRS", (str(tmp_path),))
    assert g["_find_cli"]("claude") == str(fake_bin)
    assert g["_find_cli"]("nope") is None  # fallback 里也没有 → None


def test_dispatch_rejects_bad_cwd(env, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda b: b)
    reg, _, _ = env
    r = _run(reg, "agents.dispatch_task", {"agent": "claude", "prompt": "P", "cwd": "/nonexistent-xyz-123"})
    assert not r.success and "工作目录不存在" in r.error


def test_dispatch_rejects_empty_prompt(env, tmp_path):
    reg, _, _ = env
    r = _run(reg, "agents.dispatch_task", {"agent": "claude", "prompt": "  ", "cwd": str(tmp_path)})
    assert not r.success and "prompt" in r.error


# ---------- task_status / task_list ----------


def test_task_status(env, fake_popen, tmp_path):
    reg, _, _ = env
    ctx = reg.get("agents.dispatch_task").plugin_ctx
    r = _run(reg, "agents.dispatch_task", {"agent": "claude", "prompt": "P", "cwd": str(tmp_path)})
    task_id = r.data["task_id"]
    fake_popen.procs[0].finish(0)
    _wait_row(ctx.db, task_id, "done")
    s = _run(reg, "agents.task_status", {"id": task_id})
    assert s.success
    assert s.data["row"]["id"] == task_id and s.data["row"]["status"] == "done"
    assert "hello result" in s.data["log_tail"] and "done" in s.data["human"]
    assert not _run(reg, "agents.task_status", {"id": "nope"}).success


def test_task_list_declarative(env, fake_popen, tmp_path):
    reg, _, _ = env
    r = _run(reg, "agents.dispatch_task", {"agent": "claude", "prompt": "P", "cwd": str(tmp_path)})
    lst = _run(reg, "agents.task_list", {})
    assert lst.success and lst.panel == "agents:tasks"  # 成功才带面板引用
    rows = lst.data["rows"]
    assert [row["id"] for row in rows] == [r.data["task_id"]]
    assert rows[0]["status"] == "running" and rows[0]["agent"] == "claude"
    fake_popen.procs[0].finish(0)  # 收尾


# ---------- task_stop ----------


def test_task_stop(env, fake_popen, tmp_path):
    reg, _, _ = env
    g = _mod_globals(reg)
    ctx = reg.get("agents.dispatch_task").plugin_ctx
    r = _run(reg, "agents.dispatch_task", {"agent": "claude", "prompt": "长跑任务", "cwd": str(tmp_path)})
    task_id = r.data["task_id"]
    assert task_id in g["_PROCS"]
    s = _run(reg, "agents.task_stop", {"id": task_id})
    assert s.success and s.data["id"] == task_id
    assert fake_popen.procs[0].terminated
    # stop 先落 stopped 再 terminate：_wait 醒来看见 stopped 保留，不被退出码翻成 failed
    row = _wait_row(ctx.db, task_id, "stopped")
    assert row["status"] == "stopped"
    assert _wait_for(lambda: task_id not in g["_PROCS"])
    s2 = _run(reg, "agents.task_stop", {"id": task_id})
    assert not s2.success and "已结束" in s2.error  # 已结束的任务再 stop 报错


def test_task_stop_missing_task(env):
    reg, _, _ = env
    assert not _run(reg, "agents.task_stop", {"id": "nope"}).success


def test_task_stop_lost_handle(env, fake_popen, tmp_path):
    reg, _, _ = env
    g = _mod_globals(reg)
    r = _run(reg, "agents.dispatch_task", {"agent": "claude", "prompt": "P", "cwd": str(tmp_path)})
    task_id = r.data["task_id"]
    g["_PROCS"].pop(task_id)  # 模拟底座重启后句柄丢失（pid 12345 也不可能活着）
    s = _run(reg, "agents.task_stop", {"id": task_id})
    assert not s.success and "进程句柄已丢失" in s.error
    fake_popen.procs[0].finish(0)  # 收尾


def test_task_stop_pid_fallback(env, tmp_path):
    """句柄丢失但 pid 还活着（重启后）：先落 stopped，再按 pid SIGTERM 兜底。"""
    reg, _, _ = env
    ctx = reg.get("agents.dispatch_task").plugin_ctx
    proc = subprocess.Popen(["sleep", "30"], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        ctx.db.insert("tasks", {"id": "tstop", "agent": "claude", "prompt": "P", "cwd": "/tmp",
                                "status": "running", "pid": proc.pid,
                                "created_at": int(time.time())})
        s = _run(reg, "agents.task_stop", {"id": "tstop"})
        assert s.success, s.error
        assert proc.wait(timeout=5) == -signal.SIGTERM
        assert ctx.db.query("tasks", where={"id": "tstop"})[0]["status"] == "stopped"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


# ---------- 重启后对账（_reconcile_orphans / _reap_detached）----------


def test_pid_alive_basics(env):
    reg, _, _ = env
    g = _mod_globals(reg)
    alive = g["_common"]._pid_alive
    assert alive(os.getpid())
    assert not alive(999999)  # 超过 macOS pid 上限，必不存在
    assert not alive(0) and not alive(-1)


def test_reconcile_dead_pid_marks_interrupted(env):
    """running 行 pid 已死（重启带走了 _wait 线程）→ 落 interrupted。"""
    reg, _, _ = env
    g = _mod_globals(reg)
    ctx = reg.get("agents.dispatch_task").plugin_ctx
    ctx.db.insert("tasks", {"id": "zombie", "agent": "claude", "prompt": "P", "cwd": "/tmp",
                            "status": "running", "pid": 999999,
                            "created_at": int(time.time())})
    g["_reconcile_orphans"](ctx)
    row = ctx.db.query("tasks", where={"id": "zombie"})[0]
    assert row["status"] == "interrupted" and row["finished_at"] > 0


def test_reconcile_alive_pid_reaps_and_broadcasts(env):
    """running 行 pid 还活着 → 挂探活线程，进程死后落 done + 播报（exit_code 保持 -1 未知）。"""
    reg, _, events = env
    g = _mod_globals(reg)
    ctx = reg.get("agents.dispatch_task").plugin_ctx
    proc = subprocess.Popen(["sleep", "0.2"], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # 测试里父进程（pytest）还活着，得有人 wait 收尸——否则僵尸会骗过 kill(0) 探活。
    # 生产上孤儿被 launchd 收尸，无此问题。
    threading.Thread(target=proc.wait, daemon=True).start()
    try:
        ctx.db.insert("tasks", {"id": "orphan", "agent": "claude", "prompt": "遗留任务", "cwd": "/tmp",
                                "status": "running", "pid": proc.pid,
                                "created_at": int(time.time())})
        g["_reconcile_orphans"](ctx)
        row = _wait_row(ctx.db, "orphan", "done", timeout=8.0)  # 探活间隔 2s，留足余量
        assert row["status"] == "done" and row["exit_code"] == -1 and row["finished_at"] > 0
        assert _wait_for(lambda: any("遗留任务" in e.get("text", "") for e in events), timeout=8.0)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_reap_detached_preserves_stopped(env):
    """对账任务被 task_stop 按 pid 停掉：探活线程保留 stopped，不翻成 done。"""
    reg, _, events = env
    g = _mod_globals(reg)
    ctx = reg.get("agents.dispatch_task").plugin_ctx
    proc = subprocess.Popen(["sleep", "30"], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    threading.Thread(target=proc.wait, daemon=True).start()  # 收尸防僵尸（同上）
    try:
        ctx.db.insert("tasks", {"id": "reap_stop", "agent": "claude", "prompt": "P", "cwd": "/tmp",
                                "status": "running", "pid": proc.pid,
                                "created_at": int(time.time())})
        g["_reconcile_orphans"](ctx)  # 挂探活线程
        s = _run(reg, "agents.task_stop", {"id": "reap_stop"})
        assert s.success, s.error
        row = _wait_row(ctx.db, "reap_stop", "stopped", timeout=8.0)  # 探活线程醒来后仍 stopped
        assert row["status"] == "stopped"
        assert _wait_for(lambda: any("⏹" in e.get("text", "") for e in events), timeout=8.0)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


# ---------- precheck 启发式拦截 ----------


def test_dispatch_precheck_blocks_one_shot(env):
    """短描述 + 一次性任务关键词 → 拦截并指路 code_exec；开放任务/长描述放行。"""
    reg, _, _ = env
    t = reg.get("agents.dispatch_task")
    reason = t.precheck({"agent": "claude", "prompt": "统计一下这个目录有多少行代码", "cwd": "/tmp"})
    assert reason and "code_exec" in reason
    assert t.precheck({"agent": "claude", "prompt": "格式化这些 JSON 文件", "cwd": "/tmp"}) is not None
    open_task = "调查并修复这个仓库偶发的测试失败：需要先读代码定位，再改，再跑测试迭代验证"
    assert t.precheck({"agent": "claude", "prompt": open_task, "cwd": "/tmp"}) is None
    long_prompt = "统计" + "背景说明" * 60  # 长描述即使含关键词也放行（越详细越像真开放任务）
    assert t.precheck({"agent": "claude", "prompt": long_prompt, "cwd": "/tmp"}) is None


# ---------- _wait 单元路径（超时 / emit_event 为 None）----------


def test_wait_timeout_kills_and_reports(env):
    reg, _, events = env
    g = _mod_globals(reg)
    ctx = reg.get("agents.dispatch_task").plugin_ctx
    ctx.db.insert("tasks", {"id": "t1", "agent": "claude", "prompt": "P", "cwd": "/tmp",
                            "status": "running", "created_at": int(time.time())})
    proc = _FakeProc()  # 永不完成 → 0.05s 后超时
    g["_wait"](proc, "t1", "claude", "P", "/nonexistent.log", 0.05, ctx)
    assert proc.killed
    row = ctx.db.query("tasks", where={"id": "t1"})[0]
    assert row["status"] == "failed" and row["exit_code"] == -9 and row["finished_at"] > 0
    assert any(e["kind"] == "reminder" and "⏰" in e["text"] for e in events)


def test_wait_skips_emit_when_none(env):
    reg, _, events = env
    g = _mod_globals(reg)
    ctx = reg.get("agents.dispatch_task").plugin_ctx
    ctx.emit_event = None  # 未走 serve 注入（测试/直跑环境）：静默跳过不炸
    ctx.db.insert("tasks", {"id": "t9", "agent": "claude", "prompt": "P", "cwd": "/tmp",
                            "status": "running", "created_at": int(time.time())})
    proc = _FakeProc()
    proc.finish(0)
    g["_wait"](proc, "t9", "claude", "P", "/nonexistent.log", 5, ctx)
    assert ctx.db.query("tasks", where={"id": "t9"})[0]["status"] == "done"
    assert events == []
