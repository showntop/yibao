# server.py 后台循环提取 设计

日期：2026-08-04
状态：已评审（用户确认方案 A）
关联：`2026-08-04-morning-recap-timeline-design.md` §9 / 其 plan Task 8 deferred 项；T8 已把 9 个 helper + `_DOCK_MAX` 搬到 `background.py`，3 个 asyncio 循环因是闭包未搬。

## 1. 定位与目标

`serve_async`（server.py，~76KB）内嵌 3 个 asyncio 循环闭包，直接捕获 ~10 个局部变量，**无法单测**，且让 `serve_async` 持续臃肿。本重构把它们提到 `background.py` 成为**模块级函数**，并拆出**可单测的单 tick 体**——纯行为保持，772 测试兜底，补针对性回归测试。

**完成定义**：3 个循环 + 3 个 tick 在 background.py；`serve_async` 删闭包定义、改为 `asyncio.ensure_future(_xxx_loop(...))` 传参；新增 `test_background.py` 覆盖各 tick 的门控/清理/分发行为；全量 772 + 新增测试全绿；行为零差异。

## 2. 现状（3 个闭包）

| 循环 | 位置 | cadence | 捕获 | 行为 |
|---|---|---|---|---|
| `_perception_cleanup_loop` | server.py:713 | 3600s | `pstore`、`distiller`、`_offload` | purge `pstore` + `distiller.store`，各自 try/except 印 stderr |
| `_distiller_loop` | server.py:727 | 60s | `distiller`、`settings`、`_offload` | 闸门 `master AND distill` → `last_auto_run_day` → `auto_run_due` → `run_yesterday("auto")` |
| `_reminder_loop` | server.py:792 | 10s | `agent`(→reminder_store/history)、`settings`、`feed`、`voice`、`run_state`、`write_msg`、`proactive_dispatcher`、`_offload` | `pop_due` → 逐条 `_dispatch_reminder` |

`_offload`（来自 `loop.py`）、`_dispatch_reminder`（T8 已在 background.py）、`auto_run_due`（distiller.py）、`time`/`asyncio` 均可由 background.py **直接 import**，不必当捕获传。

## 3. 设计

### 3.1 提取形状（方案 A：扁平 keyword-only 参数 + tick 拆分）

每个循环拆成两部分：

- **单 tick 体**（可单测，无 `while`/`sleep`）：执行一遍该循环的实质逻辑。
- **循环壳**（trivial）：`while True: await asyncio.sleep(N); await _xxx_tick(...)`。

```python
# background.py（_offload/_dispatch_reminder 已在本模块可用；auto_run_due/time import）

async def _perception_cleanup_tick(pstore, distiller) -> None: ...
async def _distiller_tick(settings: dict, distiller) -> None: ...
async def _reminder_tick(*, store, agent, settings, feed, voice, run_state,
                         write_msg, dispatcher) -> None: ...

async def _perception_cleanup_loop(pstore, distiller) -> None:
    while True:
        await asyncio.sleep(3600)
        await _perception_cleanup_tick(pstore, distiller)

async def _distiller_loop(settings: dict, distiller) -> None:
    while True:
        await asyncio.sleep(60)
        await _distiller_tick(settings, distiller)

async def _reminder_loop(*, agent, settings, feed, voice, run_state,
                         write_msg, dispatcher) -> None:
    store = getattr(agent, "reminder_store", None)
    if store is None:
        return                      # 保留原"无提醒库则循环即退"语义（见 §3.3）
    while True:
        await asyncio.sleep(10)
        await _reminder_tick(store=store, agent=agent, settings=settings, feed=feed,
                             voice=voice, run_state=run_state, write_msg=write_msg,
                             dispatcher=dispatcher)
```

### 3.2 serve_async 改造

