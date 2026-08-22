# R-13 第二步交接文档：serve_async 闭包域拆 runtime/ 模块

> 写于 2026-08-22 重构会话结束。本文档自足——新会话读完即可开工，无需重新侦察。
> 验证基线：sidecar `uv run pytest` **1142 全绿**；仓库工作区干净（HEAD = R-13 第一步查表化提交）。

## 1. 现状

- `sidecar/src/yibao_brain/server.py` 共 1742 行；`serve_async` 占 315–1675（**1361 行，70 个内部闭包/函数**）。
- **第一步已完成（已提交）**：msg 分发 elif 巨链（原 374 行/31 分支）已查表化——`_handlers: dict[str, object]` + 31 个命名 handler `async def _h_<rtype>(msg) -> None`（定义在主循环 `while True:` 之前，约 1186–1620 行区域），主循环收敛为 4 行查表调用（约 1667–1675 行区域）。
- stdio 读写/编解码已在 `transport.py`（serve_async 之外），是拆分的先例参照。
- 模块级辅助已在位：`build_loop`（124 行起）/`_load_plugins_safe`/`_run_and_emit`/`serve`（同步版）。

## 2. serve_async 闭包域地图（按行序，2026-08-22 快照）

| 行号 | 域 | 内容 |
|---|---|---|
| 336–348 | 事件总线 | `EventTap(write_msg)` 重绑 write_msg（tap：stdio 透传 + SSE 广播）；`_tick` 存活刻度 + `tick_task` |
| 355–358 | 确认三表 | `pending_confirms` / `early_answers` / `confirm_meta` / `_confirm_done`（deque） |
| ~360–390 | 槽位系统 | `run_slots: dict`（per-会话）+ `_slot(conversation_id)` 闭包（含内部 `done()`） |
| 394 | voice | `tts_lock = asyncio.Lock()` |
| ~400–495 | 组件装配 | `load_settings`/`FeedStore`/`ProactiveDispatcher`/`agent = build_loop(...)`/`JobsStore`/`invoke_ctx` |
| 498–604 | **工具域**（建议先拆） | `_running_tasks`(498) / `_feed_stats`(590) |
| 605–680 | **工具域续** | `_collect_widgets`(605) |
| 681–730 | 感知域 | `pstore`/`distiller`/`perception_stop`/`perception_thread` + `_screen_sampler`(681)/`_vision_summarizer`/`_secure_input_checker` + cleanup/distiller 任务 |
| 765–798 | **工具域续** | `_mem_list`(765) |
| 799–818 | 读线程 | `_reader`(799)：ping/pong 看门狗 + `call_soon_threadsafe` 入队 |
| 827–838 | **voice 域** | `_pump_tts`(827) |
| 840–891 | run 流 | `_stream_agent`(840) |
| 892–894 | run 流 | `_drive_run`(892) |
| 895–1001 | **voice 域** | `_drive_voice_start`(895)（含 `_vev`/`_done`/`_watch_cancel`/`_bye` 内嵌） |
| 1002–1078 | **runs 调度域** | `_preempt_current`/`_preempt_if_same_surface`/`_schedule_run`(1002)（含 `_marked`） |
| 1079–1115 | **mobile 域** | `_mobile_state`(1079)/`_mobile_feed`/`_register_push`/`_interrupt_mobile`/`_confirm_mobile`/`_submit_run` |
| 1116–1156 | **runs 调度域** | `_chain_start`(1116)（槽位链式启停，依赖最深） |
| 1157–1185 | **mobile/HTTP 装配** | `_http_deps = MobileDeps()` + `save/submit_run/interrupt/confirm/state/...` 绑定 + `_start_http_api` 调用 |
| 1186–1620 | **31 个 handler**（第一步产物） | `_h_run`…`_h_prompt_permission`，已命名独立 |
| 1627–1675 | 主循环 | shutdown 收尾（1627–1662）+ 4 行查表分发 |

## 3. 建议拆分序（每步独立提交 + `uv run pytest` 全绿后进下一步）

1. **工具域** → `runtime/helpers.py`：`_running_tasks`/`_feed_stats`/`_collect_widgets`/`_mem_list`。共享状态最少（agent/feed/settings 经参数注入），用来验证拆分模式与 RuntimeCtx 形态。
2. **mobile/HTTP 域** → `runtime/mobile.py`：`_mobile_state`…`_http_deps` 装配整体迁出（`_http_deps` 的绑定面在 1157–1185，是一段连续装配代码）。
3. **voice 域** → `runtime/voice.py`：`tts_lock`/`_pump_tts`/`_drive_voice_start`。
4. **runs 调度域** → `runtime/runs.py`：`_slot`/`_preempt_*`/`_schedule_run`/`_chain_start`——最后拆（`_chain_start` 被 mobile 域与 `_h_panel_action` 双向引用，依赖最深）。

共享状态（`run_slots`/`pending_confirms`/`tap`/`agent`/`feed`/`settings`…）收进 `RuntimeCtx`（简单类实例，serve_async 开头构造，各域函数以 `ctx.` 访问）。handler 层（31 个 `_h_*`）**可暂留 serve_async**——它们是薄分发层，迁不迁收益小。

## 4. 陷阱清单（本会话实测踩过或核实的）

1. **`_run_ctx` 是 `contextvars.ContextVar`**（431 读 / 1014、1255 写）——跨 await 传播任务上下文（cancel/surface/conversation_id）。**迁移时不能改成 RuntimeCtx 实例属性**，否则丢任务级隔离（并发 run 会互相污染）。它保持模块级/传入原对象。
2. **write_msg 在 serve_async 内被重绑为 tap**（338 行）——所有闭包引用的是重绑后的 EventTap 实例；拆出域函数时经 ctx 传 tap，别传原始 write_msg。
3. **测试直调 serve_async 并 monkeypatch 模块级函数**（如 `_run_and_emit`）：函数迁去 `runtime/` 后，测试 patch 路径（`server._run_and_emit`）会失效——参照 coding.py 的 `_c()` 双模块名兼容先例，或在 server.py 留 re-export + 域内经 `getattr(server_mod, ...)` 动态解析。
4. **`_h_run` 注册了两键**：`_handlers["run"]` 与 `_handlers["voice_start"]` 指向同一函数（原复合分支）。
5. **server.py 是 CRLF 行尾**——批量编辑脚本用二进制读写或 newline="" 保持。
6. `_reader` 是 daemon 线程（不是 asyncio task），经 `call_soon_threadsafe` 入队——迁移时保持线程边界。
7. serve()（同步版，292 行）与 serve_async 并存——只拆 serve_async，别动 serve()。

## 5. 验证口径

- 每步：`cd sidecar && uv run pytest -q` **1142 全绿**（数量不得下降）；
- 行为等价判据：现有测试零修改通过（允许改 import/patch 路径，但不允许改断言）；
- 全部完成后更新 `docs/plan/2026-08-22-refactor-plan.md` 执行状态表 R-13 行 → ✅，并删除本交接文档。
