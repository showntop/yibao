# server.py 后台循环提取 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `serve_async` 内 3 个 asyncio 闭包循环提取到 `background.py` 为模块级函数 + 可单测的单 tick 体，纯行为保持。

**Architecture:** 每个循环拆成 `_xxx_tick`（单轮实质逻辑，可单测）+ `_xxx_loop`（`while True: sleep; tick` 的 trivial 壳）。扁平 keyword-only 参数，mirror 现有 `_dispatch_reminder`。`_offload`/`_dispatch_reminder`/`auto_run_due` 在 background.py 直接 import 不当捕获传。`serve_async` 删闭包、调度点改 `asyncio.ensure_future(_xxx_loop(...))` 传参；现有 `.cancel()` 收尾（server.py:1039-1040）不动。

**Tech Stack:** Python 3.12，pytest（`asyncio.run` 跑 async，无 pytest-asyncio），SQLite。

## Global Constraints

- **纯行为保持**：只做"闭包→模块函数 + 参数化 + 拆 tick"。cadence（3600/60/10s）、闸门、容错、reminder 无库即退早返、日志文案——逐条不变。任何逻辑变更另开 commit。
- **逐个迁移**：每提取一个循环 → 跑全量 `pytest -q` 必须 772 绿 → 再搬下一个。中间态（部分闭包+部分模块函数）合法。
- **TDD**：每个 tick 先写失败测试（tick 不存在）→ 实现 → 通过。循环壳不单测（`while True`+sleep，trivial，靠 tick + 全量回归兜底）。
- **测试约定**：`sidecar/tests/test_background.py`（新建）；顶部 `sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))`；async 用 `asyncio.run(...)` 包在 sync `def test_*` 里；fake 用法对照 `tests/test_proactive.py`（`asyncio.run(run())` 模式）。
- **sidecar 测试命令**：`cd /Users/denny/Work/yibao/sidecar && .venv/bin/python -m pytest tests/test_background.py -v`；全量 `.venv/bin/python -m pytest -q`（基线 772，每任务后递增）。
- **不动的收尾逻辑**：server.py:1038-1040 的 `tick_task.cancel()`/`reminder_task.cancel()`/`perception_cleanup_task.cancel()` 保持原样——提取只改调度点的 callable 与参数，task 变量仍由 `asyncio.ensure_future(...)` 赋值，cancel 照常生效。（`distiller_task` 未被 cancel 是既有行为，不改。）
- **commit**：每任务一个 commit，中文 + scope（`refactor(server): ...`），仅 stage 本任务文件，不动无关 `.gitignore`。提交到 `main`（项目惯例）。

## File Structure

- `sidecar/src/yibao_brain/background.py`（T8 已存在）：新增 6 个函数（3 tick + 3 loop）+ `from .distiller import auto_run_due`。
- `sidecar/src/yibao_brain/server.py`：删 3 个闭包定义（713-725 / 727-738 / 792-808）；调度点 740/741/810 改传参；`from .background import (...)` 块追加 3 个 loop 名。
- `sidecar/tests/test_background.py`（新建）：3 组 tick 测试（fake 注入）。

---

### Task 1: 提取 _perception_cleanup（tick + loop）

**Files:**
- Modify: `sidecar/src/yibao_brain/background.py`（加 `_perception_cleanup_tick` + `_perception_cleanup_loop`）
- Modify: `sidecar/src/yibao_brain/server.py:713-725`（删闭包）、`:740`（调度点传参）、background import 块（追加 `_perception_cleanup_loop`）
- Test: `sidecar/tests/test_background.py`（新建）

**Interfaces:**
- Consumes: background.py 已有的 `from .loop import _offload`；`asyncio`/`sys`/`time` 已 import。
- Produces: `async def _perception_cleanup_tick(pstore, distiller) -> None`；`async def _perception_cleanup_loop(pstore, distiller) -> None`。

- [ ] **Step 1: 写失败测试**（新建 `sidecar/tests/test_background.py`）

