"""agents 插件（智能体调度：dispatch/status/stop + 完成主动播报）端到端测试。

加载真实 plugins/agents/（数据目录重定向到 tmp）；subprocess.Popen 换 fake
（可控完成的假进程），daemon 线程的落库与 emit_event 播报走真实路径。
"""
import json
import shutil
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

    def __call__(self, argv, cwd=None, stdout=None, stderr=None, text=None):
        self.calls.append({"argv": argv, "cwd": cwd})
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


def test_adapters_argv(env):
    reg, _, _ = env
    g = _mod_globals(reg)
    assert g["_AGENTS"]["claude"]["argv"]("/fake/bin/claude", "干活") == [
        "/fake/bin/claude", "-p", "干活", "--output-format", "json"]
    assert g["_AGENTS"]["codex"]["argv"]("/fake/bin/codex", "干活") == [
        "/fake/bin/codex", "exec", "干活", "--json"]


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
    # argv / cwd / 日志落插件数据目录
    call = fake_popen.calls[0]
    assert call["argv"] == ["/fake/bin/claude", "-p", "整理 README", "--output-format", "json"]
    assert call["cwd"] == str(tmp_path)
    row = ctx.db.query("tasks", where={"id": task_id})[0]
    log_path = Path(row["log_path"])
    assert log_path.is_file() and log_path.parent.name == "logs" and "agents" in str(log_path)
    # dispatch 立即返回：进程未结束时库里是 running 行
    assert row["status"] == "running" and row["exit_code"] == -1 and row["created_at"] > 0
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
    assert fake_popen.calls[0]["argv"] == ["/fake/bin/codex", "exec", "查日志", "--json"]
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
    r = _run(reg, "agents.dispatch_task", {"agent": "claude", "prompt": "P", "cwd": str(tmp_path)})
    assert not r.success and "未安装或未登录" in r.error


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
    g["_PROCS"].pop(task_id)  # 模拟底座重启后句柄丢失
    s = _run(reg, "agents.task_stop", {"id": task_id})
    assert not s.success and "进程句柄已丢失" in s.error
    fake_popen.procs[0].finish(0)  # 收尾


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
