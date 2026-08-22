"""JobsStore：watch_command 后台任务持久化（跨重启恢复，v1.1）。

FeedStore 同款模式：check_same_thread=False + 锁 + 幂等迁移 + 写失败只 print 不抛——
任务恢复是增强面，永远不许拖垮启动链路。
"""
from __future__ import annotations

from .log import log
import os
import sqlite3
import sys
import threading
import time


_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  task_id TEXT PRIMARY KEY,
  command TEXT NOT NULL,
  cwd TEXT NOT NULL,
  name TEXT NOT NULL,
  timeout REAL NOT NULL,
  status TEXT NOT NULL,
  exit_code INTEGER,
  output_tail TEXT NOT NULL DEFAULT '',
  started_at REAL NOT NULL,
  finished_at REAL
);
"""


class JobsStore:
    def __init__(self, db_path: str):
        self._conn = None
        self._lock = threading.Lock()
        try:
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            with self._lock:
                self._conn.executescript(_SCHEMA)
        except Exception as e:
            log(f"jobs 存储初始化失败（已降级为不持久化）：{e}")

    def add(self, job: dict) -> None:
        if self._conn is None:
            return
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT OR REPLACE INTO jobs (task_id, command, cwd, name, timeout, status,"
                    " exit_code, output_tail, started_at, finished_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (job["task_id"], job["command"], job["cwd"], job["name"], job["timeout"],
                     job["status"], job["exit_code"], job["output_tail"], job["started_at"],
                     job["finished_at"]),
                )
                self._conn.commit()
        except Exception as e:
            log(f"jobs 写入失败（已跳过）：{e}")

    def finish(self, task_id: str, *, status: str, exit_code, output_tail: str) -> None:
        if self._conn is None:
            return
        try:
            with self._lock:
                self._conn.execute(
                    "UPDATE jobs SET status=?, exit_code=?, output_tail=?, finished_at=? WHERE task_id=?",
                    (status, exit_code, output_tail, time.time(), task_id),
                )
                self._conn.commit()
        except Exception as e:
            log(f"jobs 更新失败（已跳过）：{e}")

    def running(self) -> list[dict]:
        if self._conn is None:
            return []
        try:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT * FROM jobs WHERE status = 'running' ORDER BY started_at"
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            log(f"jobs 查询失败（已降级为空）：{e}")
            return []

    def mark_interrupted(self, task_id: str) -> None:
        if self._conn is None:
            return
        try:
            with self._lock:
                self._conn.execute(
                    "UPDATE jobs SET status='interrupted', finished_at=? WHERE task_id=?",
                    (time.time(), task_id),
                )
                self._conn.commit()
        except Exception as e:
            log(f"jobs 标记失败（已跳过）：{e}")

    def close(self) -> None:
        if self._conn is not None:
            with self._lock:
                self._conn.close()
