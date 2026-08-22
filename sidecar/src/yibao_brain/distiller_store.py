"""Distiller 的存储层：distill.db（提炼原料 + 运行记录）。

R-32a 从 distiller.py 拆出（2026-08-22）：存储与编排分离。distiller.py 保留
Distiller（编排）与纯函数（auto_run_due/yesterday_window/gather_summary/
parse_distill_output/recap_select/build_recap_text），并 re-export DistillerStore
（server.py / 测试的 `from .distiller import DistillerStore` 路径不变）。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import date, timedelta

from .log import log


_SCHEMA = """
CREATE TABLE IF NOT EXISTS distillations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  day TEXT NOT NULL,
  kind TEXT NOT NULL,
  text TEXT NOT NULL,
  data TEXT,
  confidence REAL,
  projected INTEGER DEFAULT 0,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_day TEXT NOT NULL,
  target_day TEXT NOT NULL,
  source TEXT NOT NULL,
  status TEXT NOT NULL,
  error TEXT,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

_KINDS = ("pattern", "insight", "event")
_KEEP_DAYS = 14


class DistillerStore:
    """distill.db：提炼原料（distillations）+ 运行记录（runs）。明文——内容已是提炼后人话。"""

    def __init__(self, db_path: str):
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        try:
            self._conn.execute("ALTER TABLE runs ADD COLUMN stats TEXT")
            self._conn.commit()
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise
        self._lock = threading.Lock()

    def add(self, day: str, kind: str, text: str,
            data: dict | None = None, confidence: float | None = None) -> int | None:
        if kind not in _KINDS:
            kind = "event"
        try:
            with self._lock:
                cur = self._conn.execute(
                    "INSERT INTO distillations (day, kind, text, data, confidence, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (day, kind, text, json.dumps(data or {}, ensure_ascii=False),
                     confidence, time.time()),
                )
                self._conn.commit()
                return int(cur.lastrowid)
        except Exception as e:
            log(f"提炼落库失败：{e}")
            return None

    def mark_projected(self, ids: list[int]) -> None:
        if not ids:
            return
        try:
            with self._lock:
                self._conn.execute(
                    f"UPDATE distillations SET projected = 1 WHERE id IN ({','.join('?' * len(ids))})",
                    ids,
                )
                self._conn.commit()
        except Exception as e:
            log(f"提炼投影标记失败：{e}")

    def day_items(self, day: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM distillations WHERE day = ? ORDER BY id", (day,),
        ).fetchall()
        return [
            {
                "id": int(r["id"]),
                "day": r["day"],
                "kind": r["kind"],
                "text": r["text"],
                "data": json.loads(r["data"] or "{}"),
                "confidence": r["confidence"],
                "projected": int(r["projected"]),
                "created_at": float(r["created_at"]),
            }
            for r in rows
        ]

    def set_recap_day(self, day: str) -> None:
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO meta(key,value) VALUES('recap_last_day',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (day,))
                self._conn.commit()
        except Exception as e:
            log(f"recap 标记失败：{e}")

    def recap_last_day(self) -> str | None:
        try:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key='recap_last_day'").fetchone()
            return str(row["value"]) if row else None
        except Exception as e:
            log(f"recap 标记读取失败：{e}")
            return None

    def record_run(self, run_day: str, target_day: str, source: str,
                   status: str, error: str | None = None, stats: dict | None = None) -> None:
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO runs (run_day, target_day, source, status, error, stats, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (run_day, target_day, source, status, error,
                     json.dumps(stats, ensure_ascii=False) if stats else None, time.time()),
                )
                self._conn.commit()
        except Exception as e:
            log(f"提炼运行记录失败：{e}")

    def last_auto_run_day(self) -> str | None:
        row = self._conn.execute(
            "SELECT run_day FROM runs WHERE source = 'auto' ORDER BY id DESC LIMIT 1",
        ).fetchone()
        return str(row["run_day"]) if row else None

    def purge(self, *, now: float | None = None) -> int:
        """14 天滚动清理 distillations 与 runs。"""
        cutoff = (now if now is not None else time.time()) - _KEEP_DAYS * 86400
        try:
            with self._lock:
                a = self._conn.execute(
                    "DELETE FROM distillations WHERE created_at < ?", (cutoff,),
                ).rowcount
                b = self._conn.execute(
                    "DELETE FROM runs WHERE created_at < ?", (cutoff,),
                ).rowcount
                self._conn.commit()
                return int(a or 0) + int(b or 0)
        except Exception as e:
            log(f"提炼原料清理失败：{e}")
            return 0

    def recent_days(self, n: int = 14) -> list[dict]:
        """近 n 天：每天聚合 runs(状态+stats) 与 distillations(items)。无 run 的天 status=pending。"""
        try:
            today = date.today()
            days = [(today - timedelta(days=i)).isoformat() for i in range(n)]
            out: list[dict] = []
            for day in days:
                items = self.day_items(day)
                row = self._conn.execute(
                    "SELECT status, stats FROM runs WHERE target_day=? "
                    "ORDER BY id DESC LIMIT 1", (day,)).fetchone()
                if row:
                    status = str(row["status"])
                    stats = json.loads(row["stats"] or "{}")
                else:
                    status, stats = "pending", {}
                out.append({"day": day, "status": status, "stats": stats, "items": items})
            return out
        except Exception as e:
            log(f"recent_days 查询失败：{e}")
            return []

    def close(self) -> None:
        self._conn.close()
