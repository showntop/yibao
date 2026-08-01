# watch mode 收口设计（隐私、授权、生命周期与产品闭环）

日期：2026-07-31  关联：`2026-07-31-watch-mode-core-design.md`、`2026-07-31-watch-proactive-chat-design.md`、`2026-07-31-watch-command-design.md`

## 1. 目标

把当前 watch mode 从若干可运行切片收口成三个边界清楚、默认安全、可诊断的能力：

1. **健康观察**：只读本地 active/idle 状态，负责久坐提醒。
2. **屏幕主动建议**：仅对用户明确允许的前台应用截图并调用视觉模型；没有实时、可验证的应用身份时禁止截图。
3. **后台任务**：在明确工作目录中运行命令，提供任务 id、状态、取消和可靠的进程树终止。

本轮不加入自动操作屏幕、不做多轮主动对话、不做跨重启任务恢复。

## 2. 核心安全契约

### 2.1 截图白名单绑定实时画面

- perception 数据只用于低成本触发候选，不能作为截图授权依据。
- 截图前必须实时读取前台应用的稳定标识；允许列表使用 bundle id，展示时可附本地化应用名。
- 只允许 `实时前台 bundle id ∈ allowlist` 时截图；截图后再次读取前台 bundle id，不一致则丢弃截图且不上云。
- 感知开关关闭或数据超过新鲜度上限时，快照对应字段必须为空，旧记录不能继续驱动提醒或截图。
- 截图不落库；错误、审计与 Feed 不记录截图内容、窗口标题或模型看到的敏感文本。

### 2.2 任意 shell 不允许按 skill 免确认

- `watch_command` 保持 L3，每次新命令都确认。
- 前端不为该技能展示“本会话不再询问”；后端即使收到 `remember=true` 也忽略。
- 后续若需要免确认，只能基于精确的命令模板、工作目录与参数约束另行设计。

## 3. 运行模型

### 3.1 WatchService

新增 `WatchService` 作为 watch 行为的唯一生命周期控制器：

- 持有 behaviors、budget 和 asyncio task。
- `apply_settings(settings)` 即时启动、停止或重建行为，不要求用户重启大脑。
- watch 开启时，健康观察要求 `perception.master=true` 与 `perception.activity=true`；设置写入采用一次 patch，避免“开了但无数据”。
- 屏幕主动建议单独开关，要求 `perception.app=true`、屏幕录制可用、视觉模型可用、allowlist 非空；缺条件时显示明确状态，不静默假装运行。

### 3.2 统一主动事件出口

新增一个主动事件 dispatcher，健康提醒、屏幕建议、后台任务完成统一经过：

1. 所有事件先写 Feed；quiet 档也可追溯。
2. quiet：不弹、不亮、不出声。
3. bubble：桌宠气泡，不亮窗、不出声。
4. full：亮窗和气泡；仅 `proactive_voice=true` 且没有前台任务播报时语音。

行为层只产出事件，不直接写壳、播音或写 Feed。

### 3.3 触发与预算

- 屏幕建议的触发键为前台 bundle id 变化或 active 段恢复，而不是仅 activity segment 变化。
- 同一个 `app identity + activity segment` 不重复；跨 app 且超过 `look_min_gap` 可再次观察。
- 小时/每日预算在 sidecar 生命周期内共享；失败截图不扣预算，真正准备调用视觉模型前扣一次。
- 视觉输出必须是严格 JSON：`speak` 只接受布尔值；文本清洗空白并限制 20 个中文字符量级，非法输出静默。

## 4. 后台任务模型

`watch_command` 改为 `BackgroundJobManager` 管理：

- 参数：`command`、必填 `cwd`、可选 `name`、有界 `timeout_sec`。
- 返回：`task_id`、label、cwd、status=`running`。
- 使用 `Popen(start_new_session=True)`，stdout/stderr 写入有界临时日志或环形尾部，避免全量驻留内存。
- 超时或取消时终止整个进程组；先 TERM，短暂等待后 KILL。
- 提供 `watch_command_status(task_id)` 与 `watch_command_cancel(task_id)`；状态包含 running/succeeded/failed/timed_out/cancelled、退出码和安全的尾部摘要。
- sidecar 退出时终止仍在运行的任务，避免孤儿进程。

## 5. 设置与交互

设置页不再使用含糊的“观察 / watch”单卡，改成“主动协助”分组：

- **久坐提醒**：开关、分钟、静默时段；开启时自动启用所需的本地活动感知。
- **屏幕主动建议**：独立开关；明确提示“允许的应用截图会发送给当前视觉模型服务”；提供 bundle id allowlist 输入与预算/节流的高级设置。
- **触达方式**：与主动协助放在同一逻辑区，显示安静/气泡/完整和语音开关。
- 状态行显示：运行中、已暂停、缺视觉模型、缺屏幕权限、未选择应用。
- 所有开关使用 `role="switch"`、`:aria-checked`、清晰的 accessible name；分段按钮使用 `aria-pressed`。
- quiet hours 在前后端都按 `HH:MM-HH:MM` 校验，非法输入不保存并显示行内错误。

## 6. 代码组织

- `watch.py`：快照、纯行为、预算与严格解析，不负责输出通道。
- `watch_service.py`：生命周期、实时前台校验、截图任务协调。
- `proactive.py`（或 server 内小型 dispatcher）：Feed、gating、壳事件与语音统一分发。
- `background_jobs.py`：进程、任务状态、取消与输出尾部。
- `skills_real.py`：只保留薄 skill adapter。
- `server.py`：装配服务与 IPC，不再内联 watch 循环和命令线程。

## 7. 测试与验收

自动化必须覆盖：

- 感知关闭/过期时快照为空；旧 app 不能授权截图。
- 截图前后 app 不一致时不上云；非 allowlist 永不截图。
- app 切换会触发，长 active 段内仍受 gap/预算控制。
- quiet 仍落 Feed；voice 开关和 run-state 生效；三类主动事件语义一致。
- `watch_command` 无 cwd 拒绝；无法 remember；状态、取消、超时、进程组清理、日志有界。
- settings 联动、即时启动/停止、quiet hours 校验与开关 ARIA 状态。
- 视觉 JSON 严格布尔、空文本和长度限制；后台异常没有 thread warning。

最小人工验收：

1. 开启久坐提醒后无需重启，状态显示运行中；关闭后下一 tick 不再提醒。
2. 只允许代码编辑器，快速切到邮件后触发候选，确认没有截图上云或主动建议。
3. 后台运行一条指定 cwd 的短命令，查看状态并取消一条长命令；完成/取消均进入动态。
4. quiet/bubble/full 各验证一次 Feed、气泡和语音行为。

---

## 验收记录（2026-08-01，v1.1 收口迭代）

- **触发验收**：`test_watch.py` + `test_watch_acceptance.py` 共 32 例全绿——久坐触发/静默时段抑制/白名单双侧/预算/前台校验/dispatcher 落真实 feed/后台命令线程 emit 链路，满足「该触发 100%、误触发 0」契约。
- **watch_command 跨重启恢复**：已落地（jobs.db 持久化 + 启动孤儿重跑/标 interrupted + Feed 记账），恢复测试 ×3 确定性通过。
- **误报反馈回路**：事件 type 归一（health_nudge/proactive_chat/watch_command/reminder），Feed 条目 👍/👎 落 meta.feedback，同类 24h≥2👎 的 reminder 事件由 dispatcher 降级 quiet。
