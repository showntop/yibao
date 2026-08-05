# Coding 插件 · 跨 agent 交接（Codex → Claude Code）设计

日期：2026-08-05
状态：已评审（用户确认交互流 + 卡片设计）
关联：coding 插件（`2026-08-04-coding-plugin-design.md`）+ 多轮修复（`2026-08-05-coding-multiturn.md`）。前置已落地：coding 插件能驱动 Claude Code（claude-agent-sdk，多轮 resume）；本设计是给它加"从外部 agent（Codex）带上下文切进来"。

## 1. 定位与目标

用户在 Codex 的某个 session 干到一半，想切到 Claude Code 继续。痛点：代码状态 git 共享，但 **Codex 的对话/意图上下文丢了**——Claude Code 不知道"在干嘛/到哪/下一步"。

本功能：译宝**读 Codex session transcript + git 状态 → 生成交接 Brief → 经一张可审阅的「交接卡」→ 确认后作为 Claude Code 的首条 prompt 起一个 coding 会话**。用户在译宝一个面板内完成跨 agent 交接，不用手抄上下文。

**核心设计决策（用户确认）：**
1. **触发 = cwd 匹配**：点「从 Codex 接续」→ 译宝找 `payload.cwd` == 当前面板项目的 Codex session；一个直接用，多个弹选择器，没有则提示。
2. **可审阅编辑的交接卡**：Brief 生成后插入聊天流（不是弹窗），标源（Codex · session · 时间）、可编辑、点「用它开始」才起 Claude Code。确认后卡片封存为只读"会话起点"标记。
3. **复用 coding.start**：确认 = `coding.start({cwd, prompt: brief})`——brief 即首条 prompt；多轮走现有 send。**不新增会话模型**。

## 2. 交互流（面板状态机）

```
顶栏 [↩ 从Codex接续] 按钮
   │ 点击
   ▼
coding.handoff_list({cwd}) → 匹配的 Codex session 列表
   ├─ 0 个 → 提示"该项目没有 Codex session"，按钮回弹
   ├─ 1 个 → 直接进 brief 生成
   └─ N 个 → 弹选择器（时间 + 首句预览）→ 用户选一个
   ▼
coding.handoff_brief({session_id, cwd}) → 读 session + git → LLM 摘要 → Brief
   ▼
【交接卡】插入聊天流（源标记 + 可编辑 brief + [取消]/[用它开始]）
   │ 点「用它开始」
   ▼
coding.start({cwd, prompt: brief})  ← 复用现有
   ▼
卡片封存为只读起点标记 + Claude Code 流式接续（brief 为上下文）
```

**交接卡视觉**（聊天流内特殊元素）：
- 头：`↩ 来自 Codex · session <短id> · <时间>`。
- 体：可编辑文本框（译宝摘要：任务/已完成/当前/下一步 + git 状态）。
- 尾：`[取消] [用它开始 → Claude Code]`。
- 确认后：头改为`↩ 已从 Codex 接续`，体变只读，留作会话起点标记。

## 3. 架构

### 3.1 新模块 `plugins/coding/skills/codex_reader.py`
- `list_sessions(cwd: str) -> list[dict]`：扫 `~/.codex/sessions/**/*.jsonl`，读每文件首行 `session_meta`，按 `payload.cwd` 规范化（`os.path.realpath`）== `realpath(cwd)` 过滤；返回 `[{session_id, cwd, timestamp, first_line（首条用户消息预览）, path}]`，按时间倒序。
- `read_conversation(path: str, tail_turns: int = 8) -> list[dict]`：解析 JSONL，提取对话事件（用户消息 / assistant 最终回复 / 关键工具调用），取**最近 tail_turns 轮**。Codex JSONL 事件的确切 schema 在实装时按真实文件核对（首行已确认是 `session_meta` 带 `payload.cwd/session_id/timestamp`；后续行是事件流）——**测试用基于真实形态造的 JSONL fixture，不依赖运行时 Codex**。
- 容错：JSONL 损坏行跳过；能读多少读多少，返回 `{"incomplete": bool}` 标记。

### 3.2 Brief 生成（复用主 LLM provider）
`build_brief(conversation: list[dict], git_summary: str) -> str`：
- 输入：Codex 对话尾段 + git 状态摘要（`git log --oneline -10` + `git status --short`，限 cwd）。
- LLM 提示：把上面的 Codex 工作记录凝练成一段「交接 Brief」，结构为 任务/已完成/当前卡点/下一步建议（中文、具体带文件名），给另一个 coding agent（Claude Code）当接续上下文。宁缺毋滥、别编造。
- 复用 `GLMProvider`（与 distiller 一致）；失败返 None（卡片走"生成失败/手动粘贴"兜底）。

### 3.3 新 skills（`plugins/coding/skills/coding.py`）
- `coding.handoff_list({cwd})`（L0 直调）：→ `list_sessions(cwd)`，返 `{sessions: [...]}`。面板据此渲染选择器 / 直进。
- `coding.handoff_brief({session_id, cwd})`（L0 直调）：→ 找该 session 文件 → `read_conversation` + git 摘要 → `build_brief` → 返 `{brief, session_id, incomplete}`。**生成过程可走 panel_data 流式**（让用户看到 brief 在生成），或直接 action_result 返回——v1 直接返回（一次 LLM 调用，卡片整体渲染）。
- 确认（「用它开始」）= **复用 `coding.start({cwd, prompt: brief})`**，不新增。

