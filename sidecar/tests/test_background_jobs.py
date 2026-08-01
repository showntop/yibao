from __future__ import annotations

import sys
import time

from yibao_brain.background_jobs import BackgroundJobManager


def _wait_for(manager: BackgroundJobManager, task_id: str, states: set[str], timeout: float = 3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = manager.status(task_id)
        if job and job["status"] in states:
            return job
        time.sleep(0.02)
    raise AssertionError(f"task {task_id} did not reach {states}: {manager.status(task_id)}")


def test_background_job_uses_cwd_reports_id_and_bounded_tail(tmp_path):
    events = []
    manager = BackgroundJobManager(tail_chars=80)
    command = (
        f"{sys.executable} -c \"import os; print(os.getcwd()); print('x' * 200)\""
    )

    job = manager.start(command, cwd=str(tmp_path), name="cwd-check", emit=events.append)

    assert job["task_id"].startswith("job_")
    done = _wait_for(manager, job["task_id"], {"completed"})
    assert done["cwd"] == str(tmp_path)
    assert len(done["output_tail"]) <= 80
    assert events and events[-1]["task_id"] == job["task_id"]


def test_background_job_can_be_cancelled_and_shutdown_is_idempotent(tmp_path):
    manager = BackgroundJobManager()
    job = manager.start("sleep 10", cwd=str(tmp_path), name="long")
    _wait_for(manager, job["task_id"], {"running"})

    assert manager.cancel(job["task_id"]) is True
    stopped = _wait_for(manager, job["task_id"], {"cancelled"})
    assert stopped["status"] == "cancelled"
    assert manager.cancel("missing") is False
    manager.shutdown()
    manager.shutdown()


def test_background_job_rejects_missing_or_invalid_cwd(tmp_path):
    manager = BackgroundJobManager()

    for cwd in ("", str(tmp_path / "missing")):
        try:
            manager.start("echo hi", cwd=cwd)
        except ValueError as exc:
            assert "cwd" in str(exc)
        else:
            raise AssertionError("invalid cwd must be rejected")


def test_manager_persists_start_and_finish(tmp_path):
    """start 落库 running；完成更新终态。"""
    from yibao_brain.jobstore import JobsStore

    store = JobsStore(str(tmp_path / "jobs.db"))
    manager = BackgroundJobManager(store=store)
    job = manager.start("exit 0", cwd=str(tmp_path), name="p")
    assert [j["task_id"] for j in store.running()] == [job["task_id"]]
    _wait_for(manager, job["task_id"], {"completed"})
    assert store.running() == []
    manager.shutdown()
    store.close()


def test_recover_orphans_restarts_and_marks(tmp_path):
    """孤儿任务：可重跑的重跑，cwd 消失的标 interrupted。"""
    from yibao_brain.jobstore import JobsStore

    store = JobsStore(str(tmp_path / "jobs.db"))
    m1 = BackgroundJobManager(store=store)
    j1 = m1.start("sleep 30", cwd=str(tmp_path), name="可重跑")
    # 坏 cwd 的孤儿 start 时会拒绝——直接手工落库模拟上代遗物
    store.add({"task_id": "job_gone", "command": "sleep 30", "cwd": "/definitely/not/exist",
               "name": "不可重跑", "timeout": 600.0, "status": "running", "exit_code": None,
               "output_tail": "", "started_at": 1.0, "finished_at": None})
    # 不 shutdown m1（模拟崩溃）；新 manager 接管同库
    m2 = BackgroundJobManager(store=store)
    results = m2.recover_orphans(
        restart=lambda orphan: m2.start(orphan["command"], cwd=orphan["cwd"],
                                        name=orphan["name"], timeout=orphan["timeout"]))
    by_id = {r["orphan"]: r["outcome"] for r in results}
    assert by_id[j1["task_id"]] == "restarted"
    assert by_id["job_gone"] == "interrupted"
    m2.shutdown()
    store.close()
