# 晨间反刍 + 每日回顾 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 Distiller 的提炼结果两个可见出口——开窗即推的「晨间反刍」+ 动态页「回顾」视图；穿插把 server.py 三个后台循环搬到 background.py。

**Architecture:** 反刍与回顾都是 `distillations` 表的只读消费者。反刍开窗触发、零 LLM 拼装（建议在凌晨 Distiller 调用里生成、存库），经现有 `ProactiveDispatcher`（reminder 路径）复用气泡/语音/降频。回顾把 `gather_summary` 本就计算的 app 时长/活跃段存进 `runs.stats`，按天展示。server.py 拆分是纯搬运、不改逻辑、756 测试兜底。

**Tech Stack:** Python 3.12（sidecar，pytest），Vue 3 + TS + Vite（app/src），Rust + Tauri v2（app/src-tauri），SQLite。

## Global Constraints

- **零真实 LLM**：所有自动化测试用 `FakeProvider`（`yibao_brain.llm`）造假返回，不触网。
- **全链路「挂了不碍事」**：反刍/回顾任何异常只 print，绝不影响开窗、主对话、感知采集。
- **TDD**：每个 sidecar 任务先写失败测试 → 实现 → 通过 → commit。测试加进 `sidecar/tests/`，随现有 756 体系跑。
- **测试入口约定**：测试文件顶部 `sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))`，从 `yibao_brain` 导入（见 `tests/test_distiller.py`）。
- **sidecar 测试命令**：`cd sidecar && .venv/bin/python -m pytest tests/<file>::<test> -v`；全量 `.venv/bin/python -m pytest -q`。
- **前端命令**：`cd app && npx vue-tsc --noEmit && npx vite build`；Rust：`cd app && cargo check --manifest-path src-tauri/Cargo.toml` + `cargo test --manifest-path src-tauri/Cargo.toml`。
- **闸门语义**：反刍出站需 `perception.master AND perception.distill AND perception.recap` 全开（与 Distiller 对齐 master 从属）。
- **命名**：IPC rtype 用 snake_case（`recap_check` / `distill_timeline`）；Rust 事件名 `brain-<rtype>`（`brain-distill-timeline`）；前端 `brain.ts` 方法 camelCase。
- **commit 粒度**：每个任务一个 commit，消息中文 + scope，如 `feat(recap): ...`。仅提交本任务相关文件。

## File Structure

**新建：**
- `sidecar/src/yibao_brain/background.py` — 三个后台循环 + 纯 helper（从 server.py 搬出）。
- `sidecar/tests/test_recap.py` — 反刍选材/拼装/去重/闸门/查询纯逻辑测试。

**修改（sidecar）：**
- `sidecar/src/yibao_brain/distiller.py` — `_DISTILL_PROMPT` 升级；`gather_summary` 返回扩容；`DistillerStore` 加 meta 表/runs.stats 迁移/recap 去重/recent_days/record_run stats；新增 `recap_select`/`build_recap_text`。
- `sidecar/src/yibao_brain/config.py` — `_SETTINGS_DEFAULTS` 加 `perception.recap`。
- `sidecar/src/yibao_brain/server.py` — 加 `recap_check`/`distill_timeline` IPC 分支；循环搬出后改为从 background 导入。

**修改（前端）：**
- `app/src/lib/brain.ts` — `recapCheck()` / `fetchDistillTimeline` / `getDistillTimelineOnce` / `DistillDay` 类型 / `onRecapOpen`。
- `app/src/components/HomeFeed.vue` — 顶部 toggle「动态｜回顾」；回顾 mode 渲染；recap_check 开窗触发；`recap-open` 监听 deep-link。
- `app/src/App.vue` — recap（morning_recap）气泡点击 deep-link。
- `app/src/components/SettingsView.vue` — `perception.recap` 开关（distill 之下，依赖提示）。

**修改（Rust）：**
- `app/src-tauri/src/lib.rs` — `recap_check`/`get_distill_timeline` 命令；`distill_timeline` 桥接 arm；`generate_handler!` 注册。

---

### Task 1: DistillerStore — meta 表 + recap 去重 + runs.stats 迁移

**Files:**
- Modify: `sidecar/src/yibao_brain/distiller.py`（`_SCHEMA` ~L29、`DistillerStore.__init__` ~L57、`record_run` ~L115）
- Test: `sidecar/tests/test_recap.py`（新建）

**Interfaces:**
- Produces: `DistillerStore.set_recap_day(day: str) -> None`、`DistillerStore.recap_last_day() -> str | None`、`record_run(..., stats: dict | None = None)`；`runs` 表新增 `stats TEXT` 列（幂等迁移）；新 `meta(key, value)` 表。

- [ ] **Step 1: 写失败测试**

新建 `sidecar/tests/test_recap.py`：

```python
"""晨间反刍 + 每日回顾：纯逻辑与存储测试。"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from yibao_brain.distiller import DistillerStore  # noqa: E402

def _store(tmp_path):
    return DistillerStore(str(tmp_path / "distill.db"))

def test_recap_dedup_roundtrip(tmp_path):
    s = _store(tmp_path)
    assert s.recap_last_day() is None        # 初始无标记
    s.set_recap_day("2026-08-04")
    assert s.recap_last_day() == "2026-08-04"
    s.set_recap_day("2026-08-05")             # 覆盖
    assert s.recap_last_day() == "2026-08-05"
    s.close()

def test_recap_marker_survives_reopen(tmp_path):
    """去重标记持久化——新 store 实例（模拟重启）仍命中。"""
    db = str(tmp_path / "distill.db")
    s = DistillerStore(db)
    s.set_recap_day("2026-08-04")
    s.close()
    s2 = DistillerStore(db)
    assert s2.recap_last_day() == "2026-08-04"
    s2.close()

def test_record_run_persists_stats(tmp_path):
    s = _store(tmp_path)
    s.record_run("2026-08-04", "2026-08-03", "auto", "ok",
                 stats={"app_seconds": {"VSCode": 11520}, "active_blocks": [["09:00", "11:00"]]})
    row = s._conn.execute(
        "SELECT stats FROM runs WHERE target_day=?", ("2026-08-03",)
    ).fetchone()
    import json
    assert json.loads(row["stats"])["app_seconds"]["VSCode"] == 11520
    s.close()

def test_runs_stats_migration_idempotent(tmp_path):
    """重复打开存量库不报 duplicate column。"""
    db = str(tmp_path / "distill.db")
    DistillerStore(db).close()
    DistillerStore(db).close()  # 不抛即过
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd sidecar && .venv/bin/python -m pytest tests/test_recap.py -v`
Expected: FAIL（`set_recap_day` 不存在 / `stats` 列不存在）

