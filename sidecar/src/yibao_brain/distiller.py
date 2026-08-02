"""Distiller：感知观察的离线深加工层（感知 v3）。

每日凌晨（04:17）或手动触发，把昨日全量感知观察（A/C 时间段 + B 源文本）
预聚合后发给 LLM 提炼，产出三类结构化产物落 distill.db：
pattern → mem0（长期记忆）；insight（置信度 ≥0.6 的前 3 条）→ Feed；
event → Feed（按小时合并）。

纪律（与 feed 同款）：任何失败只记日志/状态，不抛给主链路；
perception.distill 关闭时零出站；未解析的 LLM 文本绝不投影。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
from datetime import date, datetime, timedelta

from .perception import build_activity_segments

_SUMMARY_CHAR_BUDGET = 20000   # 预聚合摘要字符上限（≈1 万 token）
_B_ENTRY_TEXT_LIMIT = 200      # 单条 B 源文本截断
_INSIGHT_MIN_CONFIDENCE = 0.6
_INSIGHT_MAX_PER_DAY = 3
_KEEP_DAYS = 14

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
"""

_KINDS = ("pattern", "insight", "event")


class DistillerStore:
    """distill.db：提炼原料（distillations）+ 运行记录（runs）。明文——内容已是提炼后人话。"""

    def __init__(self, db_path: str):
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
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
            print(f"[yibao] 提炼落库失败：{e}", file=sys.stderr)
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
            print(f"[yibao] 提炼投影标记失败：{e}", file=sys.stderr)

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

    def record_run(self, run_day: str, target_day: str, source: str,
                   status: str, error: str | None = None) -> None:
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO runs (run_day, target_day, source, status, error, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (run_day, target_day, source, status, error, time.time()),
                )
                self._conn.commit()
        except Exception as e:
            print(f"[yibao] 提炼运行记录失败：{e}", file=sys.stderr)

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
            print(f"[yibao] 提炼原料清理失败：{e}", file=sys.stderr)
            return 0

    def close(self) -> None:
        self._conn.close()


def auto_run_due(now: float, last_run_day: str | None, hour: int = 4, minute: int = 17) -> bool:
    """自动提炼是否到期：本地时间已过当日 hour:minute 且今日尚未自动跑过。"""
    lt = time.localtime(now)
    if (lt.tm_hour, lt.tm_min) < (hour, minute):
        return False
    return last_run_day != date.fromtimestamp(now).isoformat()
