"""agents.code_exec 沙箱技能测试：profile 生成 / 参数校验 / 同步窗口 / fake Popen 全链 / task_stop / 真沙箱冒烟。

照 test_agents_plugin.py 范式：加载真实 plugins/agents/（数据目录重定向到 tmp），
subprocess.Popen 换 fake 时 daemon 线程的落库与 emit_event 播报走真实路径；
同步窗口（_SYNC_WAIT_SEC）可经模块 globals patch：异步路径用例 patch 成 0 保持秒回；
冒烟用例（有 sandbox-exec 才跑）走真 Seatbelt 沙箱（短脚本走同步路径直接出结果）。
"""
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from yibao_brain.ipc import RiskLevel
from yibao_brain.llm import FakeProvider
from yibao_brain.memory import FakeMemory
from yibao_brain.plugins import LlmChat, load_plugins
from yibao_brain.tools import ToolRegistry

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """把插件数据目录指到 tmp（db/log/沙箱任务目录落盘不碰真实用户目录）。"""
    d = tmp_path / "data"
    monkeypatch.setenv("YIBAO_DATA_DIR", str(d))
    return d


@pytest.fixture
def env(data_dir):
    """加载真实插件目录；返回 (registry, 加载结果, emit_event 收到的事件)。"""
    reg = ToolRegistry()

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
    """sandbox.py 模块命名空间（加载器不把模块挂进 sys.modules，走方法 __globals__ 拿）。"""
    return type(reg.get("agents.code_exec")).run.__globals__


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

    pid = 12345  # 假 pid：落库断言用

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
    """fake Popen：记录 argv/cwd，往 log 文件写 log_text，返回可控假进程。

    auto_finish 非 None 时 spawn 出来的进程立即以该码完成（同步窗口路径用）。
    """

    def __init__(self):
        self.calls: list[dict] = []
        self.procs: list[_FakeProc] = []
        self.log_text = "hello result"
        self.auto_finish: int | None = None

    def __call__(self, argv, cwd=None, stdin=None, stdout=None, stderr=None, text=None):
        self.calls.append({"argv": argv, "cwd": cwd, "stdin": stdin})
        if stdout is not None:
            stdout.write(self.log_text)
            stdout.flush()
        proc = _FakeProc()
        self.procs.append(proc)
        if self.auto_finish is not None:
            proc.finish(self.auto_finish)
        return proc


@pytest.fixture
def fake_popen(monkeypatch):
    """Popen/which 换成 fake：sandbox-exec/解释器都"存在"，spawn 即写 log 并返回假进程。"""
    monkeypatch.setattr(shutil, "which", lambda b: f"/fake/bin/{b}")
    factory = _PopenFactory()
    monkeypatch.setattr(subprocess, "Popen", factory)
    return factory


@pytest.fixture
def fake_interp(monkeypatch, env):
    """解释器路径换 fake（/usr/bin/python3 是否真实存在不影响断言）。"""
    reg, _, _ = env
    monkeypatch.setitem(_mod_globals(reg), "_find_interpreter", lambda lang: f"/fake/bin/{lang}")
    return env


@pytest.fixture
def no_sync_window(monkeypatch, env):
    """同步等待窗口 patch 成 0：run() 秒回，用例专注 daemon 收尾路径（不然每个要等 25s）。"""
    monkeypatch.setitem(_mod_globals(env[0]), "_SYNC_WAIT_SEC", 0)
    return env


# ---------- 注册 / profile 生成 ----------


def test_code_exec_registered(env):
    reg, results, _ = env
    assert results["agents"] == "ok"
    assert reg.get("agents.code_exec").default_risk == RiskLevel.L3_HIGH


def test_profile_write_roots_realpath_and_git_deny(env):
    g = _mod_globals(env[0])
    build = g["_build_profile"]
    root = os.path.join("/tmp", "yibao_profile_test_xyz")  # macOS 上 realpath → /private/tmp/...
    real = os.path.realpath(root)
    p = build([root], False)
    assert f'(subpath "{real}")' in p  # 写根 realpath 规范化（subpath 按 realpath 匹配）
    assert f'(deny file-write* (subpath "{real}/.git"))' in p  # 每根一条 .git 写保护
    # 模板固定双写 /tmp 与 /private/tmp（/tmp 是 symlink）
    assert '(subpath "/private/tmp")' in p and '(subpath "/tmp")' in p
    # macOS 15.6 坑：file-read 必须全放，不能加过滤器（deny-default + process-exec + 受限读 → SIGABRT）
    assert "(allow file-read*)\n" in p
    assert "(deny default)" in p
    assert "(allow network-outbound)" not in p  # 默认断网


