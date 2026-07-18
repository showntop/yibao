"""PluginDb：插件专属 SQLite（v2 方案 §3.2 ctx.db）。

每插件一个 data.db（默认在 config.plugin_data_dir(pid) 下，目录自建）；
单连接 + threading.Lock 序列化所有读写（tool 在线程池里跑）。
表名/列名强制合法标识符校验（防注入），值一律走参数绑定。
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
import uuid

from . import config

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# manifest 列类型 → SQLite 类型（只支持这三种，够用且安全）
_TYPES = {"text": "TEXT", "integer": "INTEGER", "real": "REAL"}


def _check_ident(name: str, what: str = "标识符") -> str:
    """合法标识符校验：防 SQL 注入（表名/列名无法参数绑定，只能白名单）。"""
    if not name or not _IDENT.match(name):
        raise ValueError(f"非法{what}：{name!r}")
    return name


def _default_literal(v, typ: str) -> str:
    """列默认值渲染成 SQL 字面量（默认值来自 manifest，不走参数绑定，手工转义）。"""
    if v is None:
        return "NULL"
    if typ == "TEXT":
        return "'" + str(v).replace("'", "''") + "'"
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"数值列的默认值必须是数字：{v!r}")
    return str(v)


class PluginDb:
    """单插件的 scoped 数据库句柄。"""

    def __init__(self, plugin_id: str, db_path: str | None = None):
        self.plugin_id = plugin_id
        path = db_path or os.path.join(config.plugin_data_dir(plugin_id), "data.db")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.path = path
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------- schema ----------

    def apply_schema(self, tables: list[dict]) -> None:
        """按 manifest [[table]] 建表。只做 additive 迁移：缺表建表、缺列补列（带默认值），不删列不改列。"""
        with self._lock:
            for t in tables:
                tname = _check_ident(t["name"], "表名")
                existing = self._columns(tname)
                if not existing:
                    defs = ", ".join(self._col_def(c) for c in t.get("columns", []))
                    self._conn.execute(f'CREATE TABLE IF NOT EXISTS "{tname}" ({defs})')
                else:
                    for c in t.get("columns", []):
                        if _check_ident(c["name"], "列名") not in existing:
                            # SQLite 不允许 ADD COLUMN 带 PRIMARY KEY，补列时忽略 pk
                            self._conn.execute(
                                f'ALTER TABLE "{tname}" ADD COLUMN {self._col_def(c, allow_pk=False)}'
                            )
                for idx in t.get("indexes", []):
                    col = _check_ident(idx, "索引列")
                    self._conn.execute(
                        f'CREATE INDEX IF NOT EXISTS "idx_{tname}_{col}" ON "{tname}" ("{col}")'
                    )
            self._conn.commit()

    def _columns(self, table: str) -> set[str]:
        cur = self._conn.execute(f'PRAGMA table_info("{table}")')
        return {r["name"] for r in cur.fetchall()}

    def _col_def(self, c: dict, allow_pk: bool = True) -> str:
        name = _check_ident(c["name"], "列名")
        typ = _TYPES.get(str(c.get("type", "text")).lower())
        if typ is None:
            raise ValueError(f"不支持的列类型：{c.get('type')!r}（仅 text/integer/real）")
        parts = [f'"{name}"', typ]
        if allow_pk and c.get("pk"):
            parts.append("PRIMARY KEY")
        if "default" in c:
            parts.append(f"DEFAULT {_default_literal(c['default'], typ)}")
        return " ".join(parts)

    # ---------- CRUD ----------

    def insert(self, table: str, row: dict) -> str:
        """插入一行，返回 row id（row 无 id 时自动生成 uuid hex）。"""
        tname = _check_ident(table, "表名")
        row = dict(row)
        row.setdefault("id", uuid.uuid4().hex)
        cols = [_check_ident(k, "列名") for k in row]
        col_list = ", ".join(f'"{c}"' for c in cols)
        placeholders = ", ".join("?" for _ in cols)
        sql = f'INSERT INTO "{tname}" ({col_list}) VALUES ({placeholders})'
        with self._lock:
            self._conn.execute(sql, list(row.values()))
            self._conn.commit()
        return str(row["id"])

    def query(
        self,
        table: str,
        where: dict | None = None,
        order: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """等值条件查询。order 形如 "created_at" 或 "created_at DESC"。"""
        tname = _check_ident(table, "表名")
        sql = f'SELECT * FROM "{tname}"'
        args: list = []
        if where:
            clauses = [f'"{_check_ident(k, "列名")}" = ?' for k in where]
            sql += " WHERE " + " AND ".join(clauses)
            args.extend(where.values())
        if order:
            parts = order.strip().split()
            col = _check_ident(parts[0], "排序列")
            direction = parts[1].upper() if len(parts) > 1 else "ASC"
            if direction not in ("ASC", "DESC") or len(parts) > 2:
                raise ValueError(f"非法排序：{order!r}")
            sql += f' ORDER BY "{col}" {direction}'
        if limit is not None:
            sql += " LIMIT ?"
            args.append(int(limit))
        with self._lock:
            cur = self._conn.execute(sql, args)
            return [dict(r) for r in cur.fetchall()]

    def update(self, table: str, row_id: str, fields: dict) -> None:
        tname = _check_ident(table, "表名")
        if not fields:
            return
        sets = ", ".join(f'"{_check_ident(k, "列名")}" = ?' for k in fields)
        with self._lock:
            self._conn.execute(f'UPDATE "{tname}" SET {sets} WHERE "id" = ?', [*fields.values(), row_id])
            self._conn.commit()

    def delete(self, table: str, row_id: str) -> None:
        tname = _check_ident(table, "表名")
        with self._lock:
            self._conn.execute(f'DELETE FROM "{tname}" WHERE "id" = ?', [row_id])
            self._conn.commit()