- [ ] **Step 3: 实现**

`distiller.py` 改三处：

(a) `_SCHEMA` 末尾加 meta 表：
```python
_SCHEMA = """
CREATE TABLE IF NOT EXISTS distillations ( ... );   # 不动
CREATE TABLE IF NOT EXISTS runs ( ... );             # 不动
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""
```

(b) `DistillerStore.__init__` 在 `executescript(_SCHEMA)` 之后加 `runs.stats` 幂等迁移（仿 feed.py）：
```python
        self._conn.executescript(_SCHEMA)
        try:
            self._conn.execute("ALTER TABLE runs ADD COLUMN stats TEXT")
            self._conn.commit()
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise
```

(c) 加 recap 去重方法 + 给 `record_run` 加 `stats` 入参：
```python
    def set_recap_day(self, day: str) -> None:
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO meta(key,value) VALUES('recap_last_day',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (day,))
                self._conn.commit()
        except Exception as e:
            print(f"[yibao] recap 标记失败：{e}", file=sys.stderr)

    def recap_last_day(self) -> str | None:
        try:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key='recap_last_day'").fetchone()
            return str(row["value"]) if row else None
        except Exception:
            return None
```
`record_run` 签名改：`def record_run(self, run_day, target_day, source, status, error=None, stats=None):`，INSERT 列加 `stats`，值 `json.dumps(stats, ensure_ascii=False) if stats else None`。

- [ ] **Step 4: 跑测试验证通过**

Run: `cd sidecar && .venv/bin/python -m pytest tests/test_recap.py -v`
Expected: 4 passed

- [ ] **Step 5: 回归 + commit**

Run: `cd sidecar && .venv/bin/python -m pytest -q`
Expected: 760 passed（756 + 4）
```bash
git add sidecar/src/yibao_brain/distiller.py sidecar/tests/test_recap.py
git commit -m "feat(recap): DistillerStore recap 去重标记 + runs.stats 列"
```

---

### Task 2: DistillerStore.recent_days(n) 跨天查询

**Files:**
- Modify: `sidecar/src/yibao_brain/distiller.py`（`DistillerStore` 类内新增方法）
- Test: `sidecar/tests/test_recap.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `runs.stats` 列、现有 `distillations`/`runs` 表。
- Produces: `DistillerStore.recent_days(n: int = 14) -> list[dict]`，每元素 `{"day": str, "status": str, "stats": dict, "items": list[dict]}`；无 run 的天 status="pending"、items=[]、stats={}；按 day 倒序。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_recap.py`：
```python
def test_recent_days_aggregates_status_stats_items(tmp_path):
    s = _store(tmp_path)
    s.add("2026-08-03", "insight", "切了 14 次", confidence=0.8)
    s.record_run("2026-08-04", "2026-08-03", "auto", "ok",
                 stats={"app_seconds": {"VSCode": 11520}})
    days = s.recent_days(3)
    by_day = {d["day"]: d for d in days}
    assert by_day["2026-08-03"]["status"] == "ok"
    assert by_day["2026-08-03"]["stats"]["app_seconds"]["VSCode"] == 11520
    assert len(by_day["2026-08-03"]["items"]) == 1
    # 没提炼的天：pending + 空
    assert by_day["2026-08-02"]["status"] == "pending"
    assert by_day["2026-08-02"]["items"] == []
    # 倒序
    assert days[0]["day"] > days[1]["day"]
    s.close()
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd sidecar && .venv/bin/python -m pytest tests/test_recap.py::test_recent_days_aggregates_status_stats_items -v`
Expected: FAIL（`recent_days` 不存在）

- [ ] **Step 3: 实现**

`DistillerStore` 内加：
```python
    def recent_days(self, n: int = 14) -> list[dict]:
        """近 n 天：每天聚合 runs(状态+stats) 与 distillations(items)。无 run 的天 status=pending。"""
        from datetime import date, timedelta
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
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd sidecar && .venv/bin/python -m pytest tests/test_recap.py -v`
Expected: all passed

- [ ] **Step 5: commit**
```bash
git add sidecar/src/yibao_brain/distiller.py sidecar/tests/test_recap.py
git commit -m "feat(recap): DistillerStore.recent_days 跨天聚合查询"
```

---

### Task 3: gather_summary 返回 app_seconds + active_ranges

**Files:**
- Modify: `sidecar/src/yibao_brain/distiller.py`（`gather_summary` ~L172，返回 stats 扩容）
- Test: `sidecar/tests/test_distiller.py`（追加）

**Interfaces:**
- Produces: `gather_summary(...)` 返回的 stats dict 增加 `app_seconds: dict[str,float]`（秒）与 `active_ranges: list[[start_ts,end_ts]]`（活跃≥30min 段）。已有 `app_count`/`screen_count` 保留。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_distiller.py`（复用现有 `_pstore` helper）：
```python
def test_gather_summary_returns_activity_stats(tmp_path):
    p = _pstore(tmp_path)
    day, start, end = yesterday_window()
    p.append("app", "frontmost", {"app": "VSCode", "title": "a.py"}, "S1", ts=start + 100)
    p.append("app", "frontmost", {"app": "VSCode", "title": "b.py"}, "S2", ts=start + 2000)
    _summary, stats = gather_summary(p, start, end)
    assert "app_seconds" in stats and "active_ranges" in stats
    assert stats["app_seconds"]["VSCode"] > 0
    assert all(isinstance(r, list) and len(r) == 2 for r in stats["active_ranges"])
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd sidecar && .venv/bin/python -m pytest tests/test_distiller.py::test_gather_summary_returns_activity_stats -v`
Expected: FAIL（KeyError: 'app_seconds'）

- [ ] **Step 3: 实现**

`gather_summary` 内：`app_seconds` dict 已存在（L199-204）；活跃段循环（L209-217）改为同时收集区间：
```python
    active_ranges: list[list[float]] = []
    for seg in segments:
        if seg.get("activity") == "active" and seg["end_ts"] - seg["start_ts"] >= 1800:
            s = time.strftime("%H:%M", time.localtime(seg["start_ts"]))
            e = time.strftime("%H:%M", time.localtime(seg["end_ts"]))
            head.append(f"- {s}–{e}")
            active_ranges.append([seg["start_ts"], seg["end_ts"]])