```python
"""background.py 后台循环的单 tick 体测试（循环壳不测）。"""
from __future__ import annotations
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from yibao_brain.background import _perception_cleanup_tick  # noqa: E402


class _Pstore:
    def __init__(self) -> None:
        self.purged = 0

    def purge(self) -> int:  # _offload 在 executor 线程调同步方法
        self.purged += 1
        return 1


class _Distiller:
    def __init__(self) -> None:
        self.store = self._Store()

    class _Store:
        def __init__(self) -> None:
            self.purged = 0

        def purge(self) -> int:
            self.purged += 1
            return 1


def test_perception_cleanup_tick_purges_both():
    p, d = _Pstore(), _Distiller()
    asyncio.run(_perception_cleanup_tick(p, d))
    assert p.purged == 1 and d.store.purged == 1


def test_perception_cleanup_tick_one_failure_does_not_block_other():
    p, d = _Pstore(), _Distiller()

    def boom() -> None:
        raise RuntimeError("x")

    p.purge = boom  # type: ignore[method-assign]
    asyncio.run(_perception_cleanup_tick(p, d))  # p 抛，d 仍清
    assert d.store.purged == 1


def test_perception_cleanup_tick_none_safe():
    asyncio.run(_perception_cleanup_tick(None, None))  # 两皆 None 不抛
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd /Users/denny/Work/yibao/sidecar && .venv/bin/python -m pytest tests/test_background.py -v`
Expected: FAIL — `ImportError: cannot import name '_perception_cleanup_tick'`

- [ ] **Step 3: 实现 tick + loop（background.py）**

在 background.py 末尾追加：
```python
async def _perception_cleanup_tick(pstore, distiller) -> None:
    """单轮感知过期清理：purge pstore 与 distiller.store，各自 try/except 互不传染。"""
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


async def _perception_cleanup_loop(pstore, distiller) -> None:
    while True:
        await asyncio.sleep(3600)
        await _perception_cleanup_tick(pstore, distiller)
```

- [ ] **Step 4: 跑 tick 测试验证通过**

Run: `cd /Users/denny/Work/yibao/sidecar && .venv/bin/python -m pytest tests/test_background.py -v`
Expected: 3 passed

- [ ] **Step 5: 接线 serve_async**

(a) 删除 server.py 内 `async def _perception_cleanup_loop() -> None:` 整个闭包（含其 `while True`/两个 try/except，到该函数结束，约 713-725）。
(b) server.py 的 `from .background import (...)` 块里追加 `_perception_cleanup_loop,`。
(c) 调度点（原 740）改为：
```python
    perception_cleanup_task = asyncio.ensure_future(_perception_cleanup_loop(pstore, distiller))
```

- [ ] **Step 6: 全量回归**

Run: `cd /Users/denny/Work/yibao/sidecar && .venv/bin/python -m pytest -q`
Expected: 775 passed（772 + 3）

- [ ] **Step 7: commit**
```bash
git add sidecar/src/yibao_brain/background.py sidecar/src/yibao_brain/server.py sidecar/tests/test_background.py
git commit -m "refactor(server): 提取 _perception_cleanup 循环到 background（tick+loop）"
```

---

### Task 2: 提取 _distiller（tick + loop）

**Files:**
- Modify: `sidecar/src/yibao_brain/background.py`（加 `_distiller_tick` + `_distiller_loop`；顶部加 `from .distiller import auto_run_due`）
- Modify: `sidecar/src/yibao_brain/server.py:727-738`（删闭包）、`:741`（调度点传参）、background import 块（追加 `_distiller_loop`）
- Test: `sidecar/tests/test_background.py`（追加）

**Interfaces:**
- Consumes: background.py 新增 `from .distiller import auto_run_due`；`time` 已 import；`_offload` 已 import。
- Produces: `async def _distiller_tick(settings: dict, distiller) -> None`；`async def _distiller_loop(settings: dict, distiller) -> None`。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_background.py`）

```python
from yibao_brain import background  # noqa: E402
from yibao_brain.background import _distiller_tick  # noqa: E402


def _fake_distiller(last_day=None):
    class D:
        def __init__(self) -> None:
            self.ran: list[str] = []
            self.store = type("S", (), {"last_auto_run_day": lambda self: last_day})()

        def run_yesterday(self, source: str) -> dict:
            self.ran.append(source)
            return {"status": "ok"}

    return D()


def test_distiller_tick_gate_off_no_run(monkeypatch):
    monkeypatch.setattr(background, "auto_run_due", lambda *a: True)  # 即便到期
    d = _fake_distiller()
    asyncio.run(_distiller_tick({"perception.master": False, "perception.distill": True}, d))
    assert d.ran == []
    asyncio.run(_distiller_tick({"perception.master": True, "perception.distill": False}, d))
    assert d.ran == []


