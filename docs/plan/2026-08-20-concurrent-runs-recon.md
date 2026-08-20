# 并发对话 · 实现前侦察报告（2026-08-20）

> 配套 spec：`docs/superpowers/specs/2026-08-20-concurrent-runs-design.md`

## 1. 单 run 限制的实现

核心在 `serve_async()`（`sidecar/src/yibao_brain/server.py:660`）。无显式拒绝——一条全局单槽位链 + 抢占/排队：

- 全局 `run_state`（`server.py:708-709`）：`{"task","cancel","preempt_gen","surface","running_surface"}`——单 run 的全部真相。
- 受理统一走 `_schedule_run()`（`server.py:1272-1284`）：`_preempt_if_same_surface()`（:1251-1270）同 surface 抢占（`_preempt_current` :1246-1249），跨 surface 链式排队 + notice「另一个窗口还在说」（:1269-1270）。
- 串行化由 `_chain_start()`（:1370-1407）保证：等上一任务收尾（`_PREEMPT_GRACE_S` 超时强 cancel 自愈）；`preempt_gen` 前进则一启动即置 cancel 快速跳过（:1401-1402）。
- 手机端 `/v1/chat` 走同一 `_schedule_run("mobile",…)`（:1288-1295）。
- 例外：L0 只读 panel_action 独立并发（:1518-1530，`_is_readonly_direct` :588-603，`readonly_tasks` :711）。
- 遗留同步路径 `serve()`/`_run_and_emit`（:606-638）顺序阻塞，测试/降级用。

## 2. run 生命周期的共享状态

| 状态 | 位置 | 性质 |
|---|---|---|
| `run_state` | server.py:708-709 | 全局单槽，核心改造对象 |
| 三连取消 | cancel → `_stream_agent`（:1137-1172）：停 LLM（`loop.py:413-417` 每拍查）+ 停 TTS（`_pump_tts` :1124-1135）+ 哨兵清 TTS 队列（:1162-1163）；聆听打断 `voice.stop_listen()`（:1188-1190） | cancel 是 per-run Event 但挂全局槽；`batch_confirmer` 也读它（:754） |
| history | `history.py:42-141` 按 conversation_id 分桶（:52-56），arun 按桶读写（`loop.py:376-394/442-447/559-564`）；**无锁**，`record_messages`→`_save`（:112-141）整桶读改写 + tmp 覆盖 | 半 per-会话，缺并发保护 |
| 确认框 | `pending_confirms`/`early_answers`/`confirm_meta`/`_confirm_done`（:699-702）+ `batch_confirmer`（:720-775）；注释「未来多 run 并发这层不用改」（:698）——但 :754 绑全局 cancel；`confirm_meta` 无会话归属 | future 多槽 OK，cancel 绑定需 per-run |
| TTS | `VoiceCapability` 单例 + `_PersistentPlayer`（`voice.py:195-313`，段队列 `_q` :217）；每 run 自建 `tts_q`（:1139）但播放器共享 | 保持全局串行 |
| proactive | `proactive.py:14-90`：surface=None 全广播，提醒固定 surface="pet"，TTS 前置查 `run_state["task"]` idle（:78-86） | 全局共存，idle 判定需适配 |
| `_FOCUS` 面板焦点 | server.py:59、:1554；run 时注入 LLM 上下文（`loop.py:122`） | 全局单值，并发串味，v1 不动 |
| `invoke_ctx` 截图唤起 | server.py:800、消费 :1494 | 全局一次性，不动 |
| `gate.session_allowed` | `invoker.py:55-115` | 进程级内存，语义写明即可 |

## 3. 会话模型

- 壳端会话一等公民：`app/src/state/domains/conversation.ts:171 createConversation`，消息按 conversationId 分桶（:268-282）；Rust `session_db.rs:74-83` 按 conversation_id 持久化；`SessionList.vue:46` 新建；pet 窗固定 `petConvId`（`App.vue:98,1010-1012`）。
- 大脑不生成会话 id，全由发起方带：壳 `run_input`（`lib.rs:982-1021`，:1018 透传）；手机 `chat.ts:30` uuid、`newChat` 换 id（:152），POST `/v1/chat` 带 conversation_id（`chat.ts:123` → `http_api.py:263-271`）。
- 桌面 run 的 `id` 恒 0（`lib.rs:1017`），rid 不构成 demux 键；手机 rid 是 `mob_N`（:1286-1291）。

## 4. 事件流与 demux

- 事件已带双层归属：`write_msg({"type":"event","surface":…,"conversation_id":…})`（:1147/1155/1160/1169/1180）。
- 壳侧：Rust bridge 提 snake+camel 双 key（`lib.rs:491-504`），按 belongs_main 落库（:515-537，回退活跃会话指针 :532），`emit("brain-event")` 全窗广播（:538），前端自行过滤（`App.vue:623`、`HomeChat.vue:416-418`）。
- 手机侧：SSE 全广播环形缓冲（`http_api.py:38-104`，`_with_envelope` 并入归属 :29-34），客户端只按 surface==="mobile" 过滤、**不按 conversation_id**（`mobile/src/state/chat.ts:41-48`，注释明言串台风险）。
- `run_done` 只回 rid（:1171），需补 conversation_id。

## 5. watch/提醒/后台任务

- `BackgroundJobManager`（`background_jobs.py:17-107`）独立线程 + RLock，无冲突。
- watch（`watch.py:219-221`）独立线程 emit，不占槽；但 `ProactiveChat` 搭话、`_dispatch_reminder`（`background.py:39-68`）的 TTS 用 `run_state["task"]` 判 idle（`proactive.py:78-79`、`background.py:58-60`）——多 run 后要改「所有槽空闲」。
- 提醒落 default 历史桶（`background.py:51-54`），归属语义 spec 已列为非目标。

## 6. 既有测试（现行单槽语义）

- `test_server.py:160` 同 surface 抢占；`:197-235` 跨 surface 排队 + notice + 顺序；`:240-269` 同 surface 仍抢占；`:402` 连投抢占时序；`:1153-1203` panel_action 写占槽/只读绕过；`:1462` hung 任务槽位自愈；`:2207-2264` 手机 interrupt 域限定；`:2544-2578` 手机排队 interrupt 不误杀桌面。
- `test_loop.py:248-273/300-315/363` arun 打断；`test_voice.py:122-208` 打断停播报/聆听。