```
返回 stats 改为：
```python
    stats = {"app_count": len(app_seconds), "screen_count": len(kept),
             "app_seconds": dict(app_seconds), "active_ranges": active_ranges}
```
（`app_seconds` 原为局部 dict，直接 `dict(app_seconds)` 拷出。）

- [ ] **Step 4: 跑测试验证通过**

Run: `cd sidecar && .venv/bin/python -m pytest tests/test_distiller.py -v`
Expected: all passed（含新测）

- [ ] **Step 5: commit**
```bash
git add sidecar/src/yibao_brain/distiller.py sidecar/tests/test_distiller.py
git commit -m "feat(recap): gather_summary 返回 app 时长与活跃段统计"
```

---

### Task 4: Distiller.run_yesterday 把 stats 写进 runs 行

**Files:**
- Modify: `sidecar/src/yibao_brain/distiller.py`（`run_yesterday` ~L347，`record_run` 调用处）
- Test: `sidecar/tests/test_distiller.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `record_run(..., stats=)`、Task 3 的 gather_summary 返回 stats。
- Produces: ok 路径的 runs 行带上当天活动 stats（回顾页数据源）。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_distiller.py`：
```python
def test_run_yesterday_persists_activity_stats(tmp_path):
    p = _pstore(tmp_path)
    day, start, end = yesterday_window()
    p.append("app", "frontmost", {"app": "VSCode", "title": "a.py"}, "S1", ts=start + 100)
    provider = FakeProvider(text=_GOOD_JSON)
    d, mem, feed = _distiller(tmp_path, provider, p)
    d.run_yesterday("manual")
    row = d.store._conn.execute(
        "SELECT stats FROM runs WHERE target_day=? AND status='ok'", (day,)).fetchone()
    import json
    stats = json.loads(row["stats"])
    assert stats["app_seconds"]["VSCode"] > 0
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd sidecar && .venv/bin/python -m pytest tests/test_distiller.py::test_run_yesterday_persists_activity_stats -v`
Expected: FAIL（stats 为 None）

- [ ] **Step 3: 实现**

`run_yesterday` 内，ok 路径的 `self.store.record_run(run_day, target_day, source, "ok")` 改为带 stats：
```python
            counts = self._project(target_day, result)
            self.store.record_run(run_day, target_day, source, "ok", stats=stats)
            return {"status": "ok", "day": target_day, **counts}
```
（`stats` 变量来自 `summary, stats = gather_summary(...)`，已是含 app_seconds/active_ranges 的 dict。no_data/failed 路径不传 stats，保持 None。）

- [ ] **Step 4: 跑测试验证通过**

Run: `cd sidecar && .venv/bin/python -m pytest tests/test_distiller.py -v`
Expected: all passed

- [ ] **Step 5: commit**
```bash
git add sidecar/src/yibao_brain/distiller.py sidecar/tests/test_distiller.py
git commit -m "feat(recap): 提炼成功时把活动 stats 写进 runs 行"
```

---

### Task 5: Distiller prompt 升级（insight 带「现象+建议」）

**Files:**
- Modify: `sidecar/src/yibao_brain/distiller.py`（`_DISTILL_PROMPT` ~L265）
- Test: `sidecar/tests/test_distiller.py`（追加）

**Interfaces:**
- Produces: `_DISTILL_PROMPT` 要求 insight 为「现象 + 具体可执行建议」，明确「只给有观察依据的建议，没有就留空，不编造领域专精建议」。解析链路不变（`parse_distill_output` 已吃任意 text）。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_distiller.py`（验证带建议的 insight 能走完管线被选出——守护行为，不测 prompt 措辞）：
```python
def test_advice_bearing_insight_flows_through(tmp_path):
    """prompt 升级后 LLM 产出的『现象+建议』insight 应原样落库、可被选材。"""
    p = _pstore(tmp_path)
    day, start, end = yesterday_window()
    p.append("app", "frontmost", {"app": "VSCode", "title": "a.py"}, "S1", ts=start + 100)
    advice_json = ('{"patterns":[],"insights":['
        '{"text":"下午同个报错在编辑器/浏览器间切了 14 次——建议把报错全文贴给 AI 一次问清，省掉来回切换",'
        '"confidence":0.85}],"events":[]}')
    provider = FakeProvider(text=advice_json)
    d, mem, feed = _distiller(tmp_path, provider, p)
    d.run_yesterday("manual")
    items = d.store.day_items(day)
    assert any("建议" in i["text"] for i in items if i["kind"] == "insight")
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd sidecar && .venv/bin/python -m pytest tests/test_distiller.py::test_advice_bearing_insight_flows_through -v`
Expected: 先确认测试本身能过（这测的是管线，prompt 改前也应过）。若过，则此测试是回归守护——继续 Step 3 改 prompt，Step 4 重跑确认仍过。

- [ ] **Step 3: 实现（升级 prompt）**

`_DISTILL_PROMPT` 的 insights 条目说明改为：
```python
_DISTILL_PROMPT = """你是个人数字生活的分析助手。根据用户昨日的设备使用摘要，提炼三类结论，严格输出 JSON（不要输出任何其他文字）：

{
  "patterns": [{"text": "……", "confidence": 0.0-1.0}],
  "insights": [{"text": "……", "confidence": 0.0-1.0}],
  "events": [{"text": "……", "confidence": 0.0-1.0}]
}

- patterns：稳定可复用的使用/作息模式（将写入长期记忆），宁缺毋滥，≤5 条
- insights：基于观察的效率建议，每条写成「现象——具体可执行建议」，例如「下午同个报错在编辑器/浏览器间切了 14 次——建议把报错全文贴给 AI 一次问清，省掉来回切换」。只给有时间/切换/作息等观察依据的建议；没有就留空，不要编造领域专精建议（如该怎么写代码/做设计）。≤5 条
- events：值得记账的重要事件（如深夜工作、超长连续专注），≤5 条
每条 text 用中文一句话，具体、带数字。没有就给空数组。"""
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd sidecar && .venv/bin/python -m pytest tests/test_distiller.py -v`
Expected: all passed（含守护测）