def test_distiller_tick_runs_when_due(monkeypatch):
    monkeypatch.setattr(background, "auto_run_due", lambda *a: True)
    d = _fake_distiller(last_day="2026-01-01")
    asyncio.run(_distiller_tick({"perception.master": True, "perception.distill": True}, d))
    assert d.ran == ["auto"]


def test_distiller_tick_skips_when_not_due(monkeypatch):
    monkeypatch.setattr(background, "auto_run_due", lambda *a: False)
    d = _fake_distiller()
    asyncio.run(_distiller_tick({"perception.master": True, "perception.distill": True}, d))
    assert d.ran == []


def test_distiller_tick_distiller_none_safe():
    asyncio.run(_distiller_tick({"perception.master": True, "perception.distill": True}, None))


def test_distiller_tick_store_failure_no_raise(monkeypatch):
    monkeypatch.setattr(background, "auto_run_due", lambda *a: True)

    class Boom:
        ran: list[str] = []

        class store:
            @staticmethod
            def last_auto_run_day():
                raise RuntimeError("db")

        def run_yesterday(self, src: str) -> None:
            self.ran.append(src)

    b = Boom()
    asyncio.run(_distiller_tick({"perception.master": True, "perception.distill": True}, b))
    assert b.ran == []
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd /Users/denny/Work/yibao/sidecar && .venv/bin/python -m pytest tests/test_background.py -v -k distiller_tick`
Expected: FAIL — `ImportError: cannot import name '_distiller_tick'`

- [ ] **Step 3: 实现 tick + loop（background.py）**

(a) background.py 顶部 import 区加：
```python
from .distiller import auto_run_due
```
（确认 `time` 已 import；若未 import 则补 `import time`。）

(b) 追加函数：
```python
async def _distiller_tick(settings: dict, distiller) -> None:
    """单轮自动提炼判定：闸门(master AND distill) → 到期则 run_yesterday("auto")。"""
    if distiller is None or not (settings.get("perception.master") and settings.get("perception.distill")):
        return
    try:
        last = await _offload(distiller.store.last_auto_run_day)
        if auto_run_due(time.time(), last):
            await _offload(distiller.run_yesterday, "auto")
    except Exception as e:
        print(f"[yibao] 自动提炼失败：{e}", file=sys.stderr)


async def _distiller_loop(settings: dict, distiller) -> None:
    """每日 04:17 自动提炼昨日；master 或 perception.distill 关闭时零出站。"""
    while True:
        await asyncio.sleep(60)
        await _distiller_tick(settings, distiller)
```

- [ ] **Step 4: 跑 tick 测试验证通过**

Run: `cd /Users/denny/Work/yibao/sidecar && .venv/bin/python -m pytest tests/test_background.py -v`
Expected: 8 passed（3 + 5）

- [ ] **Step 5: 接线 serve_async**

(a) 删除 server.py 内 `async def _distiller_loop() -> None:` 整个闭包（约 727-738）。
(b) `from .background import (...)` 块追加 `_distiller_loop,`。
(c) 调度点（原 741）改为：
```python
    distiller_task = asyncio.ensure_future(_distiller_loop(settings, distiller))
```

- [ ] **Step 6: 全量回归**

Run: `cd /Users/denny/Work/yibao/sidecar && .venv/bin/python -m pytest -q`
Expected: 780 passed（775 + 5）

- [ ] **Step 7: commit**
```bash
git add sidecar/src/yibao_brain/background.py sidecar/src/yibao_brain/server.py sidecar/tests/test_background.py
git commit -m "refactor(server): 提取 _distiller 循环到 background（tick+loop）"
```

---

### Task 3: 提取 _reminder（tick + loop）

**Files:**
- Modify: `sidecar/src/yibao_brain/background.py`（加 `_reminder_tick` + `_reminder_loop`）
- Modify: `sidecar/src/yibao_brain/server.py:792-808`（删闭包）、`:810`（调度点传参）、background import 块（追加 `_reminder_loop`）
- Test: `sidecar/tests/test_background.py`（追加）

**Interfaces:**
- Consumes: background.py 已有 `_dispatch_reminder`（`async def _dispatch_reminder(r, *, settings, feed, history, voice, run_state, write_msg, dispatcher=None)`）、`_offload`、`time`。
- Produces: `async def _reminder_tick(*, store, agent, settings, feed, voice, run_state, write_msg, dispatcher) -> None`；`async def _reminder_loop(*, agent, settings, feed, voice, run_state, write_msg, dispatcher) -> None`。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_background.py`）

