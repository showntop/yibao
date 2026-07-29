"""FeedStore：主屏 Feed 流的底座存储（OS 感设计 §4.2——「它在我不看的时候干了什么」）。

append-only SQLite：任何「值得让主人知道」的动态在发生时刻写入（任务收尾播报、提醒触发），
主屏打开时一次查询拿回。写失败只 print——Feed 是增强面，永远不许拖垮主链路。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time

_SCHEMA = """
CREATE TABLE IF NOT EXISTS feed (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  kind TEXT NOT NULL,
  text TEXT NOT NULL,
  meta TEXT NOT NULL DEFAULT '{}',
  read INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_feed_ts ON feed(ts);
"""

_KINDS = ("task", "reminder", "event")  # task=任务收尾播报；reminder=提醒触发；event=其它主动事件


class FeedStore:
    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            # 幂等迁移：老库没有 read 列 → 补上（存量行天然 read=0）
            try:
                self._conn.execute("ALTER TABLE feed ADD COLUMN read INTEGER NOT NULL DEFAULT 0")
                self._conn.commit()
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise

    def add(self, kind: str, text: str, meta: dict | None = None) -> None:
        """追加一条动态；任何失败只 print 不抛（见模块 docstring）。"""
        if kind not in _KINDS:
            kind = "event"
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO feed (ts, kind, text, meta) VALUES (?, ?, ?, ?)",
                    (time.time(), kind, text, json.dumps(meta or {}, ensure_ascii=False)),
                )
                self._conn.commit()
        except Exception as e:
            print(f"[yibao] feed 写入失败（已跳过）：{e}", file=sys.stderr)

    def recent(self, limit: int = 60, since: float | None = None) -> list[dict]:
        """按时间倒序取动态。meta JSON 解析失败退化为 {}。"""
        sql = "SELECT id, ts, kind, text, meta, read FROM feed"
        args: list = []
        if since is not None:
            sql += " WHERE ts >= ?"
            args.append(since)
        sql += " ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        out = []
        for r in rows:
            try:
                meta = json.loads(r["meta"] or "{}")
            except json.JSONDecodeError:
                meta = {}
            out.append({
                "id": r["id"],
                "ts": r["ts"],
                "kind": r["kind"],
                "text": r["text"],
                "meta": meta,
                "read": int(r["read"] or 0),
            })
        return out

    def count_since(self, kind: str, ts: float) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM feed WHERE kind = ? AND ts >= ?", (kind, ts)
            ).fetchone()
        return int(row["n"]) if row else 0

    def count_unread(self) -> int:
        """未读条数。读失败只 print 不抛（Feed 是增强面）。"""
        try:
            with self._lock:
                row = self._conn.execute("SELECT COUNT(*) FROM feed WHERE read = 0").fetchone()
            return int(row[0]) if row else 0
        except Exception as e:
            print(f"[yibao] feed 计数失败（已降级为 0）：{e}", file=sys.stderr)
            return 0

    def mark_read(self, feed_id: int) -> bool:
        """标记单条已读。写失败只 print 不抛，返回 False。"""
        try:
            with self._lock:
                cur = self._conn.execute("UPDATE feed SET read = 1 WHERE id = ?", (feed_id,))
                self._conn.commit()
            return cur.rowcount > 0
        except Exception as e:
            print(f"[yibao] feed 标记已读失败（已跳过）：{e}", file=sys.stderr)
            return False

    def mark_all_read(self) -> int:
        """全部标记已读，返回受影响行数。写失败只 print 不抛，返回 0。"""
        try:
            with self._lock:
                cur = self._conn.execute("UPDATE feed SET read = 1 WHERE read = 0")
                self._conn.commit()
            return cur.rowcount
        except Exception as e:
            print(f"[yibao] feed 全部标记已读失败（已跳过）：{e}", file=sys.stderr)
            return 0

    def close(self) -> None:
        with self._lock:
            self._conn.close()