- [ ] **Step 5: commit**
```bash
git add sidecar/src/yibao_brain/distiller.py sidecar/tests/test_distiller.py
git commit -m "feat(recap): Distiller prompt 升级——insight 带现象+可执行建议"
```

---

### Task 6: recap_select + build_recap_text 纯函数

**Files:**
- Modify: `sidecar/src/yibao_brain/distiller.py`（模块级新增两函数，放 `parse_distill_output` 之后）
- Test: `sidecar/tests/test_recap.py`（追加）

**Interfaces:**
- Produces: `recap_select(items: list[dict]) -> list[dict]`（insight 置信降序 ≤3；无则最新 event 1 条；再无返 `[]`；pattern 不入选）；`build_recap_text(items: list[dict]) -> str`（模板拼「早上好。昨天我注意到：\n① …」）。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_recap.py`：
```python
from yibao_brain.distiller import recap_select, build_recap_text  # noqa: E402

def _item(kind, text, conf=None, id=1):
    d = {"id": id, "day": "2026-08-03", "kind": kind, "text": text,
         "data": {}, "confidence": conf, "projected": 0, "created_at": 0.0}
    return d

def test_recap_select_insights_by_confidence():
    items = [_item("insight", "低", 0.4), _item("insight", "高", 0.9),
             _item("insight", "中", 0.7), _item("insight", "四", 0.65)]
    sel = recap_select(items)
    assert [s["text"] for s in sel] == ["高", "中", "四"]   # 降序 ≤3，0.4 被挤掉

def test_recap_select_falls_back_to_event():
    items = [_item("pattern", "模式", 0.9), _item("event", "深夜活跃", 0.9, id=2)]
    sel = recap_select(items)
    assert len(sel) == 1 and sel[0]["kind"] == "event"

def test_recap_select_empty_when_nothing():
    assert recap_select([]) == []
    assert recap_select([_item("pattern", "仅模式", 0.9)]) == []

def test_build_recap_text_format():
    sel = [_item("insight", "建议A"), _item("insight", "建议B")]
    txt = build_recap_text(sel)
    assert txt.startswith("早上好")
    assert "①" in txt and "建议A" in txt and "②" in txt and "建议B" in txt
    assert build_recap_text([]) == ""
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd sidecar && .venv/bin/python -m pytest tests/test_recap.py -v`
Expected: FAIL（`recap_select` 不存在）

- [ ] **Step 3: 实现**

`distiller.py` 加：
```python
def recap_select(items: list[dict]) -> list[dict]:
    """反刍选材：insight 置信降序 ≤3；无则最新 event 1 条兜底；pattern 不入选；空返 []。"""
    insights = [i for i in items if i.get("kind") == "insight"]
    if insights:
        return sorted(insights, key=lambda x: -(x.get("confidence") or 0))[:3]
    events = [i for i in items if i.get("kind") == "event"]
    if events:
        return [sorted(events, key=lambda x: x.get("id", 0))[-1]]
    return []

def build_recap_text(items: list[dict]) -> str:
    """模板拼昨日简报。空输入返空串（调用方据此跳过）。"""
    if not items:
        return ""
    nums = "①②③④⑤⑥⑦⑧⑨⑩"
    lines = [f"{nums[i]} {it['text']}" for i, it in enumerate(items[:3])]
    return "早上好。昨天我注意到：\n" + "\n".join(lines)
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd sidecar && .venv/bin/python -m pytest tests/test_recap.py -v`
Expected: all passed

- [ ] **Step 5: commit**
```bash
git add sidecar/src/yibao_brain/distiller.py sidecar/tests/test_recap.py
git commit -m "feat(recap): recap_select 选材 + build_recap_text 拼装纯函数"
```

---

### Task 7: server.py — recap_check + distill_timeline IPC + 设置默认

**Files:**
- Modify: `sidecar/src/yibao_brain/server.py`（dispatch ~L1191 加两分支；`distill_timeline` 回包）
- Modify: `sidecar/src/yibao_brain/config.py`（`_SETTINGS_DEFAULTS` ~L249）
- Test: `sidecar/tests/test_recap.py`（追加，纯函数级测闸门/选材/去重编排；server 端到端在 Task 8 后由现有 test_server 模式覆盖）

**Interfaces:**
- Consumes: Task 1/2/6 的 store 方法与纯函数；serve_async 内的 `settings`、`distiller`、`_emit_event`（= `proactive_dispatcher.emit`，server.py:650）。
- Produces: IPC `recap_check`（fire-and-forget：闸门→去重→选材→emit reminder(morning_recap)→标记）；IPC `distill_timeline`（回 `{"type":"distill_timeline","days":[...]}`）；设置默认 `perception.recap: False`。

- [ ] **Step 1: 写失败测试（闸门+去重+选材编排，纯逻辑可单测）**

追加到 `tests/test_recap.py`。抽一个不依赖 event loop 的编排函数 `_recap_decide`（见 Step 3，server 里调它），单测它：
```python
from yibao_brain.server import _recap_decide  # noqa: E402

def test_recap_decide_gates_off():
    """闸门任一关 → 不出。"""
    assert _recap_decide(settings={"perception.master": False,
        "perception.distill": True, "perception.recap": True},
        last_recap_day="2026-08-04", today="2026-08-05",
        yesterday_items=[_item("insight", "x", 0.9)]) is None
    assert _recap_decide(settings={"perception.master": True,
        "perception.distill": True, "perception.recap": False},
        last_recap_day=None, today="2026-08-05",
        yesterday_items=[_item("insight", "x", 0.9)]) is None

def test_recap_decide_dedup_today():
    """今天已反刍 → 不出。"""
    assert _recap_decide(settings={"perception.master": True,
        "perception.distill": True, "perception.recap": True},
        last_recap_day="2026-08-05", today="2026-08-05",
        yesterday_items=[_item("insight", "x", 0.9)]) is None

def test_recap_decide_no_content():
    """昨日无产物 → 不出。"""
    assert _recap_decide(settings={"perception.master": True,
        "perception.distill": True, "perception.recap": True},
        last_recap_day=None, today="2026-08-05", yesterday_items=[]) is None

