"""FeedStore：主屏 Feed 流的底座存储（OS 感设计 §4.2——「它在我不看的时候干了什么」）。

append-only SQLite：任何「值得让主人知道」的动态在发生时刻写入（任务收尾播报、提醒触发），
主屏打开时一次查询拿回。写失败只 print——Feed 是增强面，永远不许拖垮主链路。
"""
from __future__ import annotations

from .log import log
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
  read INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'none'
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
            # 幂等迁移：status 列（C 子项目：跟进/忽略处置态，与 read 正交）
            try:
                self._conn.execute("ALTER TABLE feed ADD COLUMN status TEXT NOT NULL DEFAULT 'none'")
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
            log(f"feed 写入失败（已跳过）：{e}")

    def append_hourly(self, kind: str, text: str, meta: dict, hour_key: int) -> None:
        """按小时合并写：同 (kind, meta.type, hour_key) 的最近一条追加文本、更新 ts；
        否则插入新行。合并时 read 重置为 0（有新内容视为未读）。
        任何失败只 print 不抛（Feed 是增强面）。"""
        mtype = (meta or {}).get("type", "")
        meta_json = json.dumps(meta or {}, ensure_ascii=False)
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT id, text FROM feed WHERE kind=? AND json_extract(meta,'$.type')=? "
                    "AND json_extract(meta,'$.hour')=? ORDER BY ts DESC LIMIT 1",
                    (kind, mtype, hour_key),
                ).fetchone()
                if row:
                    self._conn.execute(
                        "UPDATE feed SET text=?, ts=?, read=0 WHERE id=?",
                        (f"{row['text']}；{text}", hour_key, row["id"]),
                    )
                else:
                    self._conn.execute(
                        "INSERT INTO feed (ts, kind, text, meta, read) VALUES (?,?,?,?,0)",
                        (hour_key, kind, text, meta_json),
                    )
                self._conn.commit()
        except Exception as e:
            log(f"feed.append_hourly 失败（已忽略）：{e}")

    def recent(self, limit: int = 60, since: float | None = None) -> list[dict]:
        """按时间倒序取动态。meta JSON 解析失败退化为 {}。"""
        sql = "SELECT id, ts, kind, text, meta, read, status FROM feed"
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
                "status": str(r["status"] or "none"),
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
            log(f"feed 计数失败（已降级为 0）：{e}")
            return 0

    def mark_read(self, feed_id: int) -> bool:
        """标记单条已读。写失败只 print 不抛，返回 False。"""
        try:
            with self._lock:
                cur = self._conn.execute("UPDATE feed SET read = 1 WHERE id = ?", (feed_id,))
                self._conn.commit()
            return cur.rowcount > 0
        except Exception as e:
            log(f"feed 标记已读失败（已跳过）：{e}")
            return False

    def mark_all_read(self) -> int:
        """全部标记已读，返回受影响行数。写失败只 print 不抛，返回 0。"""
        try:
            with self._lock:
                cur = self._conn.execute("UPDATE feed SET read = 1 WHERE read = 0")
                self._conn.commit()
            return cur.rowcount
        except Exception as e:
            log(f"feed 全部标记已读失败（已跳过）：{e}")
            return 0

    def set_status(self, feed_id: int, status: str) -> bool:
        """设置处置态：none/follow/ignore（与 read 正交）。写失败只 print 不抛，返回 False。"""
        if status not in ("none", "follow", "ignore"):
            return False
        try:
            with self._lock:
                cur = self._conn.execute("UPDATE feed SET status = ? WHERE id = ?", (status, feed_id))
                self._conn.commit()
            return cur.rowcount > 0
        except Exception as e:
            log(f"feed 设置处置态失败（已跳过）：{e}")
            return False

    def count_ignored(self) -> int:
        """已忽略条数（前端折叠提示用）。读失败降级 0。"""
        try:
            with self._lock:
                row = self._conn.execute("SELECT COUNT(*) FROM feed WHERE status = 'ignore'").fetchone()
            return int(row[0]) if row else 0
        except Exception as e:
            log(f"feed 计数失败（已降级为 0）：{e}")
            return 0

    def set_feedback(self, feed_id: int, feedback: str) -> bool:
        """写 meta.feedback（up/down/none，信任仪表写侧）。写失败只 print 不抛，返回 False。"""
        if feedback not in ("up", "down", "none"):
            return False
        try:
            with self._lock:
                cur = self._conn.execute(
                    "UPDATE feed SET meta = json_set(meta, '$.feedback', ?) WHERE id = ?",
                    (feedback, feed_id),
                )
                self._conn.commit()
            return cur.rowcount > 0
        except Exception as e:
            log(f"feed 反馈写入失败（已跳过）：{e}")
            return False

    def count_feedback_by_type(self, mtype: str, feedback: str, since: float) -> int:
        """since 以来同类（meta.type）条目的某反馈数（降频判断用）。读失败降级 0。"""
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT COUNT(*) AS n FROM feed WHERE json_extract(meta,'$.type') = ? "
                    "AND json_extract(meta,'$.feedback') = ? AND ts >= ?",
                    (mtype, feedback, since),
                ).fetchone()
            return int(row["n"]) if row else 0
        except Exception as e:
            log(f"feed 反馈计数失败（已降级为 0）：{e}")
            return 0

    def stats(self, days: int = 7) -> dict:
        """信任统计读模型（v1.1）：近 days 天主动行为聚合——按 kind/天计数 + 已读率/忽略率。
        读失败只 print 并返回全零结构（Feed 是增强面）。"""
        days = max(1, int(days))
        since = time.time() - days * 86400
        out = {
            "days": days, "since": since, "total": 0,
            "by_kind": {k: 0 for k in _KINDS},
            "by_day": [],
            "read_rate": 0.0, "ignored_rate": 0.0,
        }
        try:
            with self._lock:
                for row in self._conn.execute(
                    "SELECT kind, COUNT(*) AS n FROM feed WHERE ts >= ? GROUP BY kind", (since,)
                ):
                    out["by_kind"][row["kind"]] = int(row["n"])
                    out["total"] += int(row["n"])
                out["by_day"] = [
                    {"day": r["d"], "kind": r["kind"], "count": int(r["n"])}
                    for r in self._conn.execute(
                        "SELECT date(ts, 'unixepoch', 'localtime') AS d, kind, COUNT(*) AS n "
                        "FROM feed WHERE ts >= ? GROUP BY d, kind ORDER BY d DESC, kind",
                        (since,),
                    )
                ]
                row = self._conn.execute(
                    "SELECT COUNT(*) AS n, COALESCE(SUM(read), 0) AS r, "
                    "COALESCE(SUM(CASE WHEN status = 'ignore' THEN 1 ELSE 0 END), 0) AS i "
                    "FROM feed WHERE ts >= ?", (since,),
                ).fetchone()
                n = int(row["n"])
                if n:
                    out["read_rate"] = round(float(row["r"]) / n, 4)
                    out["ignored_rate"] = round(float(row["i"]) / n, 4)
        except Exception as e:
            log(f"feed 统计失败（已降级为零）：{e}")
        return out

    def close(self) -> None:
        with self._lock:
            self._conn.close()