def test_profile_multi_roots_and_network(env):
    g = _mod_globals(env[0])
    build = g["_build_profile"]
    p = build(["/tmp/yibao_a_x", "/tmp/yibao_b_x"], True)
    assert p.count("(deny file-write*") == 2  # 两个写根各一条 .git deny
    assert p.strip().endswith("(allow network-outbound)")  # network=true 末尾追加


# ---------- 参数校验 ----------


def test_rejects_bad_lang(env, tmp_path):
    r = _run(env[0], "agents.code_exec", {"lang": "ruby", "code": "p 1", "cwd": str(tmp_path)})
    assert not r.success and "python" in r.error and "node" in r.error


def test_rejects_empty_code(env, tmp_path):
    r = _run(env[0], "agents.code_exec", {"lang": "python", "code": "  ", "cwd": str(tmp_path)})
    assert not r.success and "code" in r.error


def test_rejects_bad_cwd(env):
    r = _run(env[0], "agents.code_exec",
             {"lang": "python", "code": "print(1)", "cwd": "/nonexistent-xyz-123"})
    assert not r.success and "工作目录不存在" in r.error


def test_rejects_without_sandbox_exec(env, monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda b: None)
    r = _run(env[0], "agents.code_exec", {"lang": "python", "code": "print(1)", "cwd": str(tmp_path)})
    assert not r.success and "不支持沙箱执行" in r.error


def test_rejects_missing_interpreter(env, monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which",
                        lambda b: "/fake/bin/sandbox-exec" if b == "sandbox-exec" else None)
    r = _run(env[0], "agents.code_exec", {"lang": "node", "code": "console.log(1)", "cwd": str(tmp_path)})
    assert not r.success and "node" in r.error


# ---------- fake Popen 全链 ----------


def test_code_exec_full_chain(fake_interp, no_sync_window, fake_popen, tmp_path):
    reg, _, events = fake_interp
    code = "print('沙箱你好')\n" + "# padding 注释\n" * 40  # >200 字，验证 prompt 截断
    ctx = reg.get("agents.code_exec").plugin_ctx
    r = _run(reg, "agents.code_exec",
             {"lang": "python", "code": code, "cwd": str(tmp_path), "timeout_sec": 30, "network": True})
    assert r.success
    task_id = r.data["task_id"]
    assert "已在沙箱中运行" in r.data["human"] and task_id in r.data["human"]
    # argv：sandbox-exec -f policy.sb <解释器> script.py；cwd 按 realpath
    call = fake_popen.calls[0]
    cwd_real = os.path.realpath(str(tmp_path))
    assert call["argv"][0] == "/fake/bin/sandbox-exec" and call["argv"][1] == "-f"
    assert call["argv"][2].endswith("policy.sb")
    assert call["argv"][3] == "/fake/bin/python"
    assert call["argv"][4].endswith("script.py")
    assert call["cwd"] == cwd_real
    # 脚本/policy 落盘任务目录（可审计）；policy 内容 = 写限根 + .git 保护 + 联网开关
    task_dir = Path(call["argv"][2]).parent
    assert task_dir.name == task_id and task_dir.parent.name == "sandbox"
    assert (task_dir / "script.py").read_text(encoding="utf-8") == code
    policy = (task_dir / "policy.sb").read_text(encoding="utf-8")
    assert f'(subpath "{cwd_real}")' in policy
    assert f'(deny file-write* (subpath "{cwd_real}/.git"))' in policy
    assert "(allow network-outbound)" in policy
    # 立即返回：进程未结束时库里是 running 行，kind="script"
    row = ctx.db.query("tasks", where={"id": task_id})[0]
    assert row["kind"] == "script" and row["agent"] == "python" and row["status"] == "running"
    assert row["pid"] == 12345  # pid 落库：底座重启后对账用
    assert row["prompt"] == code[:200] and row["cwd"] == cwd_real
    assert row["log_path"] == str(task_dir / "output.log")
    assert task_id in _mod_globals(reg)["_PROCS"]
    # 进程完成 → daemon 线程落库 done + emit_event 播报（摘要=log 尾部）
    fake_popen.procs[0].finish(0)
    row = _wait_row(ctx.db, task_id, "done")
    assert row["status"] == "done" and row["exit_code"] == 0 and row["finished_at"] > 0
    assert _wait_for(lambda: len(events) == 1)
    assert _wait_for(lambda: task_id not in _mod_globals(reg)["_PROCS"])
    ev = events[0]
    assert ev["kind"] == "reminder"
    assert "✅ 沙箱脚本完成" in ev["text"] and "hello result" in ev["text"]