- 删除 3 个 `async def _xxx_loop` 闭包定义。
- import：`from .background import _perception_cleanup_loop, _distiller_loop, _reminder_loop`（补进 T8 已有的 background import 块）。
- 调度点改为传参：
  - `perception_cleanup_task = asyncio.ensure_future(_perception_cleanup_loop(pstore, distiller))`
  - `distiller_task = asyncio.ensure_future(_distiller_loop(settings, distiller))`
  - `reminder_task = asyncio.ensure_future(_reminder_loop(agent=agent, settings=settings, feed=feed, voice=voice, run_state=run_state, write_msg=write_msg, dispatcher=proactive_dispatcher))`

### 3.3 行为不变量（必须逐条保持）

- **cadence 不变**：3600s / 60s / 10s。
- **distiller 闸门不变**：`distiller is None or not (master AND distill)` → 跳过（零出站）。
- **perception cleanup 容错不变**：`pstore`/`distiller` 各自 try/except，互不传染，印 stderr。
- **reminder 早退语义不变**：原 `_reminder_loop` 在进 `while` 前 `store = getattr(agent,"reminder_store",None); if store is None: return`——**循环任务即结束**。本设计把这段保留在 `_reminder_loop`（壳）内、`while` 之前；tick 收到的 `store` 必非 None。（不把该判断挪进 tick 每次重判——那样会变成"无库时循环永远空转睡眠"，属行为变更。）
- **reminder 分发不变**：`_dispatch_reminder(r, settings=, feed=, history=agent.history, voice=, run_state=, write_msg=, dispatcher=)` 逐条调；`pop_due` 失败只 print + `continue`（tick 内为 `return`，等价——单 tick 一轮）。
- **日志文案不变**。

## 4. 测试

新增 `sidecar/tests/test_background.py`，**只测 tick**（单轮、无 sleep、fake 注入）：

- `_perception_cleanup_tick`：`pstore` 与 `distiller.store` 各被 purge 一次；一方抛异常不阻断另一方；两者皆 None 不报错。
- `_distiller_tick`：`distiller is None` 或闸门关 → 不调 `run_yesterday`（零出站）；闸门开 + `auto_run_due` True → 调一次 `run_yesterday("auto")`；`auto_run_due` False → 不调；`last_auto_run_day`/`run_yesterday` 异常只 print 不抛。
- `_reminder_tick`：`pop_due` 返多条 → 逐条经 `_dispatch_reminder`（用 fake 验被调次数与参数）；`pop_due` 抛异常 → 不分发、不抛。

fake：可调用的 `pstore.purge`/`distiller.store.purge`/`distiller.store.last_auto_run_day`/`distiller.run_yesterday`/`store.pop_due`；fake `dispatcher`/`write_msg`/`voice` 等。`_offload` 走真实（`asyncio.run_in_executor` 包装同步 fake）或注入。

**循环壳不单测**（`while True`+sleep，trivial）——靠 tick 覆盖 + 全量回归兜底。

## 5. 纪律与风险

- **纯行为保持**：只做"闭包→模块函数 + 参数化 + tick 拆分"。任何逻辑/cadence/文案/异常路径变更另开 commit。
- **逐个迁移、每搬一个跑全量**：每提取一个循环（tick+loop+改 serve_async 调度点）→ 跑 `pytest -q` 必须 772 绿 → 再搬下一个。
- **回归网**：772 现有测试 + 新增 test_background.py。server.py 是最关键文件，改动以测试全绿为唯一判据。
- 不动：IPC dispatch、`build_loop`、`_recap_decide`、voice/tts 泵、其余 background.py 已搬 helper。

## 6. 明确不做（本轮）

- 不重构循环**内部**逻辑（仅搬位置 + 拆 tick）。
- 不调整 cadence 或闸门语义。
- 不把 `_offload`/`_dispatch_reminder`/`auto_run_due` 再搬迁（已在可用位置）。
- 不碰 `_tick`/`_watch_cancel`/`_pump_tts` 等其它 serve_async 内闭包（与本次目标无关）。
