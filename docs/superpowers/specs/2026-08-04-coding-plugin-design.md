# Coding 插件（统一 coding 聊天面板）设计

日期：2026-08-04
状态：已评审（用户确认方向 + spike 验证 + 复用栈敲定）
关联：研究 5 个多 agent 项目（`open-multi-agent`/`gastown`/`omnigent`/`ccg-workflow`/`myclaude`）；译宝 `agents` 插件（后台 dispatch）；感知/mem0/Feed。

## 1. 定位与目标

用户高频痛点：**订阅了多个 coding agent（Claude Code / Codex / CodeBuddy），不想在它们的 UI 之间切来切去。** 本插件给译宝加一个**统一 coding 聊天面板**：用户在译宝的一个面板里提 coding 任务，后端**程序化驱动一个 coding agent（v1：Claude Code）headless 跑**，流式回显 + 渲染文件改动，用户不用切到原生 UI。

**Spike 验证（2026-08-04）**：用 `claude -p --full-autonomy` 在一个真实但可控的 Python 任务（JSON 持久化 + 容错 + 测试）上全自主跑通，**独立复验 7 测全绿、代码干净**——证明 headless 质量**不是**杀手（至少对清晰有界任务）。结论：统一 UI 方向 viable，用户的直觉对。

**核心设计决策（避免四不像 + 绕开 omnigent 的坑）：**
1. **译宝是统一入口，不是 agent 调度器。** 用户显式选 agent（v1 只 Claude Code）；**绝不搞 auto-routing**（omnigent 的 open bug 全在路由层——绕开）。
2. **复用官方 SDK，不 wrap CLI。** dispatch 层用 `claude-agent-sdk`（MIT，官方，流式 + 工具/权限控制），不是包 `claude -p`。
3. **插件形态**（`plugins/coding/`），不污染底座；复用译宝 webview 面板 + 流式事件通道。
4. **诚实边界**：清晰有界任务（加函数/修 bug/补测试）→ 插件发光；大型含糊任务 → 兜底提示"这件回原生交互搞"，不硬撑。

## 2. 复用栈（造 vs 复用）

| 层 | 决策 | 理由 |
|---|---|---|
| **agent dispatch** | **复用 `claude-agent-sdk`** | 官方 MIT 库，`ClaudeSDKClient` + `ClaudeAgentOptions(cwd, permission_mode, allowed_tools)` + `receive_response()` 流式结构化消息；自带 CLI；不用自己包进程/解析 |
| **diff / 代码渲染** | **复用 Monaco diff editor** | MIT，Vue 可嵌，渲染 agent 的文件改动 |
| **多 agent 平台项目** | **SKIP** | omnigent 等是整产品非库，且路由有 bug |
| **自建** | 插件胶水：面板 UX、项目/agent 选择、流式→面板通道、（v2）session/memory/感知 | 译宝专属，无现成 |

> v1 只接 Claude Code（用 `claude-agent-sdk`）。Codex 等以后加——dispatch 层抽 `AgentRunner` 接口，ClaudeCodeRunner 是其首个实现，CodexRunner 后补（OpenAI 侧用对应 SDK 或 CLI）。

## 3. v1 范围与边界

**做（v1）：**
- 一个 webview coding 聊天面板（输入框 + 流式输出 + 文件改动 diff）。
- 用户**显式选项目（cwd）+ 选 agent（v1 仅 Claude Code）**。
- 后端用 `claude-agent-sdk` 全自主跑（`permission_mode="acceptEdits"`），流式回显。
- 渲染 agent 的 assistant 文本、工具调用、**文件编辑 diff**（Monaco）。
- 任务起停（启动 / 中断）。

**不做（v1，留 v2+）：**
- auto-routing（自动选 agent）——明确排除。
- 多 agent（Codex/Codebuddy 接入）——留接口，不实装。
- 跨 agent 交接 / 活动时间线 / 机构记忆（感知+mem0 那套）—— substrate 预留，出口 v2。
- 替代原生 UI 的全部能力（plan mode/subagent/复杂审批流等）——不追平，含糊任务兜底回原生。

## 4. 架构

### 4.1 插件结构（`plugins/coding/`）

```
plugins/coding/
├── manifest.toml     # id=coding; capabilities=["db","process","llm"]; 记忆命名空间; sessions 表
├── api.toml          # 面板可调方法白名单
├── tools/
│   ├── runner.py     # AgentRunner 接口 + ClaudeCodeRunner(claude_agent_sdk)
│   └── coding.py     # run_coding_task / stop_coding_task tool（流式 emit）
└── panel/
    └── coding.html   # webview 聊天面板（Monaco diff）
```

- **capabilities**：`process`（SDK 起 CLI 子进程）、`db`（sessions 落库）、`llm`（v2 摘要用）。**不**要 `host`（v1 不做感知交接）。
- `[[table]] sessions`：id / project(cwd) / agent / prompt / status / started_at / finished_at / token_cost。

### 4.2 dispatch（`tools/runner.py`）

```python
class AgentRunner(Protocol):
    async def run(self, prompt: str, cwd: str, *, on_event) -> None: ...

class ClaudeCodeRunner(AgentRunner):
    async def run(self, prompt, cwd, *, on_event):
        from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
        opts = ClaudeAgentOptions(
            cwd=cwd,
            permission_mode="acceptEdits",     # 文件编辑自动批（全自主）
            allowed_tools=["Read","Write","Edit","Bash","Glob","Grep"],  # 显式收口
        )
        async with ClaudeSDKClient(options=opts) as c:
            await c.query(prompt)
            async for msg in c.receive_response():
                on_event(msg)    # 结构化：assistant text / tool_use / tool_result
```