def test_code_exec_failure_reports(fake_interp, no_sync_window, fake_popen, tmp_path):
    reg, _, events = fake_interp
    ctx = reg.get("agents.code_exec").plugin_ctx
    r = _run(reg, "agents.code_exec", {"lang": "python", "code": "boom", "cwd": str(tmp_path)})
    task_id = r.data["task_id"]
    fake_popen.procs[0].finish(3)
    row = _wait_row(ctx.db, task_id, "failed")
    assert row["status"] == "failed" and row["exit_code"] == 3
    assert _wait_for(lambda: len(events) == 1)
    assert "❌ 沙箱脚本失败（退出码 3）" in events[0]["text"]


# ---------- 同步窗口：短任务当轮出答案 ----------


def test_code_exec_sync_fast_answer(fake_interp, fake_popen, tmp_path):
    """窗口内完成 → 输出直接进 ActionResult.data，不落库 running；同步路径也 emit 一次写 Feed。"""
    reg, _, events = fake_interp
    fake_popen.auto_finish = 0
    ctx = reg.get("agents.code_exec").plugin_ctx
    r = _run(reg, "agents.code_exec",
             {"lang": "python", "code": "print('hi')", "cwd": str(tmp_path), "timeout_sec": 60})
    assert r.success
    task_id = r.data["task_id"]
    assert "hello result" in r.data["output"]  # log 内容直接喂给 LLM
    assert f"✅ 沙箱脚本完成（任务 {task_id}）" in r.data["human"]
    row = ctx.db.query("tasks", where={"id": task_id})[0]
    assert row["status"] == "done" and row["exit_code"] == 0 and row["finished_at"] > 0
    # 同步路径也经 emit_event 写 Feed（kind=reminder + task meta，字段对齐 _common._wait）
    assert len(events) == 1 and events[0]["kind"] == "reminder"
    assert events[0]["task"] == {"id": task_id, "status": "done",
                                 "label": "沙箱脚本", "prompt": "print('hi')"}
    assert task_id not in _mod_globals(reg)["_PROCS"]


def test_code_exec_sync_emits_feed_event(fake_interp, fake_popen, tmp_path):
    """同步完成也经 emit_event 写 Feed：字段对齐 _common._wait（kind=reminder + task meta），
    底座 _on_plugin_event 凭 task meta 走 feed.add("task", ...)。done/failed/timeout/stopped 均发。"""
    reg, _, events = fake_interp
    fake_popen.auto_finish = 0
    ctx = reg.get("agents.code_exec").plugin_ctx
    code = "print('hi')"
    r = _run(reg, "agents.code_exec",
             {"lang": "python", "code": code, "cwd": str(tmp_path), "timeout_sec": 60})
    assert r.success
    task_id = r.data["task_id"]
    # 同步路径完成即 emit 一次（不再为空）
    assert len(events) == 1
    ev = events[0]
    # 字段对齐 _common._wait：kind=reminder（_on_plugin_event / _gate_proactive_event 按此分类）
    assert ev["kind"] == "reminder"
    assert "✅ 沙箱脚本完成" in ev["text"]
    # task meta：id 与落库行一致、status=done、label/prompt 给 Feed 点击追问用
    task_meta = ev["task"]
    assert task_meta["id"] == task_id
    assert task_meta["status"] == "done"
    assert task_meta["label"] == "沙箱脚本"
    assert task_meta["prompt"] == code[:120]
    # 落库行与 emit 的 status 一致（同步路径底座不再二次落库，Feed 只拿 emit 的 meta）
    row = ctx.db.query("tasks", where={"id": task_id})[0]
    assert row["status"] == task_meta["status"]