```python
from yibao_brain.background import _reminder_tick  # noqa: E402


def _run_reminder_tick(store, due_or_exc):
    """构造 store（pop_due 返 due_or_exc 或抛），跑一次 tick，返回 dispatch 调用 id 列表。"""
    calls: list = []

    class Store:
        def pop_due(self, now):
            if isinstance(due_or_exc, BaseException):
                raise due_or_exc
            return due_or_exc

    async def fake_dispatch(r, **kw):
        calls.append(r.get("id"))

    # 直接替换 background 命名空间里的 _dispatch_reminder
    import yibao_brain.background as bg
    orig = bg._dispatch_reminder
    bg._dispatch_reminder = fake_dispatch  # type: ignore[assignment]
    try:
        agent = type("A", (), {"history": None})()
        asyncio.run(_reminder_tick(store=Store(), agent=agent, settings={"proactive.level": "quiet"},
                                   feed=None, voice=None, run_state={}, write_msg=lambda m: None,
                                   dispatcher=None))
    finally:
        bg._dispatch_reminder = orig  # type: ignore[assignment]
    return calls


def test_reminder_tick_dispatches_each_due():
    calls = _run_reminder_tick(None, [{"id": 1, "text": "a"}, {"id": 2, "text": "b"}])
    assert calls == [1, 2]


def test_reminder_tick_pop_failure_no_dispatch():
    calls = _run_reminder_tick(None, RuntimeError("db"))
    assert calls == []
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd /Users/denny/Work/yibao/sidecar && .venv/bin/python -m pytest tests/test_background.py -v -k reminder_tick`
Expected: FAIL — `ImportError: cannot import name '_reminder_tick'`

- [ ] **Step 3: 实现 tick + loop（background.py）**

追加：
```python
async def _reminder_tick(*, store, agent, settings, feed, voice, run_state,
                         write_msg, dispatcher) -> None:
    """单轮到期提醒扫描：pop_due → 逐条 _dispatch_reminder。pop_due 失败只 print 不抛。"""
    try:
        due = await _offload(store.pop_due, time.time())
    except Exception as e:
        print(f"[yibao] 提醒扫描失败：{e}", file=sys.stderr)
        return
    for r in due:
        print(f"[yibao] 提醒触发 id={r.get('id')}：{str(r.get('text', ''))[:30]!r}", file=sys.stderr)
        await _dispatch_reminder(r, settings=settings, feed=feed, history=agent.history,
                                 voice=voice, run_state=run_state, write_msg=write_msg,
                                 dispatcher=dispatcher)


async def _reminder_loop(*, agent, settings, feed, voice, run_state, write_msg, dispatcher) -> None:
    """主动能力：每 10s 扫到期提醒 → 按自主权档位分发。无 reminder_store 则循环即退（保留原语义）。"""
    store = getattr(agent, "reminder_store", None)
    if store is None:
        return
    while True:
        await asyncio.sleep(10)
        await _reminder_tick(store=store, agent=agent, settings=settings, feed=feed,
                             voice=voice, run_state=run_state, write_msg=write_msg,
                             dispatcher=dispatcher)
```

- [ ] **Step 4: 跑 tick 测试验证通过**

Run: `cd /Users/denny/Work/yibao/sidecar && .venv/bin/python -m pytest tests/test_background.py -v`
Expected: 10 passed（8 + 2）

- [ ] **Step 5: 接线 serve_async**

(a) 删除 server.py 内 `async def _reminder_loop() -> None:` 整个闭包（约 792-808）。
(b) `from .background import (...)` 块追加 `_reminder_loop,`。
(c) 调度点（原 810）改为：
```python
    reminder_task = asyncio.ensure_future(_reminder_loop(
        agent=agent, settings=settings, feed=feed, voice=voice,
        run_state=run_state, write_msg=write_msg, dispatcher=proactive_dispatcher))
```

- [ ] **Step 6: 全量回归**

Run: `cd /Users/denny/Work/yibao/sidecar && .venv/bin/python -m pytest -q`
Expected: 782 passed（780 + 2）

- [ ] **Step 7: commit**
```bash
git add sidecar/src/yibao_brain/background.py sidecar/src/yibao_brain/server.py sidecar/tests/test_background.py
git commit -m "refactor(server): 提取 _reminder 循环到 background（tick+loop）"
```

