# 并发对话（解除单 run 限制）spec

日期：2026-08-20 | 依据：roadmap `docs/plan/2026-08-20-roadmap-r5.md` B2 插队项 | 侦察报告见当日会话（要点已并入本文）

## 背景

当前一次只处理一个 run：全局单槽 `run_state`（`server.py:708-709`），同 surface 新话抢占旧话、跨 surface 链式排队。多会话时代（壳端 SessionList + pet 固定会话 + 手机端）排队体验扎眼——Home 窗一句话要等 pet 窗的长 run 说完。本 spec 把槽位从「全局一个」改为「per-会话各一个」：**同会话内保留新话顶旧话，跨会话真并行**。

## 核心模型

槽位键 = `conversation_id`（壳/手机发起方必带，现状已贯通）。每会话一个槽：`{task, cancel, preempt_gen, surface}`。

- 同会话新 run → 抢占（`_preempt_current` 语义不变，`interrupted` 照旧）
- 跨会话新 run → 直接并行启动，不再排队、不再发「另一个窗口还在说」notice
- 同会话跨 surface（同一 conversation_id 从 Home 和 pet 同时发话）→ 视为同会话，抢占

## 关键设计决策

### A. 槽位表（server.py）

- `run_state` → `run_slots: dict[str, slot]`；`_schedule_run`/`_chain_start`/`_preempt_if_same_surface`（:1246-1407）按 conversation_id 取槽，无槽即建。
- `_chain_start` 的「等上一任务收尾」只在同槽内生效；跨槽零等待。`_PREEMPT_GRACE_S` 强 cancel 自愈逻辑保留在槽内。
- 无 conversation_id 的调用方（遗留 `serve()` 同步路径、提醒触发的内部 run）归 default 槽，行为同现状。
- v1 **不设并行上限**（自用场景，LLM 端天然限流），列入非目标。

### B. 确认框 per-run 化（风险最高处）

- `batch_confirmer`（`server.py:720-775`）的 `cancel = run_state["cancel"]`（:754）改为读**本 run 自己槽位的 cancel**——否则 A 会话打断误取消 B 会话的确认等待。
- `confirm_meta` 条目补 `conversation_id`/`surface` 归属；`/v1/state` 的 pending（`_mobile_state`）与壳端 ConfirmDialog 按归属过滤展示。确认 future 多槽机制（:699-702）注释已声明为多 run 设计，不动。
- `gate.session_allowed`（remember 免确认）保持进程级共享：会话 A 记住的「不再询问」对 B 生效——**安全语义写明**：remember 是对「动作」的信任，不是对会话的信任。

### C. history 并发保护（history.py）

分桶已按 conversation_id（`history.py:52-56`），但 `record_messages`→`_save`（:112-141）整桶读改写 + 整本覆盖写，无锁。加一把模块级 `threading.Lock` 包住读改写全程（run 在 asyncio loop 内同步调用，锁粒度=单次落盘，够用）。不引入 per-桶锁——整本覆盖写下无意义。

### D. TTS 全局播报锁（voice.py / server.py）

物理声道只有一条，TTS **保持全局串行，不 per-会话化**：

- 加全局「播报锁」（threading.Lock 或 asyncio 等价物），`_pump_tts`/`_stream_agent` 播报前抢锁。
- 抢不到 → 该 run **静默不播**（文字流式照出），发 `notice` 事件「正在播报另一段对话，这段不念了」。不排队——排队念旧话比不念更怪。
- 打断三连取消（停 TTS + 终止 LLM + 清队列）只作用本槽；**全局播放器停止仅限持锁者**（A 的打断不能掐掉 B 正在播的音——B 持锁时 A 的打断只停自己的 LLM 流）。

### E. interrupt 定向化

- `interrupt` 消息接受可选 `conversation_id`：带 → 只打断该槽；不带 → 全停（旧行为，兼容）。
- 壳端 interrupt 命令（`lib.rs`）带当前会话 id；手机 `_interrupt_mobile`（:1297-1308）维持 surface 域限定，叠加 conversation_id。
- `run_done` 事件补 `conversation_id`（现只回 rid 且桌面 rid 恒 0，`lib.rs:1017`）；壳侧 `brain-run-done` 监听与手机端 `chat.ts` 改按 conversation_id 过滤（修 `chat.ts:41-48` 已注释的串台风险）。

### F. idle 判定与清场

- proactive 播报前置检查（`proactive.py:78-86`）、提醒 TTS（`background.py:58-60`）的 `run_state["task"]` idle 判定 → 「所有槽均空闲」。
- stdin 关闭清场（`server.py:1446-1454`）遍历所有槽等收尾。

### G. 保持全局、仅写明语义的（不改代码）

- `_FOCUS` 面板焦点（:59）、`invoke_ctx`（:800）：全局单值，并发会话上下文可能串味——已知限制，v1 不动（自用场景同时盯两个面板概率低）。
- proactive/reminder 事件固定 `surface="pet"` + 落 default 历史桶：归属规则待「提醒归属会话」独立迭代，本 spec 不定。

## 非目标

- 并行 run 数上限 / 资源配额
- `_FOCUS`/`invoke_ctx` per-会话化
- 提醒/proactive 事件的会话归属规则
- LLM 端并发限流与计费合并（pricing.py 现状已按调用计，天然兼容）

## 测试改写与新增

- `test_server.py:197-269` 跨 surface 排队用例 → 改断言并行（两槽同时 running、无 notice）；同 surface 同会话抢占用例（:160、:240-269）保留；新增同会话跨 surface 抢占。
- 新增：A 打断不取消 B 的确认等待；A 打断不掐 B 的播报（B 持锁）；播报锁占用时后到 run 静默 + notice；history 并发写不丢数据（双线程灌同一文件断言完整）；run_done 带 conversation_id。
- `test_loop.py`/`test_voice.py` 打断语义用例按槽位化同步。
- 手机端：`chat.test.ts` 补 conversation_id 过滤用例。

## 验收标准

A. Home 窗与 pet 窗（不同 conversation_id）同时发话，两个 run 真并行，互不等待、互不掐断
B. 同会话内新话顶旧话语义不变（`interrupted` 事件照旧）
C. A 会话打断不影响 B 会话的确认框与正在播的 TTS
D. 两个会话并发写历史后重启，两边历史都完整
E. sidecar pytest 全绿（含改写用例）；app/mobile 前端测试全绿