def test_code_exec_sync_emits_on_failure_timeout_stopped(fake_interp, fake_popen, tmp_path, monkeypatch):
    """同步路径三种非成功收尾也 emit：failed（非 0 退出）/ timeout（同步杀）/ stopped（task_stop）。"""
    reg, _, events = fake_interp
    monkeypatch.setitem(_mod_globals(reg), "_SYNC_WAIT_SEC", 1.2)
    ctx = reg.get("agents.code_exec").plugin_ctx

    # failed：非 0 退出
    fake_popen.auto_finish = 3
    fake_popen.log_text = "Traceback: boom"
    _run(reg, "agents.code_exec", {"lang": "python", "code": "boom", "cwd": str(tmp_path)})
    assert len(events) == 1 and events[0]["task"]["status"] == "failed"
    assert "❌ 沙箱脚本失败（退出码 3）" in events[0]["text"]
    assert "Traceback: boom" in events[0]["text"]

    # timeout：超时预算 ≤ 同步窗口 → 同步 kill
    fake_popen.auto_finish = None
    fake_popen.log_text = ""
    _run(reg, "agents.code_exec",
         {"lang": "python", "code": "import time; time.sleep(99)",
          "cwd": str(tmp_path), "timeout_sec": 1})
    assert len(events) == 2 and events[1]["task"]["status"] == "failed"
    assert "⏰ 沙箱脚本超时已终止" in events[1]["text"]

    # stopped：同步等待期间 task_stop
    fake_popen.auto_finish = None
    holder: dict = {}

    def _call():
        holder["r"] = _run(reg, "agents.code_exec",
                           {"lang": "python", "code": "import time; time.sleep(99)",
                            "cwd": str(tmp_path), "timeout_sec": 60})

    th = threading.Thread(target=_call)
    th.start()
    # 等 running 行出现（前两个子用例的 done/failed 行也在同库里，按状态过滤取 running 那行）
    assert _wait_for(lambda: any(r["status"] == "running"
                                 for r in ctx.db.query("tasks", where={"kind": "script"})))
    running = [r for r in ctx.db.query("tasks", where={"kind": "script"}) if r["status"] == "running"][0]
    _run(reg, "agents.task_stop", {"id": running["id"]})
    th.join(timeout=5)
    assert len(events) == 3 and events[2]["task"]["status"] == "stopped"
    assert "⏹ 沙箱脚本已停止" in events[2]["text"]


def test_code_exec_sync_failure_returns_output(fake_interp, fake_popen, tmp_path):
    """窗口内失败 → error 带退出码与输出尾部（LLM 当轮能看到报错并修脚本重试）；同步路径也 emit。"""
    reg, _, events = fake_interp
    fake_popen.auto_finish = 3
    fake_popen.log_text = "Traceback: boom"
    r = _run(reg, "agents.code_exec", {"lang": "python", "code": "boom", "cwd": str(tmp_path)})
    assert not r.success
    assert "退出码 3" in r.error and "Traceback: boom" in r.error
    assert len(events) == 1 and events[0]["task"]["status"] == "failed"


def test_code_exec_sync_failure_db_row(fake_interp, fake_popen, tmp_path):
    reg, _, _ = fake_interp
    fake_popen.auto_finish = 3
    ctx = reg.get("agents.code_exec").plugin_ctx
    r = _run(reg, "agents.code_exec", {"lang": "python", "code": "boom", "cwd": str(tmp_path)})
    assert not r.success
    # error 文本里带任务 id；从库里反查唯一行验证落库 failed
    rows = ctx.db.query("tasks", where={"kind": "script"})
    assert len(rows) == 1 and rows[0]["status"] == "failed" and rows[0]["exit_code"] == 3


def test_code_exec_sync_timeout_kills(fake_interp, fake_popen, tmp_path, monkeypatch):
    """超时预算 ≤ 同步窗口：窗口耗尽即同步杀掉，不转后台（timeout_sec=1 < 窗口 1.2s）。"""
    reg, _, events = fake_interp
    monkeypatch.setitem(_mod_globals(reg), "_SYNC_WAIT_SEC", 1.2)
    ctx = reg.get("agents.code_exec").plugin_ctx
    r = _run(reg, "agents.code_exec",
             {"lang": "python", "code": "import time; time.sleep(99)", "cwd": str(tmp_path),
              "timeout_sec": 1})
    assert not r.success and "超时被终止" in r.error
    assert fake_popen.procs[0].killed
    row = ctx.db.query("tasks", where={"kind": "script"})[0]
    assert row["status"] == "failed"
    # 同步路径超时也 emit（kind=reminder + task.status=failed，与 _common._wait 一致）
    assert len(events) == 1 and events[0]["kind"] == "reminder"
    assert events[0]["task"]["status"] == "failed"
    assert "⏰ 沙箱脚本超时已终止" in events[0]["text"]


