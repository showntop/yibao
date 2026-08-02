# 感知 v3 · Distiller 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现每日离线 Distiller——把昨日感知观察预聚合后发给 LLM 提炼，产出 pattern（→mem0）/ insight（≤3 条投影 Feed）/ event（→Feed 按小时合并），原料全部落 `distill.db`。

**Architecture:** 新增 `sidecar/src/yibao_brain/distiller.py`（store + 预聚合 + LLM 提炼 + 投影 + 互斥编排，纯同步、可独立测试）；`server.py` 接入每日 04:17 asyncio 调度循环、`distill_now` IPC、purge 并入现有每小时清理；Rust/TS 透明转发；设置页加 `perception.distill` 开关（行内两段确认）+「立即提炼昨日」按钮。设计依据：`docs/superpowers/specs/2026-08-02-perception-v3-distiller-design.md`。

**Tech Stack:** Python 3.11+ / sqlite3 / pytest（sidecar）；Rust / Tauri command（src-tauri）；Vue 3 + TypeScript（app）。

## Global Constraints

- `perception.distill` 默认 `False`；关闭时调度循环直接跳过，**一步出站都不发生**。
- Distiller 只读文本：B 源截图原图采集时已删，代码不接触任何图片路径。
- 失败纪律与 feed 同款：任何失败只 `print` 到 stderr / 落 runs 表，绝不抛给主链路；未解析的 LLM 文本绝不投影 Feed。
- 只把 pattern 写进 mem0；insight/event 是时效性内容，禁进长期记忆；对话/记忆佐证只读不写（禁双写）。
- 单测禁止真实出站：LLM 一律 `FakeProvider`。
- 测试命令统一在 `sidecar/` 目录下跑：`cd sidecar && .venv/bin/python -m pytest tests/test_xxx.py -v`（仓库既有用法，venv 已存在）。
- 既有测试基线：sidecar 733 passed；不得改红任何既有测试。

---

### Task 1: distiller.py 骨架 + DistillerStore + 调度判定

**Files:**
- Create: `sidecar/src/yibao_brain/distiller.py`
- Test: `sidecar/tests/test_distiller.py`

**Interfaces:**
- Consumes: 仅标准库 + `from .perception import build_activity_segments`（Task 2 才用）。
- Produces:
  - `DistillerStore(db_path: str)`：`.add(day, kind, text, data=None, confidence=None) -> int | None`；`.mark_projected(ids: list[int]) -> None`；`.record_run(run_day, target_day, source, status, error=None) -> None`；`.last_auto_run_day() -> str | None`；`.day_items(day) -> list[dict]`；`.purge(now=None) -> int`；`.close() -> None`
  - `auto_run_due(now: float, last_run_day: str | None, hour: int = 4, minute: int = 17) -> bool`

- [ ] **Step 1: 写失败测试**

创建 `sidecar/tests/test_distiller.py`：

```python
"""Distiller：离线深加工层（感知 v3）。"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from yibao_brain.distiller import DistillerStore, auto_run_due  # noqa: E402


def _store(tmp_path):
    return DistillerStore(str(tmp_path / "distill.db"))


def test_store_add_and_day_items(tmp_path):
    s = _store(tmp_path)
    did = s.add("2026-08-01", "insight", "下午同个报错查了 3 次",
                data={"apps": ["VSCode"]}, confidence=0.8)
    assert isinstance(did, int) and did > 0
    s.add("2026-08-01", "pattern", "工作日上午深度使用 VSCode", confidence=0.9)
    items = s.day_items("2026-08-01")
    assert len(items) == 2
    first = items[0]
    assert first["kind"] == "insight"
    assert first["text"] == "下午同个报错查了 3 次"
    assert first["data"] == {"apps": ["VSCode"]}
    assert first["confidence"] == 0.8
    assert first["projected"] == 0
    assert s.day_items("2026-07-31") == []
    s.close()


def test_store_mark_projected(tmp_path):
    s = _store(tmp_path)
    a = s.add("2026-08-01", "insight", "甲")
    b = s.add("2026-08-01", "insight", "乙")
    s.mark_projected([a])
    items = {it["id"]: it for it in s.day_items("2026-08-01")}
    assert items[a]["projected"] == 1
    assert items[b]["projected"] == 0
    s.close()


def test_store_runs_and_last_auto_run_day(tmp_path):
    s = _store(tmp_path)
    assert s.last_auto_run_day() is None
    s.record_run("2026-08-02", "2026-08-01", "auto", "ok")
    s.record_run("2026-08-02", "2026-08-01", "manual", "ok")
    assert s.last_auto_run_day() == "2026-08-02"
    s.close()


def test_store_purge_keeps_14_days(tmp_path):
    s = _store(tmp_path)
    now = time.time()
    old = now - 15 * 86400
    fresh = now - 1 * 86400
    conn = s._conn  # 直接插行控制 created_at
    conn.execute(
        "INSERT INTO distillations (day, kind, text, created_at) VALUES ('2026-07-17', 'event', '旧', ?)",
        (old,),
    )
    conn.execute(
        "INSERT INTO distillations (day, kind, text, created_at) VALUES ('2026-07-31', 'event', '新', ?)",
        (fresh,),
    )
    conn.execute(
        "INSERT INTO runs (run_day, target_day, source, status, created_at) VALUES ('2026-07-17', '2026-07-16', 'auto', 'ok', ?)",
        (old,),
    )
    conn.commit()
    deleted = s.purge(now=now)
    assert deleted == 2  # 1 条旧 distillation + 1 条旧 run
    assert [it["text"] for it in s.day_items("2026-07-31")] == ["新"]
    s.close()


def test_auto_run_due():
    # 2026-08-02 是本地时间；用 mktime 构造本地时间戳避免时区陷阱
    morning = time.mktime((2026, 8, 2, 5, 0, 0, 0, 0, -1))
    early = time.mktime((2026, 8, 2, 4, 10, 0, 0, 0, -1))
    assert auto_run_due(morning, None) is True
    assert auto_run_due(morning, "2026-08-01") is True   # 上次跑是昨天
    assert auto_run_due(morning, "2026-08-02") is False  # 今日已跑
    assert auto_run_due(early, None) is False            # 还没到 04:17
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd sidecar && .venv/bin/python -m pytest tests/test_distiller.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'yibao_brain.distiller'`）