def test_recap_decide_returns_text_and_day():
    r = _recap_decide(settings={"perception.master": True,
        "perception.distill": True, "perception.recap": True},
        last_recap_day=None, today="2026-08-05",
        yesterday_items=[_item("insight", "切了14次——建议…", 0.9)])
    assert r is not None and "建议" in r["text"] and r["day"] is not None
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd sidecar && .venv/bin/python -m pytest tests/test_recap.py -v -k recap_decide`
Expected: FAIL（`_recap_decide` 不存在）

- [ ] **Step 3: 实现**

(a) `config.py` `_SETTINGS_DEFAULTS` 在 `"perception.distill": False,`（~L258）后加一行：
```python
    "perception.recap": False,
```

(b) `server.py` 在 `serve_async` 之外的模块级（`_gate_proactive_event` 附近）加纯函数：
```python
def _recap_decide(*, settings: dict, last_recap_day: str | None, today: str,
                  yesterday_items: list[dict]) -> dict | None:
    """反刍编排（纯逻辑，可单测）：闸门→去重→选材→拼装。返回 {text, day} 或 None。"""
    if not (settings.get("perception.master") and settings.get("perception.distill")
            and settings.get("perception.recap")):
        return None
    if last_recap_day == today:
        return None
    from .distiller import recap_select, build_recap_text, yesterday_window
    selected = recap_select(yesterday_items)
    if not selected:
        return None
    text = build_recap_text(selected)
    if not text:
        return None
    _day, _s, _e = yesterday_window()   # 目标日 = 昨天
    return {"text": text, "day": _day}
```

(c) `server.py` dispatch（`elif rtype == "distill_now":` 分支后）加两分支：
```python
        elif rtype == "recap_check":
            try:
                import datetime as _dt
                today = _dt.date.today().isoformat()
                decide = _recap_decide(
                    settings=settings,
                    last_recap_day=distiller.store.recap_last_day() if distiller else None,
                    today=today,
                    yesterday_items=(distiller.store.day_items(
                        (_dt.date.today() - _dt.timedelta(days=1)).isoformat())
                        if distiller else []),
                )
                if decide is not None:
                    _emit_event({"kind": "reminder", "type": "morning_recap",
                                 "text": decide["text"], "day": decide["day"]})
                    if distiller is not None:
                        distiller.store.set_recap_day(today)
            except Exception as e:
                print(f"[yibao] recap_check 失败：{e}", file=sys.stderr)
        elif rtype == "distill_timeline":
            try:
                days = int(msg.get("days") or 14)
                write_msg({"type": "distill_timeline",
                           "days": distiller.store.recent_days(days) if distiller else []})
            except Exception as e:
                print(f"[yibao] distill_timeline 失败：{e}", file=sys.stderr)
                write_msg({"type": "distill_timeline", "days": []})
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd sidecar && .venv/bin/python -m pytest tests/test_recap.py -v`
Expected: all passed

- [ ] **Step 5: 回归 + commit**

Run: `cd sidecar && .venv/bin/python -m pytest -q`
Expected: all passed（含新 _recap_decide 测）
```bash
git add sidecar/src/yibao_brain/server.py sidecar/src/yibao_brain/config.py sidecar/tests/test_recap.py
git commit -m "feat(recap): server recap_check/distill_timeline IPC + perception.recap 默认"
```

---

### Task 8: 拆分——后台循环搬到 background.py（纯搬运，756 兜底）

**Files:**
- Create: `sidecar/src/yibao_brain/background.py`
- Modify: `sidecar/src/yibao_brain/server.py`（删搬出的定义，改为 `from .background import ...`）
- Test: 现有 `tests/test_server.py` / `tests/test_watch*.py` / `tests/test_distiller.py` 等作回归网。

**纪律：** 纯搬运，不改逻辑、不改签名。任何行为变更另开 commit。搬运后 756+ 测试必须全绿。

**搬运清单（从 server.py 整体迁出）：**
- `_perception_cleanup_loop`、`_distiller_loop`、`_reminder_loop`（三个 asyncio 循环）
- 各自依赖的纯/helper 函数：`_gate_proactive_event`、`_dispatch_reminder`、`_recover_background_jobs`、`_watch_tick`、`_proactive_level`、`_dock_list`、`_plugin_summaries_list`、`_consume_invoke_context`、`_describe_screen`（按被引用关系顺带；若某函数也被 IPC dispatch 直接用，则保留在 server.py 或两边都 import——以"搬运后测试全绿"为准，逐一迁）。
- **不搬**：`serve_async`、`build_loop`、`handle_panel_action`、IPC dispatch 主循环（`rtype` if/elif 链）、voice/tts 泵。

- [ ] **Step 1: 建空文件 + 占位**

创建 `sidecar/src/yibao_brain/background.py`，顶部：
```python
"""后台循环与纯 helper（从 server.py 拆出，server 只留调度与 IPC 分发）。

纪律：本模块的函数从 server.py 原样搬来，不改逻辑；改行为请另开 commit。
"""
from __future__ import annotations
```

- [ ] **Step 2: 逐个搬迁（一次一个函数/循环，每搬一批跑全量测试）**

顺序建议：先搬无依赖的纯函数（`_proactive_level`、`_gate_proactive_event`、`_dock_list`、`_plugin_summaries_list`、`_consume_invoke_context`、`_describe_screen`、`_watch_tick`、`_recover_background_jobs`），再搬 `_dispatch_reminder`，最后搬三个 `_*_loop`。每搬一批：
- 从 server.py 删除该定义；
- server.py 顶部加 `from .background import _proactive_level, _gate_proactive_event, ...`（按实际搬的补全）；
- background.py 内补这些函数所需的 import（`asyncio`/`time`/`sys` 等）。

每批后跑：`cd sidecar && .venv/bin/python -m pytest -q`，必须全绿才继续下一批。

- [ ] **Step 3: 跑全量回归**

Run: `cd sidecar && .venv/bin/python -m pytest -q`
Expected: 全绿（数量与 Task 7 后一致；若有循环的单测，确认仍跑）

- [ ] **Step 4: 冒烟——确认 serve_async 仍调度循环**

Run: `cd sidecar && .venv/bin/python -c "from yibao_brain.server import serve_async; from yibao_brain import background; print('ok', hasattr(background, '_distiller_loop'))"`
Expected: `ok True`

- [ ] **Step 5: commit**
```bash
git add sidecar/src/yibao_brain/background.py sidecar/src/yibao_brain/server.py
git commit -m "refactor(server): 后台循环与纯 helper 拆到 background.py（纯搬运）"
```

---

### Task 9: 前端 brain.ts — IPC 方法与类型

**Files:**
- Modify: `app/src/lib/brain.ts`

**Interfaces:**
- Produces: `recapCheck(): Promise<void>`（invoke `recap_check`）；`DistillDay` 类型；`fetchDistillTimeline(days?)`/`getDistillTimelineOnce(days?, timeoutMs?)`（命令 → `brain-distill-timeline` 事件）；`onRecapOpen(cb)`（监听全局 `recap-open` 事件，deep-link 用）；`emitRecapOpen(day)`（pet 窗发出 deep-link）。

- [ ] **Step 1: 实现（前端无单测，以 tsc+build 为门）**

`brain.ts` 加（仿现有 `distillNow`/`getFeedStatsOnce`/`onFeed` 模式）：
```typescript
/** 反刍：开窗时 fire-and-forget 触发，大脑自行决定推不推。 */
export function recapCheck(): Promise<void> {
  return invoke("recap_check");
}

