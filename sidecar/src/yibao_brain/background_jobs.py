"""Managed background shell jobs with bounded output and process-group cleanup."""
from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import Callable


_TERMINAL = {"completed", "failed", "timed_out", "cancelled"}


class BackgroundJobManager:
    def __init__(self, *, tail_chars: int = 500) -> None:
        self.tail_chars = max(80, int(tail_chars))
        self._jobs: dict[str, dict] = {}
        self._lock = threading.RLock()
        self._closed = False

    def start(
        self,
        command: str,
        *,
        cwd: str,
        name: str = "",
        timeout: float = 600,
        emit: Callable[[dict], None] | None = None,
    ) -> dict:
        command = str(command).strip()
        resolved_cwd = os.path.abspath(os.path.expanduser(str(cwd).strip())) if cwd else ""
        if not command:
            raise ValueError("缺少 command 参数")
        if not resolved_cwd or not os.path.isdir(resolved_cwd):
            raise ValueError("cwd 必须是存在的目录")
        with self._lock:
            if self._closed:
                raise RuntimeError("后台任务管理器已关闭")
            task_id = f"job_{uuid.uuid4().hex[:10]}"
            job = {
                "task_id": task_id,
                "command": command,
                "name": str(name).strip() or command,
                "cwd": resolved_cwd,
                "timeout": max(1.0, min(float(timeout), 86400.0)),
                "status": "running",
                "exit_code": None,
                "output_tail": "",
                "started_at": time.time(),
                "finished_at": None,
                "cancel_requested": False,
                "process": None,
                "thread": None,
            }
            self._jobs[task_id] = job
            thread = threading.Thread(
                target=self._run,
                args=(task_id, emit),
                daemon=True,
                name=f"yibao-{task_id}",
            )
            job["thread"] = thread
            thread.start()
            return self._public(job)

    def status(self, task_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(str(task_id))
            return self._public(job) if job is not None else None

    def list(self) -> list[dict]:
        with self._lock:
            return [self._public(job) for job in reversed(self._jobs.values())]

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(str(task_id))
            if job is None or job["status"] in _TERMINAL:
                return False
            job["cancel_requested"] = True
            process = job.get("process")
        if process is not None:
            self._terminate_group(process)
        return True

    def shutdown(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            active = [job for job in self._jobs.values() if job["status"] not in _TERMINAL]
            for job in active:
                job["cancel_requested"] = True
        for job in active:
            process = job.get("process")
            if process is not None:
                self._terminate_group(process)
        for job in active:
            thread = job.get("thread")
            if thread is not None:
                thread.join(timeout=2)

    def _run(self, task_id: str, emit: Callable[[dict], None] | None) -> None:
        with self._lock:
            job = self._jobs[task_id]
            command, cwd, timeout = job["command"], job["cwd"], job["timeout"]
        status = "failed"
        exit_code = None
        tail = ""
        try:
            with tempfile.TemporaryFile(mode="w+b") as output:
                process = subprocess.Popen(
                    command,
                    shell=True,
                    executable="/bin/sh",
                    cwd=cwd,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                with self._lock:
                    job["process"] = process
                    cancelled = bool(job["cancel_requested"])
                if cancelled:
                    self._terminate_group(process)
                try:
                    exit_code = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    self._terminate_group(process)
                    exit_code = process.wait(timeout=2)
                    status = "timed_out"
                output.seek(0, os.SEEK_END)
                size = output.tell()
                output.seek(max(0, size - self.tail_chars * 4))
                tail = output.read().decode("utf-8", errors="replace")[-self.tail_chars :].strip()
                with self._lock:
                    cancelled = bool(job["cancel_requested"])
                if cancelled:
                    status = "cancelled"
                elif status != "timed_out":
                    status = "completed" if exit_code == 0 else "failed"
        except Exception as exc:
            tail = str(exc)[-self.tail_chars :]
            with self._lock:
                status = "cancelled" if job["cancel_requested"] else "failed"
        with self._lock:
            job.update(
                status=status,
                exit_code=exit_code,
                output_tail=tail,
                finished_at=time.time(),
                process=None,
            )
            public = self._public(job)
        if emit is not None:
            labels = {
                "completed": "完成 ✅",
                "failed": f"失败（退出码 {exit_code}）❌",
                "timed_out": "超时已停止",
                "cancelled": "已取消",
            }
            text = f"「{job['name']}」{labels[status]}" + (f"：{tail}" if tail else "")
            try:
                emit({"kind": "reminder", "text": text, **public})
            except Exception:
                pass

    @staticmethod
    def _terminate_group(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        except ProcessLookupError:
            pass

    @staticmethod
    def _public(job: dict) -> dict:
        return {key: value for key, value in job.items() if key not in {"process", "thread", "cancel_requested"}}