- [ ] **Step 3: 实现 distiller.py 骨架**

创建 `sidecar/src/yibao_brain/distiller.py`：

```python
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
```

（`from .perception import build_activity_segments` 本任务未用到，先放行——Task 2 的预聚合要用，避免后续再改 import 块。）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd sidecar && .venv/bin/python -m pytest tests/test_distiller.py -v`
Expected: 5 passed

- [ ] **Step 5: 跑全量确认无回归**

Run: `cd sidecar && .venv/bin/python -m pytest -x -q`
Expected: 738 passed（733 基线 + 5 新增）

- [ ] **Step 6: Commit**

```bash
git add sidecar/src/yibao_brain/distiller.py sidecar/tests/test_distiller.py
git commit -m "feat(distiller): distill.db 存储 + 每日 04:17 调度判定"
```

---

### Task 2: 昨日窗口 + 预聚合摘要

**Files:**
- Modify: `sidecar/src/yibao_brain/distiller.py`
- Test: `sidecar/tests/test_distiller.py`

**Interfaces:**
- Consumes: `PerceptionStore.query_window(start_ts, end_ts, limit=2000, sources=None) -> list[dict]`（默认只取 A/C 源；`sources=("screen",)` 取 B 源；返回 dict 含 `ts/source/kind/payload/sensitivity`，解密失败的行 payload 为 `{}`）；`latest_before(source, ts) -> dict | None`；`build_activity_segments(rows, seeds, start_ts, end_ts, *, max_segments=120) -> (segments, truncated)`，segment 形如 `{"start_ts", "end_ts", "app"?, "title"?, "activity"?}`（均见 `perception.py:232-331`）。
- Produces:
  - `yesterday_window(now: float | None = None) -> tuple[str, float, float]`（day_str, start_ts, end_ts，本地自然日）
  - `gather_summary(pstore, start_ts, end_ts, *, memories=None, history=None, char_budget=20000) -> tuple[str, dict]`——返回（摘要文本， `{"app_count": int, "screen_count": int}`）

- [ ] **Step 1: 追加失败测试**

追加到 `sidecar/tests/test_distiller.py`（需要新 import：见测试码头部）：

```python
from cryptography.fernet import Fernet  # noqa: E402

from yibao_brain.distiller import gather_summary, yesterday_window  # noqa: E402
from yibao_brain.perception import PerceptionStore  # noqa: E402


def _pstore(tmp_path):
    return PerceptionStore(str(tmp_path / "obs.db"), key=Fernet.generate_key())


def test_yesterday_window():
    now = time.mktime((2026, 8, 2, 15, 30, 0, 0, 0, -1))
    day, start, end = yesterday_window(now)
    assert day == "2026-08-01"
    assert end - start == 86400
    assert time.localtime(start).tm_hour == 0
    assert time.localtime(end) == time.localtime(time.mktime((2026, 8, 2, 0, 0, 0, 0, 0, -1)))


def test_gather_summary_basic(tmp_path):
    p = _pstore(tmp_path)
    day, start, end = yesterday_window()
    p.append("app", "frontmost", {"app": "VSCode", "title": "a.py"}, "S1", ts=start + 100)
    p.append("app", "frontmost", {"app": "Chrome", "title": "文档"}, "S1", ts=start + 3700)
    p.append("activity", "active", {"idle_seconds": 0}, "S1", ts=start + 100)
    p.append("activity", "idle", {"idle_seconds": 90}, "S1", ts=start + 4000)
    p.append("screen", "tree", {"app": "VSCode", "title": "a.py", "text": "def main() ..."}, "S3",
             ts=start + 200)
    p.append("screen", "tree", {"app": "VSCode", "title": "a.py", "text": "def main() ..."}, "S3",
             ts=start + 300)  # 重复条目应去重
    summary, stats = gather_summary(p, start, end)
    assert stats["app_count"] == 2
    assert stats["screen_count"] == 1
    assert "VSCode" in summary
    assert "Chrome" in summary
    assert summary.count("def main()") == 1
    assert "应用使用" in summary
    p.close()


def test_gather_summary_empty(tmp_path):
    p = _pstore(tmp_path)
    day, start, end = yesterday_window()
    summary, stats = gather_summary(p, start, end)
    assert stats["app_count"] == 0
    assert stats["screen_count"] == 0
    p.close()


def test_gather_summary_budget_keeps_recent(tmp_path):
    p = _pstore(tmp_path)
    day, start, end = yesterday_window()
    p.append("app", "frontmost", {"app": "VSCode", "title": "a.py"}, "S1", ts=start + 10)
    for i in range(50):
        p.append("screen", "tree", {"app": "Chrome", "title": f"p{i}", "text": f"第{i}条 " + "x" * 100},
                 "S3", ts=start + 100 + i * 60)
    summary, stats = gather_summary(p, start, end, char_budget=3000)
    assert len(summary) <= 3100
    assert "第49条" in summary   # 保最近
    assert "第0条" not in summary  # 弃最旧
    assert "VSCode" in summary   # 头部统计不丢
    p.close()