### 3.4 sessions 表加 source 列（可追溯）
`{name = "source", type = "text", default = ""}`（幂等迁移，default "" 兼容存量）。`coding.start` 增可选参数 `source: str = ""`；交接确认路径调 `coding.start({cwd, prompt: brief, source: f"codex:{codex_session_id}"})`。这样 sessions 行能查"这条 CC 会话从哪个 Codex session 接续来"。普通 start 不传 source → ""。

### 3.5 前端 `chat.html`
- 顶栏加 `[↩ 从Codex接续]` 按钮。
- 点击：`coding.handoff_list({cwd})` → 0 个提示；1 个直接 `handoff_brief`；N 个渲染选择器（浮层/下拉）→ 选后 `handoff_brief`。
- brief 回来 → 渲染**交接卡**（源头 + 可编辑 textarea + 取消/用它开始）。
- 「用它开始」→ 取编辑后的 brief → `coding.start({cwd, prompt: brief})` → 卡片封存只读 → CC 流式接续（复用现有流式渲染）。
- 「取消」→ 移除卡片，不动 currentSession。

## 4. 数据流（一次交接）

```
用户在面板（cwd=~/Work/myproj）点 [↩ 从Codex接续]
   ▼ invoke coding.handoff_list({cwd})
译宝 list_sessions → 扫 ~/.codex/sessions/**/*.jsonl，realpath 匹配 cwd
   ▼ 返回 sessions[]（倒序）
面板：1 个 → 直进；N 个 → 选择器；0 个 → 提示
   ▼ invoke coding.handoff_brief({session_id, cwd})
译宝 read_conversation(jsonl 尾段) + git log/status(cwd) → build_brief (LLM)
   ▼ 返回 {brief, session_id, incomplete}
面板：渲染交接卡（可编辑）
   ▼ 用户改完点 [用它开始]
invoke coding.start({cwd, prompt: 编辑后brief})   ← 复用
   ▼
Claude Code 起，brief 为首条/上下文，流式接续
```

## 5. 错误处理（挂了不碍事）
- 该 cwd 无 Codex session → `handoff_list` 返空 → 面板提示，不报错。
- JSONL 损坏/读不全 → `read_conversation` 跳过坏行、标 `incomplete=True`；brief 仍生成（基于能读的部分），卡片标注"基于不完整记录"。
- brief LLM 失败 → `handoff_brief` 返 `{brief: null}` → 卡片显示"自动生成失败，[重试]/[手动粘贴]"，用户可手填 brief 再「用它开始」。
- git 摘要失败 → 只用 Codex 对话生成 brief，跳过 git 部分（标注）。
- 全程不抛主链路；任何失败只在卡片内呈现。

## 6. 实现落点

sidecar（`plugins/coding/skills/`）：
- 新 `codex_reader.py`：`list_sessions` / `read_conversation`（纯函数，可单测，造 JSONL fixture）。
- `coding.py`：加 `HandoffListSkill` / `HandoffBriefSkill`（直调、L0）；brief 生成调主 provider。
- `manifest.toml`：sessions 加 `source` 列（default ""）。

前端（`plugins/coding/panel/chat.html`）：
- 顶栏 `[↩ 从Codex接续]` 按钮 + 选择器 + 交接卡渲染 + 封存逻辑。

api.toml：加 `coding.handoff_list` / `coding.handoff_brief`（direct=true）。

## 7. 测试与验收

自动化（pytest，FakeProvider + JSONL fixture）：
- `list_sessions`：造几个 rollout-*.jsonl fixture（不同 cwd）→ 按 realpath 过滤命中、倒序、返回 session_id/timestamp/first_line。
- `read_conversation`：fixture 含 session_meta + 多种事件行 → 提取对话尾段、跳过坏行、标 incomplete。
- `build_brief`：FakeProvider 造 brief 文本 → 注入对话+git → 断言返回非空且含结构（任务/下一步）。失败路径（provider 抛异常）→ 返 None。
- HandoffListSkill / HandoffBriefSkill：ctx.db/fake → list 返会话；brief 读 fixture + 调 provider。

前端：vue-tsc/vite build exit 0（chat.html 资源）。

真机验收（留人工）：
1. 在某项目用 Codex 干一段活 → 译宝编码面板选同项目 →「从 Codex 接续」→ 选对 session → 卡片显示 brief（任务/已完成/卡点/下一步，带文件名）→ 可编辑 →「用它开始」→ Claude Code 带 brief 接续，问它"刚才到哪了"能答上。
2. 多 session 项目：选择器正确。
3. 无 Codex session 的项目：按钮提示。
4. brief 自动失败：手动粘贴兜底可用。

## 8. 明确不做（本轮）
- 反向交接（Claude Code → Codex）——Codex 无等价 SDK，留后。
- 自动检测"该交接了"（perception 发现 Codex 关、Claude Code 开 → 主动建议）——v2 增强。
- 跨 agent 机构记忆（mem0 沉淀 Codex 决策）——独立特性。
- 非 Codex 源（其他 agent session reader）——reader 抽象预留，本轮只 Codex。