export interface DistillDay {
  day: string;
  status: string;        // ok | failed | no_data | pending
  stats: { app_seconds?: Record<string, number>; active_ranges?: number[][]; [k: string]: unknown };
  items: { id: number; kind: string; text: string; confidence?: number }[];
}

/** 每日回顾：发查询并等 brain-distill-timeline；大脑不在线/超时返回空。 */
export async function getDistillTimelineOnce(days = 14, timeoutMs = 3000): Promise<DistillDay[]> {
  return new Promise((resolve) => {
    once<{ days: DistillDay[] }>("brain-distill-timeline", (ev) => resolve(ev.payload.days));
    void invoke("get_distill_timeline", { days });
    setTimeout(() => resolve([]), timeoutMs);
  });
}

export function fetchDistillTimeline(days = 14): Promise<void> {
  return invoke("get_distill_timeline", { days });
}

/** deep-link：pet 窗气泡点击 → 通知 home 窗切回顾 mode + 跳当天。 */
export function emitRecapOpen(day: string): Promise<void> {
  return emit("recap-open", { day });
}
export function onRecapOpen(cb: (day: string) => void): Promise<UnlistenFn> {
  return listen<{ day: string }>("recap-open", (e) => cb(e.payload.day));
}
```
（顶部确认已 import `invoke`、`listen`、`emit`、`once`、`UnlistenFn`——`emit` 若未导入则从 `@tauri-apps/api/event` 补。）

- [ ] **Step 2: 类型 + 构建验证**

Run: `cd app && npx vue-tsc --noEmit && npx vite build`
Expected: exit 0

- [ ] **Step 3: commit**
```bash
git add app/src/lib/brain.ts
git commit -m "feat(recap): brain.ts recap/distill-timeline IPC + deep-link 事件"
```

---

### Task 10: Rust — recap_check/get_distill_timeline 命令 + 桥接

**Files:**
- Modify: `app/src-tauri/src/lib.rs`（命令 ~L797 后、桥接 ~L458 后、`generate_handler!` ~L1551）

**Interfaces:**
- Produces: `#[tauri::command] recap_check` / `get_distill_timeline`（`write_to_brain` 转发）；桥接 arm `Some("distill_timeline") => app.emit("brain-distill-timeline", v)`；两命令注册进 `generate_handler!`。

- [ ] **Step 1: 实现**

(a) 命令（紧接 `get_feed_stats` 函数后）：
```rust
/// 晨间反刍探测：开窗时前端调用，大脑自行决定推不推（fire-and-forget）。
#[tauri::command]
fn recap_check(state: tauri::State<Brain>) -> Result<(), String> {
    write_to_brain(&state, serde_json::json!({ "id": 0, "type": "recap_check" }))
}

/// 每日回顾查询：大脑回 {"type":"distill_timeline","days":[…]}，经 brain-distill-timeline 事件广播。
#[tauri::command]
fn get_distill_timeline(state: tauri::State<Brain>, days: Option<u32>) -> Result<(), String> {
    write_to_brain(
        &state,
        serde_json::json!({ "id": 0, "type": "distill_timeline", "days": days.unwrap_or(14) }),
    )
}
```

(b) 桥接 arm（`Some("feed_stats")` arm 之后）：
```rust
                            // 每日回顾响应：整体转发
                            Some("distill_timeline") => {
                                let _ = app.emit("brain-distill-timeline", v);
                            }
```

(c) `generate_handler!` 列表（`get_feed_stats,` 之后）加：
```rust
            recap_check,
            get_distill_timeline,
```

- [ ] **Step 2: 编译验证**

Run: `cd app && cargo check --manifest-path src-tauri/Cargo.toml`
Expected: exit 0

- [ ] **Step 3: commit**
```bash
git add app/src-tauri/src/lib.rs
git commit -m "feat(recap): Rust recap_check/get_distill_timeline 命令 + 桥接"
```

---

### Task 11: HomeFeed.vue — 回顾 toggle + 回顾视图 + recap_check 触发 + deep-link

**Files:**
- Modify: `app/src/components/HomeFeed.vue`

**Interfaces:**
- Consumes: Task 9 的 `recapCheck`/`getDistillTimelineOnce`/`onRecapOpen`/`DistillDay`。
- Produces: 顶部 Segmented toggle「动态｜回顾」；回顾 mode 按天卡片（app 时长 + 活跃段 + 洞察/事件 + 状态徽章）；开窗触发 recap_check；收到 `recap-open` 切回顾 mode 并滚到指定天。

- [ ] **Step 1: script 改造**