def test_gather_summary_context_evidence(tmp_path):
    p = _pstore(tmp_path)
    day, start, end = yesterday_window()
    p.append("app", "frontmost", {"app": "VSCode", "title": "a.py"}, "S1", ts=start + 10)
    summary, _ = gather_summary(
        p, start, end,
        memories=["用户通常在 23 点后休息"],
        history=[{"role": "user", "content": "帮我看看这个报错"}, {"role": "tool", "content": "x"}],
    )
    assert "近期记忆" in summary and "23 点后休息" in summary
    assert "近期对话" in summary and "帮我看看这个报错" in summary
    assert '"role": "tool"' not in summary  # tool 消息不进佐证
    p.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd sidecar && .venv/bin/python -m pytest tests/test_distiller.py -v`
Expected: 4 个新测试 FAIL（`ImportError: cannot import name 'gather_summary'`）

- [ ] **Step 3: 实现昨日窗口与预聚合**

追加到 `sidecar/src/yibao_brain/distiller.py` 末尾：

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd sidecar && .venv/bin/python -m pytest tests/test_distiller.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add sidecar/src/yibao_brain/distiller.py sidecar/tests/test_distiller.py
git commit -m "feat(distiller): 昨日窗口 + 预聚合摘要（A/C 时间段 + B 源去重 + 佐证，预算保最近）"
```

---

### Task 3: LLM 提炼 + 严格 JSON 解析

**Files:**
- Modify: `sidecar/src/yibao_brain/distiller.py`
- Test: `sidecar/tests/test_distiller.py`

**Interfaces:**
- Consumes: `LLMResponse`（`llm.py:44+`，pydantic，`.text: str`、`.tool_calls`）。
- Produces:
  - `parse_distill_output(text: str | None) -> dict | None`——合法返回 `{"patterns": [...], "insights": [...], "events": [...]}`，每条 `{"text": str, "confidence": float(0-1), "data": dict}`；任何不合法返回 `None`
  - `_DISTILL_PROMPT: str`（system prompt）

- [ ] **Step 1: 追加失败测试**