def test_task_stop_during_sync_window(fake_interp, fake_popen, tmp_path, monkeypatch):
    """同步等待期间 task_stop 也能停（_PROCS 登记在 wait 之前）。"""
    reg, _, _ = fake_interp
    monkeypatch.setitem(_mod_globals(reg), "_SYNC_WAIT_SEC", 5)
    ctx = reg.get("agents.code_exec").plugin_ctx
    holder: dict = {}

    def _call():
        holder["r"] = _run(reg, "agents.code_exec",
                           {"lang": "python", "code": "import time; time.sleep(99)",
                            "cwd": str(tmp_path), "timeout_sec": 60})

    th = threading.Thread(target=_call)
    th.start()
    assert _wait_for(lambda: len(ctx.db.query("tasks", where={"kind": "script"})) == 1)
    task_id = ctx.db.query("tasks", where={"kind": "script"})[0]["id"]
    s = _run(reg, "agents.task_stop", {"id": task_id})
    assert s.success
    th.join(timeout=5)
    r = holder["r"]
    assert not r.success and "已被用户停止" in r.error
    row = ctx.db.query("tasks", where={"id": task_id})[0]
    assert row["status"] == "stopped"
    assert task_id not in _mod_globals(reg)["_PROCS"]



def test_task_stop_script_task(fake_interp, no_sync_window, fake_popen, tmp_path):
    reg, _, _ = fake_interp
    ctx = reg.get("agents.code_exec").plugin_ctx
    r = _run(reg, "agents.code_exec", {"lang": "python", "code": "import time; time.sleep(99)",
                                       "cwd": str(tmp_path)})
    task_id = r.data["task_id"]
    assert task_id in _mod_globals(reg)["_PROCS"]
    # agents.py 的 task_stop 停 sandbox.py 起的进程（_PROCS 跨模块共享的关键断言）
    s = _run(reg, "agents.task_stop", {"id": task_id})
    assert s.success and s.data["id"] == task_id
    assert fake_popen.procs[0].terminated
    row = _wait_row(ctx.db, task_id, "stopped")
    assert row["status"] == "stopped"
    assert _wait_for(lambda: task_id not in _mod_globals(reg)["_PROCS"])


# ---------- 真沙箱冒烟（需要 macOS sandbox-exec）----------

_has_sandbox = shutil.which("sandbox-exec") is not None


@pytest.mark.skipif(not _has_sandbox, reason="需要 macOS sandbox-exec")
def test_real_sandbox_hello_and_write_cwd(env, tmp_path):
    reg, _, events = env
    ctx = reg.get("agents.code_exec").plugin_ctx
    code = "print('hello sandbox')\nopen('out.txt', 'w').write('written-in-cwd')\n"
    r = _run(reg, "agents.code_exec",
             {"lang": "python", "code": code, "cwd": str(tmp_path), "timeout_sec": 60})
    assert r.success, r.error
    # 短脚本在同步窗口内完成：输出直接进 data，库落 done；同步路径也 emit 写 Feed
    assert "hello sandbox" in r.data["output"]
    row = ctx.db.query("tasks", where={"id": r.data["task_id"]})[0]
    assert row["status"] == "done" and row["exit_code"] == 0
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "written-in-cwd"  # 写 cwd 放行
    assert len(events) == 1 and events[0]["kind"] == "reminder"
    assert events[0]["task"]["status"] == "done" and events[0]["task"]["label"] == "沙箱脚本"


@pytest.mark.skipif(not _has_sandbox, reason="需要 macOS sandbox-exec")
def test_real_sandbox_blocks_home_write(env, tmp_path):
    reg, _, _ = env
    evil = os.path.expanduser("~/yibao_sbx_evil.txt")
    if os.path.exists(evil):
        os.remove(evil)
    code = (
        "import os\n"
        "try:\n"
        "    open(os.path.expanduser('~/yibao_sbx_evil.txt'), 'w').write('evil')\n"
        "    print('WROTE-EVIL')\n"
        "except Exception as e:\n"
        "    print(f'{type(e).__name__}: {e}')\n"
    )
    try:
        r = _run(reg, "agents.code_exec",
                 {"lang": "python", "code": code, "cwd": str(tmp_path), "timeout_sec": 60})
        assert r.success, r.error  # 脚本自己 catch 了异常 → exit 0，同步窗口内完成
        assert "WROTE-EVIL" not in r.data["output"]
        assert "PermissionError" in r.data["output"] or "Operation not permitted" in r.data["output"]
        assert not os.path.exists(evil)
    finally:
        if os.path.exists(evil):
            os.remove(evil)