- `on_event` 把 SDK 的结构化消息转成译宝事件、经 `ctx.emit_event` 流式推给面板。
- **runner 在独立线程/任务跑**（不阻塞 IPC），中断 = 取消该任务（SDK 支持 cancel）。
- 全程 try/except：SDK 崩/超时 → 记 session 失败 + 面板报错，**绝不拖垮主链路**（与 agents 插件同纪律）。

### 4.3 流式 → 面板通道

复用译宝既有流式事件模式（主 chat 的 `final_reply_chunk` 同款）：
- runner 把 SDK 消息归一成 `coding_event`（kind: `text_delta` / `tool_use` / `file_edit` / `done` / `error`），`ctx.emit_event` 出。
- Rust 桥 `brain-event` 转发（既有通道，无需新桥）。
- webview 面板订阅 `coding_event`：`text_delta`→追加聊天气泡；`file_edit`→Monaco diff 卡片；`done`→收尾 + session 落库。

### 4.4 面板（`panel/coding.html`，webview）

- 顶栏：**项目选择器**（默认当前/最近项目，可切 cwd）+ **agent 选择器**（v1 只有 Claude Code，置灰 Codex 等"敬请期待"）。
- 主区：聊天气泡流（用户 prompt + agent 流式回复 + 工具调用折叠）+ **文件改动 diff 卡片**（Monaco，可展开）。
- 输入框 + 「发送 / 中断」。
- 起一个新会话即一个 session 行；切项目=新 session。

## 5. 交互流（用户 / 译宝 / agent）

```
用户在 coding 面板：选项目 ~/myproj + agent Claude Code + 输入「给 taskstore 加 JSON 持久化和测试」→ 发送
   ▼  IPC: coding.run_coding_task(prompt, cwd, agent)
译宝 sidecar：建 session 行；ClaudeCodeRunner 起任务（claude_agent_sdk, cwd=~/myproj, acceptEdits）
   ▼  SDK 流式 receive_response()
译宝 emit coding_event(text_delta/tool_use/file_edit) → brain-event → 面板实时渲染
   ▼  agent 跑完
面板：done + diff 卡片；session 落库（status=ok）
用户：看 diff → 满意保留 / 不满意「中断」或改 prompt 重来
```

**中断**：用户点中断 → IPC `coding.stop_coding_task(session_id)` → 取消 runner 任务（SDK cancel）→ session 标 interrupted。

**含糊大任务兜底**：v1 不自动判定；用户自己判断"这件太复杂"→ 关面板回原生 Claude Code 交互。spec 不承诺替代原生全场景。

## 6. 与 `agents` 插件的关系（不重复）

- `agents` 插件 = **后台 fire-and-forget dispatch**（派 CLI 子进程、完成播报、不流式）。
- `coding` 插件 = **交互流式 coding 聊天**（SDK 流式 + diff 面板）。
- 两者**互补不重叠**。共享一个 `AgentRunner` 抽象（v2 可让 agents 插件也走 SDK）。v1 各自独立，不过早合并。

## 7. 可扩展 substrate（为 v2 预留）

v1 的 `AgentRunner` 接口 + `sessions` 表 + 流式事件通道 = substrate：
- **多 agent**：加 `CodexRunner` 等（实现 AgentRunner）+ agent 选择器激活。
- **交接/记忆（v2）**：session 落库后，runner 的输出可喂 mem0（跨 agent 记忆）；感知（host 能力）检测前台 agent 切换 → 主动交接（参谋长那一套，作为 v2 增强）。
- **活动时间线**：sessions 表 + Feed 复用 → 一个面板看各 agent 干了啥。

v1 不实装这些，但结构不挡路。

## 8. 风险与约束

- **SDK 版本耦合**：`claude-agent-sdk` 在 0.2.x（fast-moving）。pin 版本；SDK 破性变更 → runner 适配层吸收（不扩散到面板/IPC）。
- **流式 → webview 通道**：译宝 webview 面板 + 流式事件需确认能稳定接收高频 chunk（复用主 chat 流式已验证的模式；若 webview 面板订阅事件有缺口，本 spec 落地时先打通这条）。
- **成本**：全自主 headless 高频跑烧 token。v1 给 session 记 token_cost 可见；不限流（个人工具），但面板显示消耗。
- **权限/安全**：`permission_mode="acceptEdits"` + `allowed_tools` 显式收口 = agent 能改 cwd 内文件、跑 Bash。**cwd 必须用户显式选**（不默认任意目录）；Bash 风险由 agent 自身闸门管（译宝侧 L3 确认可加：启动会话前确认 cwd）。
- **诚实边界**：不承诺大型含糊任务质量；UI 不追平原生全功能。

## 9. 测试与验收

**自动化（sidecar pytest，FakeRunner 注入）：**
- `AgentRunner` 接口 + `ClaudeCodeRunner`：用 fake SDK client（造结构化消息流）验证 on_event 归一、流式 emit、中断取消、异常隔离。
- sessions 落库 / 状态流转（running→ok/interrupted/failed）。
- tool 闸门（cwd 必填、agent 白名单）。
- **不**在单测里跑真 SDK（贵且不稳）。

**真机验收（留人工）：**
1. 选一个真实项目 cwd + Claude Code，提一个清晰任务（如 spike 那种加持久化+测试）→ 面板流式回显 + diff → 跑通、结果可用。
2. 中断生效（点中断→session interrupted、agent 停）。
3. 大含糊任务：人工判断兜底回原生（不报错即可）。
4. session 列表/历史可查。

**Spike 已证**：headless Claude Code 在清晰任务上质量可靠（7 测全绿，独立复验）。真机验收复用同型任务。

## 10. 明确不做（本轮）

- auto-routing、多 agent 实装、跨 agent 交接/记忆/感知、活动时间线、替代原生全功能、签名公证/分发。
