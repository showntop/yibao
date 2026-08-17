# v1 接续模型统一 plan（接续 specs/2026-08-17-p2-supervisor-wall.md，本轮滚动任务）

worktree：.worktrees/feat-p1-takeover（分支 feat/p1-takeover，续接 633f257）

## 设计定稿

「历史」+「Codex 接续」两钮收敛为顶栏一个「接续」入口（popover，改造现有 history-overlay 抽屉）：

- **区 1「上次会话」**（跨源检测，每源最新一条，不存在则不显示该卡）：
  - cc 卡：`~/.claude/projects/<slug>/` 最新 transcript（含 DB 外会话）→ [继续] = coding.attach_cc 导入后 resumeSession（原生 resume，上下文完整）
  - codex 卡：`~/.codex/sessions` 按 cwd 过滤最新（复用 handoff_list 逻辑）→ [交接给 CC] = 现有 handoff_brief→start 流程（brief 有损交接，文案注明「交接=生成摘要交给 Claude Code，非完整搬移」）
  - 目标驱动选择器（codex/cc/cursor…）v2 引入新驱动后才有意义，v1 交接目标恒为 CC
- **区 2「译宝历史」**：现有 coding.list 列表 + resumeSession，不变
- **空态（新项目）**：「新项目——直接输入任务开始（Claude Code）」；v2 此处变驱动选择器
- keys-row 的 handoff 按钮移除；633f257 的 syncActionRow 退役 handoff 部分（只保留 stop 可见性逻辑），probeHandoff 删除

## 接口契约（C1/C2 并行依据，逐字对齐）

- `coding.last_sessions {cwd}` → `{cc: {cc_session_id, ts, summary, message_count} | null, codex: {session_id, ts, summary} | null}`
  - cc：~/.claude/projects/<cwd slug>/ 下 mtime 最新 .jsonl（排除 subagents/ 目录），summary=首条 user 消息截 60 字，message_count=user+assistant 行数；复用/扩展 _cc_reader
  - codex：复用 handoff_list 的扫描逻辑取最新一条（session_id 形态与 handoff_brief 入参对齐）
- `coding.attach_cc {cc_session_id, cwd}` → `{session_id}`：_cc_reader 读 transcript → sessions 表 insert（agent="cc", cc_session_id, source="import", status="done"）+ messages 表写 transcript；**幂等**：cc_session_id 已在库则直接返回既有 session_id
- 前端拿到 session_id 后走现有 resumeSession（send 时 SDK resume cc_session_id 的链路需 C1 核实确认——coding.send/start 的 resume 语义，若现有链路不支持 DB 外 cc_session_id 的 SDK resume，attach_cc 负责把关联写正确使其工作）

## 任务

### C1（sidecar；coding.py + 测试）
- 实现 last_sessions / attach_cc（L0 direct，quiet，api.toml 注册，无需 panel 字段——popover 内调用不开面板）
- 核实并打通 SDK resume：attach 后 send 消息走 cc_session_id 原生续（读 _runner.py resume 参数与 coding.send/start 现状，缺则补最小链路）
- 测试：last_sessions 双源/空源、attach_cc 导入+幂等+transcript 落库、resume 链路
- `uv run --extra dev pytest -q` 全绿（基线 1010）

### C2（chat.html；与 C1 并行，按上面契约编程）
- history-overlay 改造为统一接续 popover：区 1（两卡：来源徽标 CC/Codex + 相对时间 + 摘要 + 按钮）+ 区 2（现 history 列表）+ 空态；打开时调 coding.last_sessions{cwd} 实时检测（无缓存，每次打开新查）
- 顶栏「历史」→「接续」（id 保留避免大改，文案/title 改）；keys-row handoff 按钮 DOM 移除；syncActionRow 删 handoff 分支改名或保留（只管 stop）；probeHandoff/handoffCache 删除；handoff() 流程本体保留（popover 的 codex 卡调用）
- python3 提取 <script> node --check；非 takeover/takeover 两模式视觉自查

### C3 终审 + 验证（全量 diff + 两份 spec 验收回归 + v1 新交互静态推演）

## 风险备案

- cc transcript 的 subagent 目录排除（<session-uuid>/subagents/，调研证实布局）
- codex 会话按日期树混存，需读首行 session_meta.cwd 过滤（handoff_list 已有则复用）
- attach_cc 导入的会话 live=idle，会话墙自然可见（agent 列="cc"）
- 交接链 drift：手动单步行为，不防；v2 若做链式交接再加护栏