在 `<script setup>` 顶部 import 补 `recapCheck, getDistillTimelineOnce, onRecapOpen` 与 `DistillDay`、`getCurrentWindow`：
```typescript
import { getCurrentWindow } from "@tauri-apps/api/window";
import { recapCheck, getDistillTimelineOnce, onRecapOpen, type DistillDay } from "../lib/brain";
```
加视图状态与回顾数据：
```typescript
type FeedView = "feed" | "recap";
const view = ref<FeedView>("feed");
const recapDays = ref<DistillDay[]>([]);
const recapLoaded = ref(false);
const recapFocusDay = ref<string | null>(null);

async function loadRecap() {
  recapDays.value = await getDistillTimelineOnce(14);
  recapLoaded.value = true;
}
function fmtHours(sec: number): string {
  return sec > 0 ? `${(sec / 3600).toFixed(1)}h` : "";
}
function activeRangesLabel(stats: DistillDay["stats"]): string {
  const rs = stats.active_ranges ?? [];
  if (!rs.length) return "";
  const f = (t: number) => `${String(new Date(t * 1000).getHours()).padStart(2, "0")}:${String(new Date(t * 1000).getMinutes()).padStart(2, "0")}`;
  return rs.slice(0, 3).map((r) => `${f(r[0])}–${f(r[1])}`).join(" · ");
}
function statusLabel(s: string): string {
  return ({ ok: "已提炼", failed: "提炼失败", no_data: "当日无数据", pending: "未提炼" } as Record<string, string>)[s] ?? s;
}
function recapInsights(d: DistillDay) { return d.items.filter((i) => i.kind === "insight"); }
function recapEvents(d: DistillDay) { return d.items.filter((i) => i.kind === "event"); }
```
开窗触发 + deep-link，挂进现有 `onMounted`（与 `reload()` 并列）：
```typescript
let unRecapOpen: (() => void) | null = null;
// 在 onMounted 内追加：
void (async () => {
  try {
    const win = getCurrentWindow();
    const fire = () => { void recapCheck().catch(() => {}); };
    if (await win.isVisible()) fire();
    const un = await win.onVisibleChange((v) => { if (v) fire(); });
    // 存 un 以便 onUnmounted 释放（赋给一个模块级变量）
    (window as any).__unRecapVisible = un;
  } catch { /* 非 tauri 环境（设计预览）忽略 */ }
})();
unRecapOpen = await onRecapOpen((day) => {
  view.value = "recap";
  recapFocusDay.value = day;
  if (!recapLoaded.value) void loadRecap();
});
```
切到回顾 mode 时按需加载：
```typescript
watch(view, (v) => { if (v === "recap" && !recapLoaded.value) void loadRecap(); });
```
`onUnmounted` 追加 `unRecapOpen?.();` 并释放可见性监听。

> 注：`getCurrentWindow().onVisibleChange` 是 Tauri v2 window API；若该钩子名在该版本不存在，改用 `appWindow.listen('tauri://focus')` + `isVisible()` 组合判定（以编译过为准）。

- [ ] **Step 2: template 改造**

在 `.tl-head`（现有 segmented 之上或并排）加视图 toggle；回顾 mode 用 `v-if="view==='recap'"` 渲染按天列表，`v-else` 保留现有时间线：
```html
<!-- 顶部视图切换（macOS Segmented，复用 .segmented 样式） -->
<div class="segmented" style="margin-bottom: var(--yb-space-3)">
  <button class="seg" :class="{ on: view === 'feed' }" @click="view = 'feed'">动态</button>
  <button class="seg" :class="{ on: view === 'recap' }" @click="view = 'recap'">回顾</button>
</div>

<section v-if="view === 'recap'" class="recap-list">
  <div v-for="d in recapDays" :key="d.day" class="recap-day"
       :class="{ focus: d.day === recapFocusDay }">
    <div class="rd-head">
      <strong class="rd-date">{{ d.day }}</strong>
      <span class="rd-status" :class="`st-${d.status}`">{{ statusLabel(d.status) }}</span>
    </div>
    <div v-if="d.status === 'ok'" class="rd-body">
      <p v-if="Object.keys(d.stats.app_seconds ?? {}).length" class="rd-stats yb-num">
        {{ Object.entries(d.stats.app_seconds ?? {}).sort((a,b)=>b[1]-a[1]).slice(0,4).map(([k,v])=>`${k} ${fmtHours(v)}`).join(' · ') }}
      </p>
      <p v-if="activeRangesLabel(d.stats)" class="rd-blocks">深度专注 {{ activeRangesLabel(d.stats) }}</p>
      <ul class="rd-items">
        <li v-for="i in recapInsights(d)" :key="i.id" class="rd-item insight">💡 {{ i.text }}</li>
        <li v-for="i in recapEvents(d)" :key="i.id" class="rd-item event">📌 {{ i.text }}</li>
      </ul>
      <p v-if="!recapInsights(d).length && !recapEvents(d).length" class="rd-empty">这天没有洞察</p>
    </div>
    <p v-else class="rd-empty">{{ statusLabel(d.status) }}</p>
  </div>
  <p v-if="recapLoaded && !recapDays.length" class="rd-empty">暂时没有回顾</p>
</section>

<!-- 原时间线包在 v-else -->
<div v-else class="tl-scroll"> ...现有时间线... </div>
```

- [ ] **Step 3: 样式补丁**

`<style scoped>` 追加回顾卡片最小样式（复用令牌）：
```css
.recap-list { flex: 1; min-height: 0; overflow-y: auto; padding-bottom: var(--yb-space-3); }
.recap-day { padding: var(--yb-space-3) 0; border-bottom: 1px solid var(--yb-card-row-line); }
.recap-day.focus { background: var(--yb-accent-soft); }
.rd-head { display: flex; justify-content: space-between; align-items: baseline; }
.rd-date { font-size: var(--yb-fs-lg); font-weight: var(--yb-fw-bold); color: var(--yb-text-strong); }
.rd-status { font-size: var(--yb-fs-xs); color: var(--yb-text-dim); }
.rd-stats, .rd-blocks { margin: var(--yb-space-1) 0; font-size: var(--yb-fs-md); color: var(--yb-text-dim); }
.rd-items { list-style: none; margin: var(--yb-space-2) 0 0; padding: 0; }
.rd-item { padding: 4px 0; font-size: var(--yb-fs-lg); line-height: var(--yb-lh-ui); }
.rd-item.insight { color: var(--yb-text); }
.rd-item.event { color: var(--yb-text-dim); }
.rd-empty { color: var(--yb-text-faint); font-size: var(--yb-fs-md); }
```

- [ ] **Step 4: 类型 + 构建验证**

Run: `cd app && npx vue-tsc --noEmit && npx vite build`
Expected: exit 0

