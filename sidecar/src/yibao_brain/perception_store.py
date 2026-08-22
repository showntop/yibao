"""本机感知 v1 的存储层：加密观察 SQLite（observations）+ macOS Keychain 密钥托管。

R-32c 从 perception.py 拆出（2026-08-22）：存储与编排分离。perception.py 保留
技能/时间线纯函数与 re-export（server/测试的 `from .perception import PerceptionStore`
路径不变；测试 patch `perception_store.sys/getpass/subprocess` 以控制 keychain 分支）。
"""
from __future__ import annotations

import getpass
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Callable

from cryptography.fernet import Fernet, InvalidToken

from .log import log


_KEYCHAIN_SERVICE = "com.yibao.perception"
_DAY = 86400

_SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  source TEXT NOT NULL,
  kind TEXT NOT NULL,
  payload TEXT NOT NULL,
  sensitivity TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_obs_ts ON observations(ts);
CREATE INDEX IF NOT EXISTS idx_obs_source ON observations(source);
"""


class PerceptionKeyUnavailable(RuntimeError):
    """无法安全取得感知密钥；调用方必须保持感知关闭。"""


def _run_security(args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except subprocess.TimeoutExpired as exc:
        raise PerceptionKeyUnavailable("访问 macOS Keychain 超时") from exc


def key_from_macos_keychain() -> bytes:
    """读取或创建登录 Keychain 中的 Fernet key。"""
    if sys.platform != "darwin":
        raise PerceptionKeyUnavailable("感知密钥仅支持 macOS Keychain")
    account = getpass.getuser()
    find = _run_security(
        [
            "/usr/bin/security",
            "find-generic-password",
            "-a",
            account,
            "-s",
            _KEYCHAIN_SERVICE,
            "-w",
        ],
    )
    if find.returncode == 0 and find.stdout.strip():
        key = find.stdout.strip().encode("ascii")
        try:
            Fernet(key)
        except (ValueError, TypeError) as exc:
            raise PerceptionKeyUnavailable("Keychain 中的感知密钥已损坏") from exc
        return key

    key = Fernet.generate_key()
    add = _run_security(
        [
            "/usr/bin/security",
            "add-generic-password",
            "-U",
            "-a",
            account,
            "-s",
            _KEYCHAIN_SERVICE,
            "-w",
            key.decode("ascii"),
        ],
    )
    if add.returncode != 0:
        detail = add.stderr.strip() or "unknown error"
        raise PerceptionKeyUnavailable(f"无法把感知密钥写入 Keychain：{detail}")
    return key


class PerceptionStore:
    """线程安全的加密观察 SQLite store。"""

    def __init__(
        self,
        db_path: str,
        *,
        key: bytes | None = None,
        key_provider: Callable[[], bytes] | None = None,
    ):
        self.db_path = db_path
        parent = os.path.dirname(db_path) or "."
        os.makedirs(parent, mode=0o700, exist_ok=True)
        os.chmod(parent, 0o700)
        resolved_key = key if key is not None else (key_provider or key_from_macos_keychain)()
        try:
            self._fernet = Fernet(resolved_key)
        except (ValueError, TypeError) as exc:
            raise PerceptionKeyUnavailable("感知密钥格式无效") from exc
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
        os.chmod(db_path, 0o600)

    def _encrypt(self, payload: dict) -> str:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return self._fernet.encrypt(raw).decode("ascii")

    def _decrypt(self, token: str) -> dict:
        try:
            value = json.loads(self._fernet.decrypt(token.encode("ascii")).decode("utf-8"))
            return value if isinstance(value, dict) else {}
        except (InvalidToken, UnicodeError, json.JSONDecodeError, ValueError):
            return {}

    def _decode_row(self, row: sqlite3.Row) -> dict:
        return {
            "id": int(row["id"]),
            "ts": float(row["ts"]),
            "source": row["source"],
            "kind": row["kind"],
            "payload": self._decrypt(row["payload"]),
            "sensitivity": row["sensitivity"],
        }

    def append(
        self,
        source: str,
        kind: str,
        payload: dict,
        sensitivity: str,
        *,
        ts: float | None = None,
    ) -> int | None:
        """加密追加观察；失败只报 stderr 并返回 None。"""
        try:
            encrypted = self._encrypt(payload)
            with self._lock:
                cur = self._conn.execute(
                    "INSERT INTO observations (ts, source, kind, payload, sensitivity) VALUES (?, ?, ?, ?, ?)",
                    (time.time() if ts is None else ts, source, kind, encrypted, sensitivity),
                )
                self._conn.commit()
                return int(cur.lastrowid)
        except Exception as exc:
            log(f"感知写入失败（已跳过）：{exc}")
            return None

    def list(self, limit: int = 60, before_id: int | None = None) -> list[dict]:
        limit = max(1, min(int(limit), 200))
        sql = "SELECT id, ts, source, kind, payload, sensitivity FROM observations"
        args: list[object] = []
        if before_id is not None:
            sql += " WHERE id < ?"
            args.append(int(before_id))
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [self._decode_row(row) for row in rows]

    def query_window(
        self,
        start_ts: float,
        end_ts: float,
        limit: int = 2000,
        sources: tuple[str, ...] | None = None,
    ) -> list[dict]:
        """按时间正序读取观察；默认只取 A/C 源，损坏行保留为空 payload，交给上层计数跳过。"""
        bounded_limit = max(1, min(int(limit), 2001))
        source_set = tuple(sources) if sources else ("app", "activity")
        placeholders = ", ".join("?" for _ in source_set)
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, ts, source, kind, payload, sensitivity FROM ("
                "SELECT id, ts, source, kind, payload, sensitivity FROM observations "
                f"WHERE ts >= ? AND ts <= ? AND source IN ({placeholders}) "
                "ORDER BY ts DESC, id DESC LIMIT ?"
                ") ORDER BY ts ASC, id ASC",
                (float(start_ts), float(end_ts), *source_set, bounded_limit),
            ).fetchall()
        return [self._decode_row(row) for row in rows]

    def latest_before(self, source: str, ts: float) -> dict | None:
        """读取指定来源在窗口起点前最后一个可解密状态。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT id, ts, source, kind, payload, sensitivity FROM observations "
                "WHERE source = ? AND ts < ? ORDER BY ts DESC, id DESC LIMIT 1",
                (source, float(ts)),
            ).fetchone()
        if row is None:
            return None
        item = self._decode_row(row)
        return item if item["payload"] else None

    def delete(self, observation_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM observations WHERE id = ?", (int(observation_id),))
            self._conn.commit()
            return cur.rowcount > 0

    def sources(self) -> list[str]:
        """返回整个日志中出现过的来源，不受当前分页范围影响。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT source FROM observations ORDER BY source"
            ).fetchall()
        return [str(row["source"]) for row in rows]

    def clear(self) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM observations")
            self._conn.commit()
            return max(0, cur.rowcount)

    def purge(self, *, now: float | None = None) -> int:
        """按来源清理：A/C 30 天、screen/environment 7 天、clipboard 1 天。"""
        current = time.time() if now is None else now
        with self._lock:
            cur = self._conn.execute(
                """
                DELETE FROM observations
                WHERE (source IN ('app', 'activity') AND ts < ?)
                   OR (source IN ('screen', 'environment') AND ts < ?)
                   OR (source = 'clipboard' AND ts < ?)
                   OR (source NOT IN ('app', 'activity', 'screen', 'environment', 'clipboard') AND ts < ?)
                """,
                (current - 30 * _DAY, current - 7 * _DAY, current - _DAY, current - 7 * _DAY),
            )
            self._conn.commit()
            return max(0, cur.rowcount)

    def close(self) -> None:
        with self._lock:
            self._conn.close()
