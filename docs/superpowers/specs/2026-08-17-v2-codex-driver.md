# v2 Codex 驱动 spec——coding 面板多引擎化第一步

日期：2026-08-17 ｜ 依据：docs/research/2026-08-16-yibao-collab-patterns.md + Codex CLI 能力调研（0.137.0 本机已装）

## 目标

coding 插件从 Claude Code 单引擎 → 双引擎（CC + Codex CLI）。用户可以：
1. 新会话选择引擎（ctx-row 引擎 chip，无活动会话时可切；默认取该 cwd 最近会话的引擎，sessions 表天然记忆）
2. Codex 会话完整体验：流式回复、工具卡（命令执行/文件改动/MCP/web 搜索）、中断、终态汇报（Feed 任务卡/气泡）、会话墙与译宝历史并列显示（带引擎徽标）、接续 popover codex 卡可原生续（不再只能交接给 CC）
3. 译宝大脑可按引擎派生任务（coding.start agent 参数已有 schema，接线即可）

## 关键设计（侦察依据）

- **驱动抽象零改造**：`AgentRunner` Protocol 已存在（_runner.py:286-291：`async run(prompt, cwd, *, on_event, cancel_event, resume_session_id, permission_mode, can_use_tool, session_entry) -> str|None`）。新建 `plugins/coding/skills/_codex_runner.py` 实现 `CodexCliRunner`，与 `ClaudeCodeRunner` 并列；coding.py 的 `_runner_for(agent)` 选择（{"claude-code","cc"}→CC，"codex"→Codex）
- **子进程形态**：`codex exec --json -s <sandbox> -C <cwd> --skip-git-repo-check -`（prompt 走 stdin，`-` 触发，防 argv 长度上限）；resume = `codex exec resume <thread_id> --json … -`。模式映射：plan→read-only，acceptEdits→workspace-write（headless 无运行中审批钩子，approval 恒 never——L2 确认条对 Codex 会话天然不触发，符合调研的降级预期）
- **driver 会话 id** 复用 sessions.cc_session_id 列（侦察确认通用语义）：存 thread.started 的 thread_id；SendSkill 按行 agent 选驱动并 resume（`codex exec resume` 有已知 encrypted_content bug——失败转 error 事件，用户可新会话+brief，v1 不做自动 fallback）
- **事件归一**（JSONL→面板事件，字段对齐 _runner.py 清单）：agent_message→text_delta（item.completed 全量一条，v1 不做流式 diff）；reasoning→thinking（截 500）；command_execution→tool_use{Bash,{command}}/tool_result{text,is_error}；file_change→file_edit{tool,path,old:null,new:null}（卡片显示路径，无 diff 降级）；mcp_tool_call→tool_use{server.tool}；web_search→tool_use{WebSearch}；todo_list→tool_use{TodoWrite}；turn.completed→done{usage}；turn.failed/error→error{text}；user_msg 不发（无 uuid 锚点→⏪ 天然隐藏，chat.html 1.5s 兜底气泡已覆盖）
- **usage 差分**：turn.completed.usage 是 thread 累计值——runner 在 session_entry 记 `usage_baseline`，本次增量=当前累计−baseline 并更新 baseline（内存态，sidecar 重启漂移一次，留档）；cost_usd 无→None（前端容缺）；duration_ms 由 runner 计时
- **中断**：cancel_event 轮询 + SIGTERM（3s 宽限）→SIGKILL；发 stopped 不发 done（对齐 CC 语义）
- **RewindSkill 守卫**：session agent 非 claude-code → 明确报错「⏪ 回滚仅支持 Claude Code 会话」
- **引擎偏好记忆**：不加存储——默认引擎取「当前 cwd 最近会话的 agent」（sessions 表天然 per-cwd 记忆）；chip 切换只影响下一个新会话（新会话落库后自然成为下次默认）
- **可用性检测**：coding.drivers（L0 quiet）→ [{id, available, version?}]（shutil.which("codex")；auth 检测留档，未登录时 exec 秒败走 error 事件）

## UI 变化

- ctx-row 加引擎 chip（无活动会话时可见可点：CC⇄Codex 切换；有会话时只读徽标显示当前会话引擎；codex 不可用时灰显禁用）
- send() 的 coding.start 参数加 agent；新会话落库 agent="codex"
- panel_data 的 data 加 agent 平级键（_stream 透传），chat.html 实时更新徽标
- done usage 前端容缺（cost_usd 缺失时只显示 token/耗时）
- 会话墙卡 subtitle 加引擎前缀（Codex · 运行中 · 3 分钟前）；译宝历史行加引擎徽标
- 接续 popover：codex 卡从「交接给 CC」改为双钮 [原生续（Codex）] [交接给 CC]；原生续 = codex exec resume（走 attach 通道新开 codex 会话：coding.attach_codex 复用 last_sessions 的 codex id，建 DB 行 agent="codex"+cc_session_id=thread_id，返回 session_id 前端 resumeSession）

## 非目标

- codex app-server（JSON-RPC 实验通道）逐工具审批——等其稳定
- agent_message 流式增量（v1 整段到达）
- resume 已知 bug 的自动 brief fallback、usage baseline 持久化
- cursor CLI（v3 留档）、Best-of-N、worktree 隔离（R4 后续）

## 验收标准

A. 引擎 chip：无会话时可见可切，默认=该 cwd 最近会话引擎；codex 不可用灰显
B. Codex 新会话：发任务→流式回复/工具卡正常；中断生效（进程被杀）；done 有 token 增量显示
C. Codex 会话续聊：同会话第二条消息走 codex exec resume（上下文连续）
D. Codex 会话无审批卡、无 ⏪ 锚点；mode pill 切换映射 sandbox（下条消息生效）
E. 终态汇报：Feed 任务卡+气泡与 CC 会话同构（usage 后缀无成本时降级）
F. 会话墙/译宝历史/接续 popover 的引擎徽标正确；墙「接管」「停止」对 codex 会话生效
G. 接续 popover codex 卡 [原生续] 恢复 codex 会话可接着聊；[交接给 CC] 原流程不回归
H. CC 会话全部行为零回归（含审批/rewind/usage）
I. sidecar pytest 全绿（含 codex runner 全事件映射/取消/usage 差分/模式映射/resume 参数测试）