```python
from yibao_brain.distiller import parse_distill_output  # noqa: E402


def test_parse_valid_output():
    text = '{"patterns": [{"text": "上午深度用 VSCode", "confidence": 0.9}],' \
           ' "insights": [{"text": "同个报错查了 3 次", "confidence": 0.75, "data": {"app": "Chrome"}}],' \
           ' "events": []}'
    out = parse_distill_output(text)
    assert out is not None
    assert out["patterns"][0]["text"] == "上午深度用 VSCode"
    assert out["patterns"][0]["confidence"] == 0.9
    assert out["patterns"][0]["data"] == {}
    assert out["insights"][0]["data"] == {"app": "Chrome"}
    assert out["events"] == []


def test_parse_fenced_output():
    text = "```json\n{\"patterns\": [], \"insights\": [], \"events\": [{\"text\": \"凌晨 2 点仍活跃\"}]}\n```"
    out = parse_distill_output(text)
    assert out is not None
    assert out["events"][0]["text"] == "凌晨 2 点仍活跃"
    assert out["events"][0]["confidence"] == 0.5  # 缺省置信度


def test_parse_invalid_outputs():
    assert parse_distill_output("") is None
    assert parse_distill_output(None) is None
    assert parse_distill_output("这不是 JSON") is None
    assert parse_distill_output("[1,2,3]") is None                      # 不是 dict
    assert parse_distill_output('{"patterns": "oops"}') is None         # 键类型错
    # 坏条目被丢弃而不是整体失败
    out = parse_distill_output('{"patterns": [{"no_text": 1}, {"text": "好"}], "insights": 5, "events": []}')
    # patterns 里无 text 的被丢；insights 类型错 → 整体 None
    assert out is None
    out2 = parse_distill_output('{"patterns": [{"no_text": 1}, {"text": "好", "confidence": 3}], "insights": [], "events": []}')
    assert [p["text"] for p in out2["patterns"]] == ["好"]
    assert out2["patterns"][0]["confidence"] == 1.0  # 钳到 0-1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd sidecar && .venv/bin/python -m pytest tests/test_distiller.py -k parse -v`
Expected: FAIL（`ImportError: cannot import name 'parse_distill_output'`）

- [ ] **Step 3: 实现 prompt 与解析**

追加到 `sidecar/src/yibao_brain/distiller.py` 末尾：

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd sidecar && .venv/bin/python -m pytest tests/test_distiller.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add sidecar/src/yibao_brain/distiller.py sidecar/tests/test_distiller.py
git commit -m "feat(distiller): 提炼 prompt + 严格 JSON 解析（非法即弃不投影）"
```

---

### Task 4: Distiller 编排（投影分流 + 互斥 + 失败兜底）

**Files:**
- Modify: `sidecar/src/yibao_brain/distiller.py`
- Test: `sidecar/tests/test_distiller.py`

**Interfaces:**
- Consumes:
  - `Memory.add(text: str, user_id: str) -> bool`（`memory.py:22-31`；mem0 自身去重）
  - `FeedStore.add(kind, text, meta=None) -> None`、`FeedStore.append_hourly(kind, text, meta, hour_key)`（`feed.py:54-93`；append_hourly 要求 meta 含 `"type"` 与 `"hour"`）
  - `GLMProvider.chat(messages) -> LLMResponse`（`llm.py:190-208`）；测试用 `fakes.py` 的 `FakeProvider(text=...)`（`.calls` 可断言收到的 messages）
  - `Memory` 测试假件 `fakes.py` 的 `FakeMemory`（若其 `add` 无记录能力，用下方测试里的自定义 `_Mem`）
- Produces:
  - `Distiller(*, store: DistillerStore, pstore, provider, memory, feed, memories_fn=None, history_fn=None, user_id="default", char_budget=20000)`
  - `.run_yesterday(source: str = "auto") -> dict`——返回 `{"status": "ok"|"no_data"|"failed"|"already_running", "day": str, ...}`，绝不抛异常

- [ ] **Step 1: 追加失败测试**

先看 `sidecar/tests/fakes.py` 里 `FakeMemory` 是否有 `add` 记录；若无，测试里用自带的 `_Mem`：

```python
from yibao_brain.distiller import Distiller  # noqa: E402
from yibao_brain.feed import FeedStore  # noqa: E402
from yibao_brain.fakes import FakeProvider  # noqa: E402

_GOOD_JSON = (
    '{"patterns": [{"text": "上午深度用 VSCode", "confidence": 0.9}],'
    ' "insights": [{"text": "低置信洞察", "confidence": 0.4},'
    ' {"text": "洞察A", "confidence": 0.9}, {"text": "洞察B", "confidence": 0.8},'
    ' {"text": "洞察C", "confidence": 0.7}, {"text": "洞察D", "confidence": 0.65}],'
    ' "events": [{"text": "凌晨 2 点仍活跃", "confidence": 0.9}]}'
)


class _Mem:
    def __init__(self):
        self.added: list[str] = []

    def add(self, text, user_id):
        self.added.append(text)
        return True

    def recall(self, query, user_id):
        return []


def _distiller(tmp_path, provider, p=None):
    p = p or _pstore(tmp_path)
    feed = FeedStore(str(tmp_path / "feed.db"))
    mem = _Mem()
    d = Distiller(
        store=_store(tmp_path), pstore=p, provider=provider,
        memory=mem, feed=feed, user_id="default",
    )
    return d, mem, feed


def test_run_yesterday_end_to_end(tmp_path):
    p = _pstore(tmp_path)
    day, start, end = yesterday_window()
    p.append("app", "frontmost", {"app": "VSCode", "title": "a.py"}, "S1", ts=start + 100)
    p.append("screen", "tree", {"app": "VSCode", "title": "a.py", "text": "代码"}, "S3", ts=start + 200)
    provider = FakeProvider(text=_GOOD_JSON)
    d, mem, feed = _distiller(tmp_path, provider, p)

    result = d.run_yesterday("manual")
    assert result["status"] == "ok"
    assert result["day"] == day

    # 原料全落库：1 pattern + 5 insight + 1 event
    items = d.store.day_items(day)
    assert len([i for i in items if i["kind"] == "pattern"]) == 1
    assert len([i for i in items if i["kind"] == "insight"]) == 5
    assert len([i for i in items if i["kind"] == "event"]) == 1

    # pattern → mem0（只写 pattern）
    assert mem.added == ["上午深度用 VSCode"]

    # insight 投影：置信度 ≥0.6 的前 3 条（D 0.65 落选，低置信 0.4 过滤）
    feed_items = feed.recent(limit=20)
    insights = [f for f in feed_items if f["meta"].get("type") == "distill_insight"]
    assert [f["text"] for f in insights] == ["洞察A", "洞察B", "洞察C"]
    assert all(f["meta"]["distill_id"] for f in insights)

    # event 走 append_hourly 合并写
    events = [f for f in feed_items if f["meta"].get("type") == "distill_event"]
    assert any("凌晨 2 点仍活跃" in f["text"] for f in events)

    # runs 表记录
    assert d.store.last_auto_run_day() is None  # manual 不算 auto
    d.store.close()
    p.close()


def test_run_yesterday_no_data_skips_llm(tmp_path):
    provider = FakeProvider(text=_GOOD_JSON)
    d, mem, feed = _distiller(tmp_path, provider)
    result = d.run_yesterday("auto")
    assert result["status"] == "no_data"
    assert provider.calls == []            # 空数据零出站
    assert feed.recent() == []
    assert d.store.last_auto_run_day() is not None  # 但运行记录落库（防重跑）
    d.store.close()


def test_run_yesterday_bad_llm_output(tmp_path):
    p = _pstore(tmp_path)
    day, start, end = yesterday_window()
    p.append("app", "frontmost", {"app": "VSCode", "title": "a.py"}, "S1", ts=start + 100)
    provider = FakeProvider(text="模型抽风输出")
    d, mem, feed = _distiller(tmp_path, provider, p)
    result = d.run_yesterday("auto")
    assert result["status"] == "failed"
    assert feed.recent() == []             # 未解析文本绝不投影
    assert mem.added == []
    d.store.close()
    p.close()


def test_run_yesterday_llm_exception(tmp_path):
    class _Boom:
        def chat(self, messages, tools=None):
            raise RuntimeError("网络炸了")

    p = _pstore(tmp_path)
    day, start, end = yesterday_window()
    p.append("app", "frontmost", {"app": "VSCode", "title": "a.py"}, "S1", ts=start + 100)
    d, mem, feed = _distiller(tmp_path, _Boom(), p)
    result = d.run_yesterday("auto")       # 不抛异常
    assert result["status"] == "failed"
    assert "网络炸了" in result["error"]
    assert feed.recent() == []
    d.store.close()
    p.close()


def test_run_yesterday_memory_failure_still_projects_feed(tmp_path):
    class _BadMem(_Mem):
        def add(self, text, user_id):
            raise RuntimeError("mem0 挂了")

    p = _pstore(tmp_path)
    day, start, end = yesterday_window()
    p.append("app", "frontmost", {"app": "VSCode", "title": "a.py"}, "S1", ts=start + 100)
    provider = FakeProvider(text=_GOOD_JSON)
    feed = FeedStore(str(tmp_path / "feed.db"))
    d = Distiller(store=_store(tmp_path), pstore=p, provider=provider,
                  memory=_BadMem(), feed=feed, user_id="default")
    result = d.run_yesterday("manual")
    assert result["status"] == "ok"        # mem0 挂不影响整体
    assert any(f["meta"].get("type") == "distill_insight" for f in feed.recent())
    d.store.close()
    p.close()


def test_run_yesterday_mutex(tmp_path):
    p = _pstore(tmp_path)
    day, start, end = yesterday_window()
    p.append("app", "frontmost", {"app": "VSCode", "title": "a.py"}, "S1", ts=start + 100)
    provider = FakeProvider(text=_GOOD_JSON)
    d, mem, feed = _distiller(tmp_path, provider, p)
    assert d._run_lock.acquire(blocking=False)  # 模拟进行中的任务
    try:
        result = d.run_yesterday("manual")
        assert result["status"] == "already_running"
    finally:
        d._run_lock.release()
    d.store.close()
    p.close()
```

注意：`FakeProvider` 的入参属性名（`.calls`）以 `sidecar/tests/fakes.py` 实际为准——若记录属性叫别的名字（如 `.chat_calls`），改断言处即可，不要改 fakes.py。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd sidecar && .venv/bin/python -m pytest tests/test_distiller.py -v`
Expected: 6 个新测试 FAIL（`ImportError: cannot import name 'Distiller'`）

- [ ] **Step 3: 实现 Distiller 编排**

追加到 `sidecar/src/yibao_brain/distiller.py` 末尾：

```python
class Distiller:
    """昨日提炼编排：汇集 → 预聚合 → LLM → 落库 → 投影。绝不抛异常。"""

    def __init__(
        self,
        *,
        store: DistillerStore,
        pstore,
        provider,
        memory,
        feed,
        memories_fn=None,   # () -> list[str] | None；近期记忆佐证（只读）
        history_fn=None,    # () -> list[dict] | None；近期对话佐证（只读）
        user_id: str = "default",
        char_budget: int = _SUMMARY_CHAR_BUDGET,
    ):
        self.store = store
        self.pstore = pstore
        self.provider = provider
        self.memory = memory
        self.feed = feed
        self.memories_fn = memories_fn
        self.history_fn = history_fn
        self.user_id = user_id
        self.char_budget = char_budget
        self._run_lock = threading.Lock()

    def run_yesterday(self, source: str = "auto") -> dict:
        """跑一次昨日提炼。source: "auto" | "manual"。绝不抛异常。"""
        if not self._run_lock.acquire(blocking=False):
            return {"status": "already_running"}
        run_day = date.today().isoformat()
        target_day, start_ts, end_ts = yesterday_window()
        try:
            memories = self._safe_call(self.memories_fn)
            history = self._safe_call(self.history_fn)
            summary, stats = gather_summary(
                self.pstore, start_ts, end_ts,
                memories=memories, history=history, char_budget=self.char_budget,
            )
            if stats["app_count"] == 0 and stats["screen_count"] == 0:
                self.store.record_run(run_day, target_day, source, "no_data")
                return {"status": "no_data", "day": target_day}
            resp = self.provider.chat([
                {"role": "system", "content": _DISTILL_PROMPT},
                {"role": "user", "content": summary},
            ])
            result = parse_distill_output(resp.text)
            if result is None:
                self.store.record_run(run_day, target_day, source, "failed", "LLM 输出无法解析")
                return {"status": "failed", "day": target_day, "error": "parse"}
            counts = self._project(target_day, result)
            self.store.record_run(run_day, target_day, source, "ok")
            return {"status": "ok", "day": target_day, **counts}
        except Exception as e:
            print(f"[yibao] 提炼失败：{e}", file=sys.stderr)
            try:
                self.store.record_run(run_day, target_day, source, "failed", str(e)[:200])
            except Exception:
                pass
            return {"status": "failed", "day": target_day, "error": str(e)[:200]}
        finally:
            self._run_lock.release()

    def _safe_call(self, fn):
        """佐证读取失败只 print，返回 None 继续（佐证不是必需品）。"""
        if fn is None:
            return None
        try:
            return fn()
        except Exception as e:
            print(f"[yibao] 提炼佐证读取失败：{e}", file=sys.stderr)
            return None

    def _project(self, day: str, result: dict) -> dict:
        """全量落库 → pattern 写 mem0、insight 前 3 条投影 Feed、event 按小时合并。"""
        saved: dict[str, list[tuple[int, dict]]] = {"pattern": [], "insight": [], "event": []}
        for kind, key in (("pattern", "patterns"), ("insight", "insights"), ("event", "events")):
            for item in result[key]:
                did = self.store.add(day, kind, item["text"],
                                     data=item.get("data"), confidence=item["confidence"])
                if did is not None:
                    saved[kind].append((did, item))

        projected: list[int] = []
        # pattern → mem0（只写 pattern；mem0 自身去重；失败只 print）
        for did, item in saved["pattern"]:
            try:
                self.memory.add(item["text"], self.user_id)
                projected.append(did)
            except Exception as e:
                print(f"[yibao] 模式写记忆失败：{e}", file=sys.stderr)
        # insight → Feed：置信度 ≥0.6 的前 3 条，带 distill_id 回指
        ranked = sorted(saved["insight"], key=lambda x: -x[1]["confidence"])
        for did, item in [
            i for i in ranked if i[1]["confidence"] >= _INSIGHT_MIN_CONFIDENCE
        ][:_INSIGHT_MAX_PER_DAY]:
            try:
                self.feed.add("event", item["text"],
                              {"type": "distill_insight", "distill_id": did})
                projected.append(did)
            except Exception as e:
                print(f"[yibao] 洞察投影 Feed 失败：{e}", file=sys.stderr)
        # event → Feed 按小时合并，防刷屏
        if saved["event"]:
            h = int(time.time()) // 3600 * 3600
            for did, item in saved["event"]:
                try:
                    self.feed.append_hourly(
                        "event", item["text"],
                        {"type": "distill_event", "hour": h, "distill_id": did}, h,
                    )
                    projected.append(did)
                except Exception as e:
                    print(f"[yibao] 事件投影 Feed 失败：{e}", file=sys.stderr)
        if projected:
            self.store.mark_projected(projected)
        return {
            "patterns": len(saved["pattern"]),
            "insights": len(saved["insight"]),
            "events": len(saved["event"]),
            "projected": len(projected),
        }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd sidecar && .venv/bin/python -m pytest tests/test_distiller.py -v`
Expected: 18 passed

- [ ] **Step 5: 跑全量确认无回归**

Run: `cd sidecar && .venv/bin/python -m pytest -x -q`
Expected: 751 passed

- [ ] **Step 6: Commit**

```bash
git add sidecar/src/yibao_brain/distiller.py sidecar/tests/test_distiller.py
git commit -m "feat(distiller): 编排——投影分流（pattern→mem0 / insight≤3→Feed / event 合并）+ 互斥 + 失败兜底"
```

---

### Task 5: server.py 接线（设置键 + 每日循环 + distill_now IPC + purge）

**Files:**
- Modify: `sidecar/src/yibao_brain/config.py:249-259`（`_SETTINGS_DEFAULTS`）
- Modify: `sidecar/src/yibao_brain/server.py`（import 块 :16-33；pstore 构造后 :740-748；`_perception_cleanup_loop` :831-840；feed IPC 分支后 :1245）

**Interfaces:**
- Consumes: Task 1-4 的 `Distiller` / `DistillerStore` / `auto_run_due`；`server.py` 现有 `settings`（共享 dict，:588）、`agent`（:650，`agent.provider` / `agent.memory` / `agent.history`）、`feed`（:640）、`pstore`（:740-748）、`db_path`（audit 库路径）、`_offload`（loop.py:58）、`write_msg`；`agent.history.messages() -> list[dict]`（history.py:58-72）；`agent.memory.recall(query, user_id) -> list[str]`。
- Produces: IPC `{"type": "distill_now"}` → 回包 `{"type": "distill_now", "ok": bool, "reason"?: "disabled", "result"?: {...}}`；设置键 `perception.distill`（默认 False）。

- [ ] **Step 1: 加设置默认值**

`sidecar/src/yibao_brain/config.py` `_SETTINGS_DEFAULTS` 中 `"perception.model_access": False,` 一行之后加：

```python
    # Distiller（每日离线提炼）：默认关；开=每日 04:17 将昨日全天感知内容发给当前模型提炼
    "perception.distill": False,
```

- [ ] **Step 2: 补设置键的回归测试（若已有 settings 默认值测试则顺带跑）**

Run: `cd sidecar && .venv/bin/python -m pytest tests/test_config.py -v`
Expected: 全过（若存在该文件）；随后写一个小断言追加到现有测试或直接在 Step 4 全量中覆盖。

- [ ] **Step 3: server.py 接线（4 处编辑）**

编辑 1——import 块（`from .feed import FeedStore` 一行之后）加：

```python
from .distiller import Distiller, DistillerStore, auto_run_due
```

编辑 2——pstore 构造完成之后（`server.py:740-748` 块尾）加：

```python
# Distiller（感知 v3）：离线深加工层。perception.distill 关闭时调度循环直接跳过，零出站。
distiller = None
if pstore is not None:
    try:
        distiller = Distiller(
            store=DistillerStore(os.path.join(os.path.dirname(db_path), "distill.db")),
            pstore=pstore,
            provider=agent.provider,
            memory=agent.memory,
            feed=feed,
            memories_fn=lambda: agent.memory.recall("作息 使用习惯 工作模式", "default"),
            history_fn=lambda: agent.history.messages()[-10:],
        )
    except Exception as e:
        print(f"[yibao] 提炼器初始化失败（不影响主链路）：{e}", file=sys.stderr)
        distiller = None
```

（`db_path` 是 audit 库路径变量——照抄 `feed = FeedStore(os.path.join(os.path.dirname(db_path), "feed.db"))`（:640）的同款目录推导；若该作用域里变量名不同，以 feed 那行的实际写法为准。）

编辑 3——`_perception_cleanup_loop`（:831-840）整个替换为：

```python
    async def _perception_cleanup_loop() -> None:
        while True:
            await asyncio.sleep(3600)
            if pstore is not None:
                try:
                    await _offload(pstore.purge)
                except Exception as e:
                    print(f"[yibao] 感知过期清理失败：{e}", file=sys.stderr)
            if distiller is not None:
                try:
                    await _offload(distiller.store.purge)
                except Exception as e:
                    print(f"[yibao] 提炼原料清理失败：{e}", file=sys.stderr)

    async def _distiller_loop() -> None:
        """每日 04:17 自动提炼昨日；perception.distill 关闭时零出站。"""
        while True:
            await asyncio.sleep(60)
            if distiller is None or not settings.get("perception.distill"):
                continue
            try:
                last = await _offload(distiller.store.last_auto_run_day)
                if auto_run_due(time.time(), last):
                    await _offload(distiller.run_yesterday, "auto")
            except Exception as e:
                print(f"[yibao] 自动提炼失败：{e}", file=sys.stderr)

    perception_cleanup_task = asyncio.ensure_future(_perception_cleanup_loop())
    distiller_task = asyncio.ensure_future(_distiller_loop())
```

（保留原有 `perception_cleanup_task` 一行，新增 `distiller_task` 一行紧随其后。）

编辑 4——`elif rtype == "feed":` 分支的 `write_msg({...})` 结束之后（:1245 之后）加：

```python
        elif rtype == "distill_now":
            # 设置页「立即提炼昨日」：开关关闭时直接拒绝（零出站）；运行可长达 60s，挪线程池
            if distiller is None or not settings.get("perception.distill"):
                write_msg({"type": "distill_now", "ok": False, "reason": "disabled"})
            else:
                result = await _offload(distiller.run_yesterday, "manual")
                write_msg({"type": "distill_now", "ok": True, "result": result})
```

- [ ] **Step 4: 跑全量确认无回归**

Run: `cd sidecar && .venv/bin/python -m pytest -x -q`
Expected: 751 passed（server.py 改动不破坏既有测试）

- [ ] **Step 5: 冒烟——sidecar 能正常启动**

Run: `cd sidecar && echo '{"id":0,"type":"ping"}' | timeout 10 .venv/bin/python -m yibao_brain.server || true`
Expected: 输出含 hello/pong 行，无 traceback（启动路径上 distiller 初始化不炸）

- [ ] **Step 6: Commit**

```bash
git add sidecar/src/yibao_brain/config.py sidecar/src/yibao_brain/server.py
git commit -m "feat(distiller): server 接线——perception.distill 设置键 + 每日 04:17 循环 + distill_now IPC + purge 并入每小时清理"
```

---

### Task 6: Rust + TS 转发 distill_now

**Files:**
- Modify: `app/src-tauri/src/lib.rs`（feed 桥接 :449-451 后；get_feed 命令 :782-789 后；invoke_handler :1531+）
- Modify: `app/src/lib/brain.ts`（`fetchFeed` :279-281 后；`SettingsValues` :659）

**Interfaces:**
- Consumes: Task 5 的 IPC 协议；`write_to_brain`（lib.rs:376-381）；`app.emit`；TS `invoke` / `once`（@tauri-apps/api）。
- Produces:
  - Rust command `distill_now`；事件 `brain-distill-now`
  - TS `distillNow(timeoutMs = 90000) -> Promise<DistillNowResponse>`，`DistillNowResponse = { ok: boolean; reason?: string; result?: { status: string; day?: string; patterns?: number; insights?: number; events?: number; projected?: number; error?: string } }`
  - `SettingsValues` 新增 `"perception.distill"?: boolean;`

- [ ] **Step 1: Rust 桥接事件**

`app/src-tauri/src/lib.rs` 在 `Some("feed") => { let _ = app.emit("brain-feed", v); }` 块之后加：

```rust
                            // 手动提炼响应（distill_now）：整体转发，设置页一次性取用
                            Some("distill_now") => {
                                let _ = app.emit("brain-distill-now", v);
                            }
```

- [ ] **Step 2: Rust 命令**

在 `get_feed` 命令（:782-789）之后加：

```rust
/// 手动触发昨日提炼：大脑回 {"type":"distill_now","ok":…,"result":…}，经 brain-distill-now 事件广播。
#[tauri::command]
fn distill_now(state: tauri::State<Brain>) -> Result<(), String> {
    write_to_brain(
        &state,
        serde_json::json!({ "id": 0, "type": "distill_now" }),
    )
}
```

并在 `invoke_handler` 列表（:1531+，`get_feed,` 一行之后）加：

```rust
            distill_now,
```

- [ ] **Step 3: cargo check**

Run: `cd app/src-tauri && cargo check`
Expected: exit 0

- [ ] **Step 4: TS 封装 + 设置类型**

`app/src/lib/brain.ts` 在 `fetchFeed` 函数（:279-281）之后加：

```ts
/** 手动提炼回包（distill_now 的响应，经 brain-distill-now 事件回来）。 */
export interface DistillNowResponse {
  ok: boolean;
  reason?: string;
  result?: {
    status: string;
    day?: string;
    patterns?: number;
    insights?: number;
    events?: number;
    projected?: number;
    error?: string;
  };
}

/** 一次性手动提炼：发请求并等下一条 brain-distill-now；大脑不在线/超时返回 ok:false。 */
export async function distillNow(timeoutMs = 90000): Promise<DistillNowResponse> {
  const resp = new Promise<DistillNowResponse>((resolve) => {
    void once<DistillNowResponse>("brain-distill-now", (ev) => resolve(ev.payload));
  });
  const timeout = new Promise<DistillNowResponse>((resolve) =>
    setTimeout(() => resolve({ ok: false, reason: "timeout" }), timeoutMs),
  );
  try {
    await invoke("distill_now");
  } catch { /* 大脑不在线：走超时兜底 */ }
  return Promise.race([resp, timeout]);
}
```

`SettingsValues`（:655-660）中 `"perception.screen"?: boolean;` 之后加一行：

```ts
  "perception.distill"?: boolean;
```

- [ ] **Step 5: 类型检查 + 构建**

Run: `cd app && npx vue-tsc --noEmit && npx vite build`
Expected: exit 0

- [ ] **Step 6: Commit**

```bash
git add app/src-tauri/src/lib.rs app/src/lib/brain.ts
git commit -m "feat(distiller): Rust/TS 转发 distill_now（brain-distill-now 事件）+ 设置类型"
```

---

### Task 7: 设置页开关 + 行内确认 + 立即提炼按钮

**Files:**
- Modify: `app/src/components/SettingsView.vue`（ref 声明区；`syncPerceptionSettings` :295-305；`setPerceptionSetting` :307-344；感知组模板 :783-794 后）

**Interfaces:**
- Consumes: Task 6 的 `distillNow` / `DistillNowResponse`；现有 `setPerceptionSetting` / `perceptionScreen` / `screenConfirming` 模式（:307-358）。
- Produces: 设置页「感知」组新增「每日提炼」开关（行内两段确认）+「立即提炼昨日」按钮（仅开关开启时可见）。

- [ ] **Step 1: script 接线**

`SettingsView.vue` 中照 `perceptionScreen` 的模式加 ref（与 `perceptionScreen = ref(false)` 同区）：

```ts
const perceptionDistill = ref(false);
const distillConfirming = ref(false);
const distillRunning = ref(false);
const distillResult = ref("");
```

`syncPerceptionSettings`（:295-305）函数体末尾加一行：

```ts
  perceptionDistill.value = s["perception.distill"] === true;
```

`setPerceptionSetting` 的 key 联合类型（:308-313）加一项 `"perception.distill"`；函数体内三处对称扩展——`old` 对象加 `"perception.distill": perceptionDistill.value,`；`if (key === ...)` 赋值链加 `if (key === "perception.distill") perceptionDistill.value = next;`；失败回滚链加 `perceptionDistill.value = old["perception.distill"];`。

照 `onScreenToggle`/`confirmScreenEnable`（:347-358）加：

```ts
// distill 开关：关闭直接生效；开启先弹行内说明，确认才写入（照屏幕内容的行内两段确认模式）
function onDistillToggle() {
  if (perceptionDistill.value) {
    void setPerceptionSetting("perception.distill", false);
  } else {
    distillConfirming.value = true;
  }
}

async function confirmDistillEnable() {
  distillConfirming.value = false;
  await setPerceptionSetting("perception.distill", true);
}

// 「立即提炼昨日」：最长 90s（LLM 60s 超时 + 余量），结果一次性展示
async function onDistillNow() {
  distillRunning.value = true;
  distillResult.value = "";
  const r = await distillNow();
  distillRunning.value = false;
  if (!r.ok) {
    distillResult.value = r.reason === "timeout" ? "提炼超时，请稍后再试" : "提炼未开启或大脑不在线";
    return;
  }
  const st = r.result?.status;
  if (st === "ok") {
    distillResult.value = `已提炼 ${r.result?.day ?? "昨日"}：洞察 ${r.result?.insights ?? 0} 条、模式 ${r.result?.patterns ?? 0} 条、事件 ${r.result?.events ?? 0} 条`;
  } else if (st === "no_data") {
    distillResult.value = "昨日没有感知观察，未提炼";
  } else if (st === "already_running") {
    distillResult.value = "提炼正在进行中";
  } else {
    distillResult.value = "提炼失败，请稍后再试";
  }
}
```

import 区把 `distillNow` 加进从 `../lib/brain`（或现有 brain.ts import 路径）的导入列表。

- [ ] **Step 2: 模板**

感知组模板中「屏幕内容」行内确认块（:787-794）之后加：

```html
          <div class="s-row">
            <span class="s-row-label">每日提炼<span class="s-row-why">每日凌晨将昨日感知内容发送给当前模型做提炼，产出模式记忆与效率洞察</span></span>
            <button class="switch" :class="{ on: perceptionDistill }" role="switch" :aria-checked="perceptionDistill" :disabled="!perceptionMaster" title="每日提炼" @click="onDistillToggle"><i /></button>
          </div>
          <!-- 开启每日提炼的行内两段确认：说明外发边界后，确认才写入 -->
          <div v-if="distillConfirming" class="s-row">
            <span class="s-row-label"><span class="s-row-why">确认后，每日 04:17 自动将昨日全天感知内容（应用名、窗口标题、活动状态、界面结构文本与截图概括）发送给当前模型做提炼；不发送截图原图或按键内容</span></span>
            <span class="s-row-btns">
              <button class="s-mini danger" @click="confirmDistillEnable">确认开启</button>
              <button class="s-mini" @click="distillConfirming = false">取消</button>
            </span>
          </div>
          <div v-if="perceptionDistill" class="s-row">
            <span class="s-row-label"><span class="s-row-why">{{ perceptionScreen ? "提炼含应用、活动与屏幕内容" : "未开启屏幕内容，提炼只含应用与活动数据" }}</span></span>
            <span class="s-row-btns">
              <button class="s-mini" :disabled="distillRunning" @click="onDistillNow">{{ distillRunning ? "提炼中…" : "立即提炼昨日" }}</button>
            </span>
          </div>
          <div v-if="distillResult" class="s-note">{{ distillResult }}</div>
```

- [ ] **Step 3: 类型检查 + 构建**

Run: `cd app && npx vue-tsc --noEmit && npx vite build`
Expected: exit 0

- [ ] **Step 4: Commit**

```bash
git add app/src/components/SettingsView.vue
git commit -m "feat(distiller): 设置页每日提炼开关（行内两段确认）+ 立即提炼昨日按钮"
```

---

### Task 8: 端到端验证 + 真机验收

**Files:**
- 无新增代码；只跑验证命令与真机检查。

- [ ] **Step 1: sidecar 全量测试**

Run: `cd sidecar && .venv/bin/python -m pytest -q`
Expected: 751 passed

- [ ] **Step 2: 前端全量检查**

Run: `cd app && npx vue-tsc --noEmit && npx vite build`
Expected: exit 0

- [ ] **Step 3: Rust 全量检查**

Run: `cd app/src-tauri && cargo check && cargo test`
Expected: exit 0

- [ ] **Step 4: 真机验收（手动，对照规格 §7）**

1. 启动 dev app（或复用后台已跑的 dev 实例，需重启 sidecar 以加载新代码）。
2. 设置 → 隐私 → 开启「每日提炼」（出现行内确认，文案含「不发送截图原图或按键内容」）→「立即提炼昨日」→ 等 ≤90s：
   - 昨日有观察：按钮区显示「已提炼 …」；主屏 Feed 出现洞察（meta.type=distill_insight）；设置 → 记忆管理页出现 pattern 记忆；`~/Library/Application Support/yibao/distill.db` 的 distillations 表有 3 类原料、runs 表有 ok 记录。
   - 昨日无观察：显示「昨日没有感知观察，未提炼」，Feed 无新增。
3. 关闭「每日提炼」→ 再点按钮区域消失；改系统时间到次日 04:17 后（或临时把 `_distiller_loop` 的 `auto_run_due` 默认时刻调近再改回）确认**无任何出站**（sidecar 日志无提炼记录，runs 表无新行）。
4. 未开「屏幕内容」时开关下方提示「未开启屏幕内容，提炼只含应用与活动数据」。

- [ ] **Step 5: 收尾提交（如有验收中修的小问题）**

```bash
git add -A
git commit -m "test(distiller): 真机验收修正"
```

---

## Self-Review 记录

- **规格覆盖**：§2 数据流（Task 2/3/4）、§3 存储（Task 1/4）、§4 出站授权（Task 5/7）、§5 错误处理（Task 4 + server try/except）、§6 实现落点（Task 5/6/7）、§7 测试验收（各 Task + Task 8）。§8 不做项无任务——符合。
- **类型一致性**:`DistillerStore.add/mark_projected/record_run/last_auto_run_day/day_items/purge`、`gather_summary`、`parse_distill_output`、`Distiller.run_yesterday`、`auto_run_due` 在 Task 间引用一致；IPC 形状 server/Rust/TS 三方一致（`{"type":"distill_now"}` ↔ `brain-distill-now`）。
- **已知留白**：`FakeProvider` 记录属性名、`feed.db` 目录推导的局部变量名、`FakeMemory` 能力——均已在对应 Step 注明「以现有代码为准」的核对点，实现者现场确认。