---

### Task 4: 全量验收 + 行为保持核对

**Files:** 无（验证任务）

- [ ] **Step 1: 全量测试**

Run: `cd /Users/denny/Work/yibao/sidecar && .venv/bin/python -m pytest -q`
Expected: 782 passed（772 基线 + 10 新 test_background.py）

- [ ] **Step 2: 闭包已清零（grep 核对）**

Run: `cd /Users/denny/Work/yibao/sidecar && grep -n "async def _perception_cleanup_loop\|async def _distiller_loop\|async def _reminder_loop" src/yibao_brain/server.py`
Expected: 无输出（3 个闭包定义都已从 server.py 删除）。

Run: `cd /Users/denny/Work/yibao/sidecar && grep -n "async def _perception_cleanup_loop\|async def _distiller_loop\|async def _reminder_loop\|async def _perception_cleanup_tick\|async def _distiller_tick\|async def _reminder_tick" src/yibao_brain/background.py`
Expected: 6 行（3 tick + 3 loop 都在 background.py）。

- [ ] **Step 3: 行为不变量核对（grep 文案/cadence 仍在）**

Run: `cd /Users/denny/Work/yibao/sidecar && grep -n "asyncio.sleep(3600)\|asyncio.sleep(60)\|asyncio.sleep(10)" src/yibao_brain/background.py`
Expected: 三行各一（cadence 3600/60/10 保持）。

Run: `cd /Users/denny/Work/yibao/sidecar && grep -n "感知过期清理失败\|提炼原料清理失败\|自动提炼失败\|提醒扫描失败\|提醒触发" src/yibao_brain/background.py`
Expected: 5 条日志文案都在（与原 server.py 逐字一致）。

Run: `cd /Users/denny/Work/yibao/sidecar && grep -n "reminder_task.cancel\|perception_cleanup_task.cancel" src/yibao_brain/server.py`
Expected: 2 行（收尾 cancel 调用未动，task 变量仍由 ensure_future 赋值）。

- [ ] **Step 4: serve_async 不再捕获式定义（冒烟）**

Run: `cd /Users/denny/Work/yibao/sidecar && .venv/bin/python -c "from yibao_brain.server import serve_async; from yibao_brain import background; print('ok', all(hasattr(background, n) for n in ['_perception_cleanup_loop','_distiller_loop','_reminder_loop','_perception_cleanup_tick','_distiller_tick','_reminder_tick']))"`
Expected: `ok True`

- [ ] **Step 5: 真机/集成本轮不要求**

3 循环经全量 772 + 10 新单测覆盖；调度点改动由回归网兜底。真机行为（提醒触发、04:17 提炼、每小时清理）随日常使用观察，无需专项脚本。

- [ ] **Step 6: 收尾**

无需额外 commit（前三任务已提交）。若 Step 1-4 全过，本重构完成。把结果回写 spec `docs/superpowers/specs/2026-08-04-extract-background-loops-design.md` 末尾「实装记录」段并 commit：
```bash
git add docs/superpowers/specs/2026-08-04-extract-background-loops-design.md
git commit -m "docs(refactor): 后台循环提取验收记录回写"
```

---

## 自审（plan vs spec 覆盖核对）

- spec §3.1 提取形状（tick + loop，扁平 kw-only）→ Task 1/2/3 各实现 ✅
- spec §3.2 serve_async 改造（删闭包 / import / 调度点传参）→ Task 1/2/3 Step 5 ✅
- spec §3.3 行为不变量：cadence 3600/60/10（Task1/2/3 loop）、distiller 闸门（Task2 tick）、cleanup 容错（Task1 tick）、reminder 无库即退早返保留在 loop（Task3 loop）、文案（Task4 Step 3 grep 核对）✅
- spec §4 测试（test_background.py 测各 tick，循环壳不测）→ Task 1/2/3 Step1/2/4 ✅
- spec §5 纪律（纯搬运、逐个迁移每步 772 绿）→ 每 Task Step 6 全量回归 ✅
- 类型/命名一致：`_perception_cleanup_tick/_loop`、`_distiller_tick/_loop`、`_reminder_tick/_loop` 跨任务命名一致；`_reminder_tick` 的 kw（store/agent/settings/feed/voice/run_state/write_msg/dispatcher）与 `_reminder_loop` 调用、`_dispatch_reminder` 形参对齐 ✅
- 无占位 ✅
