"""可审计操作日志：SQLite（截图路径入库，截图文件由调用方存盘）。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .ipc import Action, ActionResult


class AuditLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS actions (
                id TEXT PRIMARY KEY,
                ts TEXT DEFAULT (datetime('now')),
                skill_id TEXT,
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
        self.conn.execute(
            "INSERT INTO actions (id, skill_id, params, risk, success, error, data, screenshot_path)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                action.id,
                action.skill_id,
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
        cur = self.conn.execute("SELECT * FROM actions ORDER BY ts DESC LIMIT ?", (n,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def close(self) -> None:
        self.conn.close()
