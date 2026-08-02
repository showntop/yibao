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


def yesterday_window(now: float | None = None) -> tuple[str, float, float]:
    """昨日本地自然日的 (day_str, start_ts, end_ts)。"""
    today = date.fromtimestamp(now if now is not None else time.time())
    yday = today - timedelta(days=1)
    start = datetime(yday.year, yday.month, yday.day).timestamp()
    end = datetime(today.year, today.month, today.day).timestamp()
    return yday.isoformat(), start, end


def gather_summary(
    pstore,
    start_ts: float,
    end_ts: float,
    *,
    memories: list[str] | None = None,
    history: list[dict] | None = None,
    char_budget: int = _SUMMARY_CHAR_BUDGET,
) -> tuple[str, dict]:
    """汇集窗口内观察并预聚合成紧凑文本（本地，零 LLM）。

    返回 (摘要, {"app_count", "screen_count"})。B 源条目去重（app+前50字），
    超预算时从最早的 B 源条目开始弃，保最近；头部统计不丢。
    """
    # A/C 源 → 时间线段
    rows = pstore.query_window(start_ts, end_ts, limit=2001)
    seeds = [
        s for s in (
            pstore.latest_before("app", start_ts),
            pstore.latest_before("activity", start_ts),
        )
        if s
    ]
    segments, _ = build_activity_segments(rows, seeds, start_ts, end_ts)

    head: list[str] = ["【应用使用（时长降序）】"]
    app_seconds: dict[str, float] = {}
    for seg in segments:
        app = seg.get("app")
        if app:
            app_seconds[app] = app_seconds.get(app, 0.0) + (seg["end_ts"] - seg["start_ts"])
    for app, secs in sorted(app_seconds.items(), key=lambda kv: -kv[1]):
        head.append(f"- {app}: {secs / 3600:.1f} 小时")
    if not app_seconds:
        head.append("- （无记录）")

    head.append("\n【活跃时段（≥30 分钟）】")
    active_blocks = 0
    for seg in segments:
        if seg.get("activity") == "active" and seg["end_ts"] - seg["start_ts"] >= 1800:
            s = time.strftime("%H:%M", time.localtime(seg["start_ts"]))
            e = time.strftime("%H:%M", time.localtime(seg["end_ts"]))
            head.append(f"- {s}–{e}")
            active_blocks += 1
    if not active_blocks:
        head.append("- （无记录）")

    # B 源 → 去重文本条目（保留到预算内最近的若干条）
    rows_b = pstore.query_window(start_ts, end_ts, limit=2001, sources=("screen",))
    seen: set[tuple[str, str]] = set()
    b_lines: list[str] = []
    for row in rows_b:
        payload = row.get("payload") or {}
        text = str(payload.get("text") or "")[:_B_ENTRY_TEXT_LIMIT]
        app = str(payload.get("app") or "")
        key = (app, text[:50])
        if not text or key in seen:
            continue
        seen.add(key)
        ts = time.strftime("%H:%M", time.localtime(row["ts"]))
        b_lines.append(f"- [{ts}] {app}: {text}")

    # 佐证：近期记忆 + 近期对话（只读，不双写）
    ctx: list[str] = []
    if memories:
        ctx.append("\n【近期记忆（佐证）】")
        ctx.extend(f"- {str(m)[:120]}" for m in memories[:10])
    if history:
        ctx.append("\n【近期对话（佐证）】")
        for m in history[-10:]:
            if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str):
                ctx.append(f"- {m['role']}: {m['content'][:120]}")

    head_text = "\n".join(head) + "\n" + "\n".join(ctx)
    kept: list[str] = []
    used = len(head_text)
    for line in reversed(b_lines):  # 从最新往旧加，超预算即弃最旧
        need = len(line) + 1
        if used + need > char_budget:
            break
        kept.append(line)
        used += need
    kept.reverse()
    if b_lines and not kept:
        kept = [b_lines[-1]]  # 至少保一条最新的

    body = "\n".join(kept) if kept else "- （无记录）"
    marker = "【屏幕内容条目】（仅含最近部分）" if len(kept) < len(b_lines) else "【屏幕内容条目】"
    summary = f"{head_text}\n{marker}\n{body}"
    stats = {"app_count": len(app_seconds), "screen_count": len(kept)}
    return summary, stats


_DISTILL_PROMPT = """你是个人数字生活的分析助手。根据用户昨日的设备使用摘要，提炼三类结论，严格输出 JSON（不要输出任何其他文字）：

{
  "patterns": [{"text": "……", "confidence": 0.0-1.0}],
  "insights": [{"text": "……", "confidence": 0.0-1.0}],
  "events": [{"text": "……", "confidence": 0.0-1.0}]
}

- patterns：稳定可复用的使用/作息模式（将写入长期记忆），宁缺毋滥，≤5 条
- insights：可执行的效率观察与建议（如长时间卡在同一问题、频繁在应用间切换），≤5 条
- events：值得记账的重要事件（如深夜工作、超长连续专注），≤5 条
每条 text 用中文一句话，具体、带数字。没有就给空数组。"""


def parse_distill_output(text: str | None) -> dict | None:
    """解析 LLM 提炼输出；任何不合法返回 None（未解析文本绝不投影）。"""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`").strip()
        if t[:4].lower() == "json":
            t = t[4:].strip()
    try:
        obj = json.loads(t)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    out: dict[str, list[dict]] = {}
    for key in ("patterns", "insights", "events"):
        items = obj.get(key) or []
        if not isinstance(items, list):
            return None
        cleaned: list[dict] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            txt = str(it.get("text") or "").strip()
            if not txt:
                continue
            try:
                conf = float(it.get("confidence", 0.5))
            except (TypeError, ValueError):
                conf = 0.5
            data = it.get("data")
            cleaned.append({
                "text": txt,
                "confidence": max(0.0, min(1.0, conf)),
                "data": data if isinstance(data, dict) else {},
            })
        out[key] = cleaned
    return out
