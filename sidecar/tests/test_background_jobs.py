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
