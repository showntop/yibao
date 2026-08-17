# v2 Codex 驱动 实施 plan（对应 specs/2026-08-17-v2-codex-driver.md）

worktree：.worktrees/feat-codex-driver（分支 feat/codex-driver，base main 8247838）
执行：D1（sidecar）与 D2（chat.html）按契约并行；D3 终审。

## 关键事实（侦察结论）

- AgentRunner Protocol：_runner.py:286-291；CC 实现 ClaudeCodeRunner（_runner.py:297-386），事件 kind/载荷清单见 spec；coding.py:53 硬编码 `ClaudeCodeRunner = _runner.ClaudeCodeRunner`
- _spawn_stream/_stream：coding.py:104-240；panel_data 透传 `{"session_id": sid, "event": ev}`（:196-198）；_persist 落 messages（user/assistant/marker，:163-180）；终态 _report_final（:240）+ `_usage_suffix`（:243-282）；终态写 sessions.cc_session_id=runner 返回值（:232-236）
- StartSkill：agent 参数 schema 已有默认 "claude-code"（:326-356），runner 直接 new ClaudeCodeRunner（:363）；SendSkill resume：读 row.cc_session_id（:417）→ _spawn_stream(resume_session_id=cc)（:428-429）；RewindSkill 直接 new ClaudeCodeRunner（:796-814）
- attach_cc 写 agent="cc"（:1068）→ _runner_for 须把 "cc" 映射 CC
- ListSkill spread 整行（agent 已在数据里，:514）；WallDataSkill 卡片 {id,live,title,subtitle}（:598-608）；last_sessions codex 卡 {session_id,ts,summary}（_codex_latest_session）
- chat.html：handleInitData 各 kind 读字段（1539-1659）；addUsage/setStatusDone 读 usage 四键（1520-1533、836-843）；ctx-row 结构（cwd chip/mode pill/at-btn）；send() coding.start 参数（~1740）；resumeSession（~2275）；接续 popover codex 卡现走 handoff()（~2160）；prefillCwd（~2559）
- codex CLI 0.137.0：`codex exec --json -s read-only|workspace-write -C <cwd> --skip-git-repo-check -`（stdin 喂 prompt）；`codex exec resume <id>|--last`；turn.completed.usage={input_tokens,cached_input_tokens,output_tokens} 累计值；item 类型 agent_message/reasoning/command_execution/file_change/mcp_tool_call/web_search/todo_list；事件 thread.started/turn.started/turn.completed/turn.failed/item.started|updated|completed/error
- sidecar 无 asyncio 子进程先例（agents 插件 Popen+日志文件重定向）：runner 线程内 asyncio loop 自建 create_subprocess_exec 管道流式读
- session_entry 是 _SESSIONS[sid] dict（mode_pending/rewind_pending 通道）：codex 驱动复用它存 usage_baseline；mode_pending 对 codex 不生效（下次 run 用新 sandbox，SendSkill 读库 mode 传入即生效）

## 任务

### D1（sidecar：CodexCliRunner + 选择器 + 管线；文件 _codex_runner.py（新建）/coding.py/api.toml/测试）
1. `_codex_runner.py`：CodexCliRunner 实现 AgentRunner Protocol（可注入 process_factory 供测试 fake）；argv 构建（模式映射/resume/-C/--skip-git-repo-check/--json）；stdin 喂 prompt 后关闭；逐行读 stdout JSONL 归一事件（容忍非 JSON 行跳过）；thread.started 捕获 thread_id 作返回值；turn.completed usage 经 session_entry["usage_baseline"] 差分；cancel → SIGTERM→3s→SIGKILL → stopped；turn.failed/error → error；异常不外抛转 error 事件（对齐 CC 语义）；can_use_tool/mode_pending/rewind_pending 忽略（注释注明）
2. coding.py：`_runner_for(agent)`（{"claude-code","cc"}→ClaudeCodeRunner，"codex"→CodexCliRunner，未知→明确错误）；StartSkill/SendSkill 经 _runner_for；RewindSkill 守卫 agent；_spawn_stream/_stream 加 agent 透传（panel_data data 加平级 "agent"）；_report_final/_usage_suffix 容 cost_usd=None
3. `coding.drivers`（L0 quiet，api.toml 注册无 panel）：[{id:"claude-code",available:true},{id:"codex",available:bool,version}]（shutil.which+`codex --version` 容错）
4. `coding.attach_codex {session_id}`（L0 quiet）：codex thread_id 建 DB 行（agent="codex", cc_session_id=thread_id, source="native", status="done", prompt 从 _codex_reader 读首条 user 摘要，幂等按 cc_session_id）→ 返回 {session_id}（接续 popover 原生续用）
5. WallDataSkill subtitle 加引擎前缀（"Codex · "/"CC · "）
6. 测试全覆：事件归一逐类型、usage 差分、cancel kill、resume argv、模式映射、_runner_for、attach_codex 幂等、drivers 检测、CC 回归
- `uv run --extra dev pytest -q` 全绿（基线 1020）

### D2（chat.html；与 D1 并行，按 spec UI 节 + 契约编程）
1. ctx-row 引擎 chip：`#agent-chip`（mode pill 旁）：无 currentSession 可点切换 CC/Codex（coding.drivers 探测，codex 不可用灰显禁用+title 原因）；有会话只读显示当前引擎；打开面板/attach/resume/newChat 时刷新（默认=当前 cwd 最近会话 agent——coding.list 已有 agent 字段，prefillCwd 处取）
2. send() isStart 参数加 agent:curAgent；handleInitData 读 data.agent 更新徽标；done usage 容缺（cost_usd None 只显 token/耗时）
3. 接续 popover codex 卡：[交接给 CC] 保留，加 [原生续] 主钮 → coding.attach_codex{session_id} → resumeSession
4. 译宝历史行 historyRow 加引擎徽标文本（row.agent："codex"→Codex，否则 CC）
5. python3 提取 <script> node --check；CC 路径回归推演

### D3 终审 + 修复波
- spec 验收 A-I 逐条静态+自动化；跨文件契约核对（panel_data agent、attach_codex、done usage 容缺、墙 subtitle）；diff 卫生

## 风险备案

- codex exec 未登录 auth → 秒败 error 事件（文案引导「先 codex login」）；drivers 只检二进制不检 auth（留档）
- prompt 含特殊字符走 stdin 无转义风险；超长 prompt stdin 无 argv 上限
- codex 会话 permission 永不触发 → _PERM 无残留；release_pending_permissions 对 codex sid 无前缀命中，安全
- item.updated 的流式增量 v1 丢弃（只取 completed），体感=整段到达，留档
- 多 codex 会话并发=多子进程，无锁问题；进程泄漏靠 cancel kill + sidecar 退出时 daemon 线程随进程结束（对账留档，与 CC 重启对账缺口同级）
