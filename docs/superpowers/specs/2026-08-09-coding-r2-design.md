# Coding 线产品化（第二轮：缺口扫平）设计（2026-08-09）

> 目标：把第一轮留下的五个缺口一次做完——rewind、plan mode、@files、can_use_tool 权限交互、transcript 落库。
> 调研结论（SDK 0.2.129 实测）：全部有官方通道，无自研轮子。

## 1. SDK 能力面（已验证）

| 缺口 | SDK 通道 |
|---|---|
| rewind | `enable_file_checkpointing=True` + `extra_args={"replay-user-messages": None}` → UserMessage 带 `uuid` → `client.rewind_files(user_message_id)` |
| plan mode | `permission_mode` ∈ default/acceptEdits/plan/bypassPermissions/dontAsk/auto；`client.set_permission_mode(mode)` 可运行中切换 |
| can_use_tool | options.can_use_tool 异步回调 → PermissionResultAllow/Deny |
| 停止强化 | `client.interrupt()`（当前取消只停读消息，后台工具还在跑） |
| transcript | SDK 无需——我们自己落库（sessions 旁加 messages 表），_cc_reader 保留兜底 |

## 2. 逐项设计

### R1 接线升级（地基，先行）
`ClaudeCodeRunner` options 加 `enable_file_checkpointing=True`、`extra_args={"replay-user-messages": None}`；normalize 放行 UserMessage 提取 `uuid` → `{"kind":"user_msg","uuid","text"}`（user 气泡带 uuid，checkpoint 原料）；取消时 `await c.interrupt()` 后再发 stopped（真杀后台工具）；runner 接 `permission_mode` 参数（默认 acceptEdits，向后兼容）。

### R2 transcript 落库
manifest 加 `[[table]] messages`（id/session_id/role/text/ts/seq）；`_stream` 在 on_event 把 user prompt 与 assistant text_delta（块级）、done 标记落库（thinking/tool_result/usage 不落，保持库瘦身）；`coding.history` 先读库（快、可靠），空则 fallback `_cc_reader`；抽屉渲染逻辑不变。

### R3 plan mode 切换
sessions 表加 `mode` 字段（默认 "acceptEdits"）。面板 prompt 区加 mode pill（自动改文件 ⇄ 只读规划循环切换）→ start/send 传 mode → db 落 mode → runner options 用之。运行中切换：面板点 pill → `coding.mode` api → `_SESSIONS[sid]["mode_pending"]=mode` → runner 每条消息前检查并 `client.set_permission_mode(mode)`（延迟 ≤1 条消息）。plan 模式下 done 后 pill 保持，下条默认沿用。

### R4 rewind 检查点
流中捕获每条 user_msg.uuid 存进 `_SESSIONS[sid]["checkpoints"]`（{uuid,text,ts}）→ 面板给对应用户气泡加「⏪ 回滚到此」按钮 → 点击 → `coding.rewind{id,user_msg_id}`：
- 会话在跑（live client）：`_SESSIONS[sid]["rewind"]=uuid` → runner 下条消息前执行 `client.rewind_files(uuid)`
- 会话已结束（无 live client）：新开 client（resume=cc_session_id + checkpointing on）→ connect → rewind_files → disconnect（CLI 侧 checkpoint 持久，跨实例应可；失败降级文案「回滚失败：CLI 未保留检查点」）
- 面板收到 rewind_ok 事件 → 灰卡「已回滚到 N 点」，不落库不改历史（文件已还原，会话继续）。

### R5 can_use_tool 权限交互
runner options 加 can_use_tool 回调：把 (tool_name,input) 发 `permission_request{sid,rid,tool,input}` 事件进面板流 + 在 `_DECISIONS[rid]` 上等用户裁决（threading.Event，超时 60s 默认 deny「超时未批准」）→ 面板渲染审批卡（工具/参数摘要/允许/拒绝）→ 点击 → `coding.decide{rid,allow}` → 写回 `_DECISIONS` → 回调返回 PermissionResultAllow/Deny。rid 命名 `perm_<sid>_<n>`。decide api：direct+quiet，L1。acceptEdits 模式下文件改动仍自动过（SDK 语义），Bash 等触发审批——这正是 Cline 的核心信任交互。

### R6 @files 上下文
新 api `coding.files{cwd,q}`（direct+quiet）：cwd 下模糊匹配（os.walk 限深 6/限 200 结果/排除 .git node_modules dist target .venv build out）→ 返回 [{path,rel}]。面板 textarea 输入 `@` 触发下拉（模糊过滤，↑↓ 选择，Enter 插 `“@rel ”`，Esc 关）；发送时 prompt 原样携带 @路径（CC 原生理解）。

## 3. 错误处理

- rewind 无 live client 且 CLI 无 checkpoint → 失败文案，不改文件不落错。
- can_use_tool 回调/裁决注册表任何异常 → deny（安全默认）+ stderr。
- plan 模式切换失败（无 live client）→ 落 db 下轮生效（静默）。
- @files walk 权限/过大目录 → 截断返回，不报错。

## 4. 测试策略

- sidecar pytest：R1（checkpoint 选项/replay extra_args/interrupt 调用/user_msg uuid 事件）、R2（messages 落库 + history 先库后 fallback）、R3（mode 透传 db/options/pending 切换调用 set_permission_mode）、R4（checkpoints 捕获/rewind live 路径/非 live 新 client 路径/失败降级）、R5（回调桥请求-裁决往返/超时默认 deny/decide 写回）、R6（files 模糊匹配/排除目录/截断）。
- 面板：无框架，vite build + 真机。
- 全部 SDK 交互用既有 FakeSDK/注入模式，不触真 SDK。

## 5. 风险

- **rewind 跨实例有效性**（非 live client）依赖 CLI 持久 checkpoint——真机验收项，失败有降级文案。
- **can_use_tool 回调跨线程等待**：asyncio loop 在 runner 线程，裁决在 asyncio.to_thread 里等 threading.Event——死锁防护=超时 deny。
- **messages 表膨胀**：只落文本类事件；不做截断 v1（编码会话量小）。
