# Coding 线产品化（第一轮：可用性 + 透明）设计（2026-08-08）

> 对标：Claude Code / Cline / Cursor 2026 收敛的四条金线——改动可见可审、权限模式可切、长跑透明、会话可恢复。
> 本轮目标：coding 面板从「demo」到「能用起来」——先灭三个致命伤，再补透明度和会话恢复。

## 1. 范围

**P0（致命）**：①中断假死（取消后无终态事件，UI 锁死）②流式重入（同 sid 双 runner 竞态）③每次发送弹 L2 确认。
**透明**：④tool_result/thinking/usage 渲染 + 运行状态条（spinner/耗时/token）。
**手感**：⑤diff 重做（删 Monaco 死代码，默认展开着色 + +x/-y 统计）⑥会话历史抽屉（列表 + 读回 + 接着聊）⑦文件夹选择器 + 分工 steer（执行 8/6 现成计划）。

**不做（下轮）**：checkpoints/rewind、plan mode 切换、@files、can_use_tool 权限交互、transcript 落库。

## 2. 逐项设计

### ① 中断假死
`_runner.py` 取消路径（:139-141）不再静默 `return None`——发 `{"kind":"stopped","text":"已中断"}` 终态事件；coding.py `_stream` 识别 stopped → 落库 stopped + 推 panel_data 终态；chat.html 新增 `stopped` kind 处理 → `onSessionEnded(sid, "已中断")` 复位状态行与按钮。

### ② 流式防重入
- 面板：发送后 `send.disabled` 保持禁用，直到 done/stopped/error 终态才复位（不再 invoke 返回即解锁）。
- 后端兜底：`SendSkill.run` 先查会话 status，running → 直接失败「会话正在运行，请先中断」。

### ③ 确认降噪
`StartSkill`/`SendSkill` `default_risk` L2_MEDIUM → L1_LOW。理由：文件改动已由 SDK `permission_mode="acceptEdits"` 管理，会话启动/续聊本身不执行高危动作；高频对话循环弹确认 = 不可用。

### ④ 透明渲染
normalize 不再丢弃：
- `thinking`（ThinkingBlock → 截 500 字）→ 面板淡色斜体块
- `tool_result`（UserMessage ToolResultBlock → 截 800 字 + is_error）→ 折叠在对应 tool 卡下的暗色块
- `done` 携带 `usage`（input/output tokens、cost、duration_ms，duck-typed 防御）→ 状态行「✓ 完成 · 12s · 3.2k tok」
状态条：streaming 期间 CSS spinner + 秒表（JS interval）。

### ⑤ diff 重做
删除 Monaco loader 全部死代码（chat.html:9-14 注释、:641-649）。file_edit 卡片：默认展开；头部加 `+x/-y` 统计；LCS 行级 diff 着色（绿增红删灰上下文）默认显示；Write = 全绿新增；MultiEdit 按 edits 数组逐段渲染（不再 JSON 坨）。

### ⑥ 会话历史抽屉
- 新 `_cc_reader.py`（仿 `_codex_reader.py`）：`read_transcript(cc_session_id, limit=40)`——`~/.claude/projects/**/*.jsonl` 按 session id 匹配，解析 user/assistant 文本行，返回最近 N 条。
- 新 api 方法 `coding.history`（direct+quiet）：`{id} → {messages:[{role,text}], cwd, cc_session_id}`。
- 面板 header 加「历史」按钮 → 浮层列会话（时间/cwd/首行 prompt/status 徽标，数据来自既有 `coding.list`）→ 点击：设 currentSession + 调 `coding.history` 读回渲染（灰分隔线「—— 历史 ——」）→ 之后 `coding.send` 自动 resume（cc_session_id 已在库）。

### ⑦ 文件夹选择器 + 分工 steer（8/6 计划原文执行）
FP1：tauri-plugin-dialog + `pick_folder` 命令 + capabilities `dialog:allow-open`；FP2：WebviewPanel `native:` 白名单旁路（只放 `native:pick_folder`）；FP3：chat.html cwd 药丸加 📁 按钮；DIV1：loop.py SYSTEM_PROMPT 加 coding 分工段 + 断言测试。

## 3. 错误处理

- runner 一切异常 → error 事件（现状保持），面板复位输入。
- `_cc_reader`：transcript 缺失/解析失败 → `{messages:[]}`（面板静默当新会话，不报错）。
- 防重入后端失败文案直接显示在状态行。
- L1 后仍保留风险闸门语义（API 层可再收紧）。

## 4. 测试策略

- sidecar pytest：runner stopped 事件、SendSkill 防重入、risk 断言更新、normalize 新 kind（thinking/tool_result/done-usage）、_cc_reader（tmp 目录伪造 jsonl）、coding.history 端、steer 断言。测试文件：test_coding_plugin.py / test_coding_handoff.py 追加为主。
- Rust：cargo check（FP1 新插件编译）；`native:` 白名单无单测（WebviewPanel TS 层，vue-tsc 覆盖）。
- 面板 JS：无框架，vue-tsc/vite build + 真机逐项。

## 5. 风险

- **SDK 内部字段**（usage/thinking 块类型名）以 duck-typing 防御，拿不到就降级不渲染——不阻断流。
- **历史读回**依赖 `~/.claude/projects/` 私有格式——解析失败静默降级，不让「恢复」变成「报错」。
- **L2→L1**：用户已验收「改文件自动批」的 SDK 行为；如后续要 Bash 审批交互，走 can_use_tool（下轮）。
