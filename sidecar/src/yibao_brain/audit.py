"""可审计操作日志：SQLite（截图路径入库，截图文件由调用方存盘）。"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from .ipc import Action, ActionResult


class AuditLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        # 连接在主线程创建、在 agent loop 的执行线程写（invoker 经 _offload 调 record），
        # 必须 check_same_thread=False + 锁（与 plugindb.py 同一模式）。
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS actions (
                    id TEXT PRIMARY KEY,
                    ts TEXT DEFAULT (datetime('now')),
                    tool_id TEXT,
                    params TEXT,
                    risk INTEGER,
                    success INTEGER,
                    error TEXT,
                    data TEXT,
                    screenshot_path TEXT
                )
                """
            )
            self.conn.commit()

    def record(self, action: Action, result: ActionResult, screenshot_path: str | None = None) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO actions (id, tool_id, params, risk, success, error, data, screenshot_path)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (
                    action.id,
                    action.tool_id,
                    json.dumps(action.params, ensure_ascii=False),
                    int(action.risk),
                    1 if result.success else 0,
                    result.error,
                    json.dumps(result.data, ensure_ascii=False),
                    screenshot_path,
                ),
            )
            self.conn.commit()

    def recent(self, n: int = 50) -> list[dict]:
        with self._lock:
            cur = self.conn.execute("SELECT * FROM actions ORDER BY ts DESC LIMIT ?", (n,))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def plugin_call_counts(self) -> dict[str, int]:
        """按 plugin id 聚合已执行 tool 次数（Dock 频率排序用）。

        audit 表无独立 plugin 列，但 tool_id 恒为 `<plugin_id>.<tool>`（DeclarativeTool
        强制前缀），取首个 `.` 前缀即 plugin id。NULL/空 tool_id 跳过；无记录返回 {}。
        """
        with self._lock:
            rows = self.conn.execute(
                "SELECT tool_id, COUNT(*) FROM actions GROUP BY tool_id"
            ).fetchall()
        counts: dict[str, int] = {}
        for tool_id, n in rows:
            if not tool_id:
                continue
            plugin = tool_id.split(".", 1)[0]
            counts[plugin] = counts.get(plugin, 0) + n
        return counts

    def close(self) -> None:
        with self._lock:
            self.conn.close()