- [ ] **Step 5: commit**
```bash
git add app/src/components/HomeFeed.vue
git commit -m "feat(recap): HomeFeed 动态|回顾 toggle + 回顾视图 + 开窗 recap_check"
```

---

### Task 12: App.vue — recap 气泡 deep-link

**Files:**
- Modify: `app/src/App.vue`（`case "reminder":` ~L398）

**Interfaces:**
- Consumes: Task 9 的 `emitRecapOpen`。
- Produces: `type === "morning_recap"` 的 reminder 气泡可点击 → `emitRecapOpen(day)` → 触发 HomeFeed 切回顾 mode（Task 11 已监听）。

- [ ] **Step 1: 实现**

`App.vue` import 补 `emitRecapOpen, openHomeWindow`（`openHomeWindow` 已在用，见 L21 区域）。`case "reminder":` 内，push 气泡时区分 morning_recap，给一个可点击标记：
```typescript
    case "reminder": {
      const text = e.text ?? "到点了";
      const isRecap = e.type === "morning_recap";
      bubbles.value.push({ role: "ai", text, icon: "clock", recap: isRecap ? (e.day as string) : undefined } as any);
      // 既有亮窗/attentionNeeded 逻辑保留不动 …
```
给 recap 气泡渲染加点击：在模板里气泡元素上（找到 `bubbles` 渲染处），若 `b.recap` 则 `@click="onRecapClick(b.recap)"`：
```typescript
function onRecapClick(day?: string) {
  if (!day) return;
  void openHomeWindow().catch(() => {});      // 确保 home 窗可见
  void emitRecapOpen(day);                     // 通知 home 切回顾 + 跳当天
}
```
（气泡 `recap` 字段在类型上用 `as any` 或给 bubble 类型加可选 `recap?: string`——以 tsc 过为准。）

- [ ] **Step 2: 类型 + 构建验证**

Run: `cd app && npx vue-tsc --noEmit && npx vite build`
Expected: exit 0

- [ ] **Step 3: commit**
```bash
git add app/src/App.vue
git commit -m "feat(recap): morning_recap 气泡点击 deep-link 到回顾"
```

---

### Task 13: SettingsView.vue — perception.recap 开关

**Files:**
- Modify: `app/src/components/SettingsView.vue`

**Interfaces:**
- Produces: 设置页「感知」组、`perception.distill` 之下新增 `perception.recap` 行内确认开关；distill 关时 recap 禁用 + 依赖提示文案。

- [ ] **Step 1: 实现**

参照该文件现有 `perception.distill` 开关的写法（行内两段确认模式），在其下方加 recap 开关：绑定同一 settings 对象的 `perception.recap`；当 `perception.distill` 为 false 时，recap 开关 `:disabled="!distillOn"` 并显示提示「需先开启每日提炼」。确认文案：「打开后，每天首次打开主窗时，译宝会主动把昨日的效率洞察与建议端给你（受打扰度旋钮管，可随时关）。」

- [ ] **Step 2: 类型 + 构建验证**

Run: `cd app && npx vue-tsc --noEmit && npx vite build`
Expected: exit 0

- [ ] **Step 3: commit**
```bash
git add app/src/components/SettingsView.vue
git commit -m "feat(recap): 设置页 perception.recap 开关（依赖 distill）"
```

---

### Task 14: 全量验收 + 真机清单

**Files:** 无（验证任务）

- [ ] **Step 1: sidecar 全量测试**

Run: `cd sidecar && .venv/bin/python -m pytest -q`
Expected: 全绿（756 + Task 1-7 新增）

- [ ] **Step 2: 前端 + Rust 构建**

Run: `cd app && npx vue-tsc --noEmit && npx vite build && cargo check --manifest-path src-tauri/Cargo.toml && cargo test --manifest-path src-tauri/Cargo.toml`
Expected: 全 exit 0

- [ ] **Step 3: 真机验收清单（留人工，回写 spec §7）**

1. 开 `perception.recap`（distill 已开）→ 次日有提炼产物 → 开主窗 → 团子气泡出昨日简报（含「现象——建议」）→ 点气泡 → 跳回顾 mode 当天。
2. 同日再开窗 → 不重复推（去重）。
3. 昨日无产物（distill 关/no_data）→ 开窗静默无气泡。
4. `proactive.level=quiet` → 只落动态、不响；`full` → 气泡 + 语音。
5. 回顾 mode：按天卡片数字（app 时长）+ 活跃段 + 洞察/事件 + 状态徽章正确；未提炼天显示「未提炼」。
6. 同类 recap 👇 ≥2 → 后续反刍降 quiet（复用 dispatcher 降频）。
7. 跨重启：反刍标记持久（推过后重启再开窗不重推）。

- [ ] **Step 4: 验收回写 + 收尾 commit**

把真机验收结果回写到 spec `docs/superpowers/specs/2026-08-04-morning-recap-timeline-design.md` 末尾「实装记录」段。
```bash
git add docs/superpowers/specs/2026-08-04-morning-recap-timeline-design.md
git commit -m "docs(recap): 真机验收记录回写"
```

---

## 自审（plan vs spec 覆盖核对）

- spec §3 反刍数据流 → Task 6（选材/拼装）+ Task 7（recap_check 编排+emit+去重）+ Task 11（开窗触发）+ Task 12（气泡）✅
- spec §3 回顾数据流 → Task 2（recent_days）+ Task 9/10（IPC）+ Task 11（回顾视图）✅
- spec §4 存储（meta + runs.stats 迁移 + gather_summary 返回）→ Task 1 + Task 3 + Task 4 ✅
- spec §5 选材规则（insight≤3/event 兜底/pattern 不进）→ Task 6 ✅
- spec §2 建议深度边界（prompt 升级）→ Task 5 ✅
- spec §6 授权（perception.recap 默认关 + 三闸门 + 依赖 distill）→ Task 7（默认+闸门）+ Task 13（设置页）✅
- spec §8 Rust 桥接 → Task 10 ✅
- spec §9 server.py 拆分 → Task 8 ✅
- spec §10 测试 → Task 1-7 单测 + Task 14 构建/真机 ✅
- 无占位、类型一致（`recap_select`/`build_recap_text`/`recent_days`/`set_recap_day`/`recap_last_day`/`_recap_decide` 跨任务命名一致；`DistillDay` 前后端字段对齐 `day/status/stats/items`）✅
