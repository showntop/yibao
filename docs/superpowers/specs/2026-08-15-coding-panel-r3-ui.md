# Coding 面板 R3：UI 渲染基座 + 交互规范打磨

- 日期：2026-08-15
- 依据：`docs/research/2026-08-15-coding-agent-competitive.md`（竞品对照，截图/issues 实证）
- 拍板：范围=全量一次做完；coding 线定位=多 agent 控制台（roadmap 方向，R3 不涉及）
- 工作分支：`feat/coding-r3`（worktree `.worktrees/feat-coding-r3`）

## 背景与问题

coding 面板（`plugins/coding/panel/chat.html`，1528 行手写 vanilla）跑在 iframe sandbox + srcdoc，CSP `default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'`。核心短板（竞品对照实证）：

1. AI 气泡**纯 textContent 渲染**，无 markdown、无代码高亮——「看着廉价」的最大来源。
2. 工具卡/结果卡片形态落后品类规范（竞品一致收敛：默认折叠单行 = 图标+工具名+意图摘要+结果计数）。
3. 审批卡是通用「批准」，竞品最优解是动态动词（"Run Command"）+ 等待/执行视觉分态。
4. 运行状态无形（cline 无声挂起 issue 霸榜的教训：状态必须可见+可取消）。
5. 成本不透明（竞品：会话级聚合一行）。
6. 长输出不折叠、错误直接上消息流（holaOS 反例证明：失败步骤应降噪到 turn 级状态条）。
7. 输入区信息组织乱（竞品：三段式 = 输入框 → 上下文/agent 行 → 快捷键行，键白动作灰；占位文案即教学）。

## 目标（验收标准）

### A. 渲染基座
- A1. vendor 三库落地 `plugins/coding/panel/vendor/`：`marked`（GFM）、`dompurify`、`highlight.js`（common 包），精确版本锁定，许可证头部注释保留，`vendor/README.md` 记录版本/来源/许可证。
- A2. sidecar `_load_panels`（`sidecar/src/yibao_brain/plugins.py:389` 附近）支持占位注释内联：html 中 `<!--inject:vendor/xxx.js-->` 被替换为对应文件内容；无占位符的面板（tools.html / editor.html / gen 面板）原样透传；vendor 内容中字面 `</script` 转义为 `<\/script`；文件缺失时保留占位注释并 log 告警，不炸加载。
- A3. AI 气泡 markdown 渲染：marked(GFM, breaks) → DOMPurify 消毒 → highlight.js 代码高亮；流式期间节流重渲染（≤150ms/次 + rAF 对齐），`done` 后终渲一次；代码块带语言标签 + 复制按钮；表格/列表/标题/引用/行内码全样式化。
- A4. 用户气泡保持纯文本（命令式短文本不需要 markdown）。
- A5. 中文正文用系统无衬线，仅代码/pre 用 mono（opencode 全 mono 反例）。

### B. 工具卡与错误降噪
- B1. 工具卡默认折叠为单行：chevron + 工具图标 + 工具名 + **意图摘要**（从 input 提炼：Grep→`"pattern"`、Read→路径、Bash→命令截断 60 字、Edit/Write→路径），执行完追加结果计数（如 `(18 matches)` / `+24/-3` 已有 diff 统计沿用）；点击展开详情（现状 populate 逻辑保留）。
- B2. 长 tool_result 折叠：`… +N lines` 单行，点击展开（Emdash 形态）。
- B3. 错误不上消息流：`error` 事件渲染到底部 turn 级错误状态条（红底细条，可点开展开详情）；绝不渲染协议 XML/原始堆栈到气泡（Claudia `<tool_use_error>` 反例）。
- B4. 工具失败在单行卡上以红色 ✗ 态呈现（不新增卡片）。

### C. 审批与状态
- C1. 审批卡动态动词主按钮：按工具映射（Bash→「运行命令」、Edit/MultiEdit/Write→「保存修改」、WebFetch→「抓取网页」…默认「允许」），绿色实心主按钮 + 灰色「拒绝」次按钮，双按钮占满一行（Cline 形态）。
- C2. 等待审批 vs 执行中视觉分态：等待审批 = 琥珀色脉冲边框卡；执行中 = 普通工具卡（emdash#531 教训）。
- C3. 运行状态改为**底部居中悬浮 pill**：spinner + 已耗时（秒表沿用） + 累计 token + 红色 Stop（Claudia 形态）；不占消息流空间。
- C4. 顶栏右侧加**会话成本聚合**：`N tok · $X.XXXX`，从 `done` 事件的 usage（`_runner.py:104-115` 已推 `{duration_ms, cost_usd, input_tokens, output_tokens}`）前端 live 累加；历史恢复后不补（接受降级）。
- C5. 完成行保留（耗时/token/cost 明细在 done 行），样式收敛为灰小字一行。

### D. 输入区与排版
- D1. 输入区三段式重构：① 输入框（2px 左竖条 accent）② 上下文行（cwd 药丸移入此行 + mode pill + @ 提示）③ 快捷键提示行（**按键白、动作灰**：`↵ 发送` `⇧↵ 换行` `@ 文件` `esc 停止`）。
- D2. 占位文案即教学：`输入消息，@ 引用文件，/ 命令…`。
- D3. 全量排版 token 打磨：消息间距节奏、卡片圆角/描边统一（对齐 :root token）、气泡最大宽度、时间戳灰小字规范。
- D4. 顶栏收窄：cwd 药丸移走后只留标题 + 成本聚合 + 历史/新会话 ghost 按钮。

## 非目标（明确不做）

- checkpoint 三选恢复（SDK 仅 `rewind_files`，无「仅对话」机制）——保持现有单档 ⏪。
- 多 agent UI / Best-of-N / 会话看板（定位方向已定，R4 另行规划）。
- 面板缩放/字号设置（痛点#9，后续单独做）。
- WebviewPanel.vue / Rust 侧改动（注入机制全部在 sidecar）。
- gen 面板（LLM 生成）不支持 vendor 注入（本就不该引第三方库）。

## 技术约束

- CSP `script-src 'unsafe-inline'`：所有 JS 必须内联；vendor 经 sidecar 占位注入。
- 所有用户/模型内容渲染前必须 esc 或 DOMPurify 消毒（安全底线不动）。
- 测试基线：sidecar `uv run pytest -q` 全绿；`npx vue-tsc --noEmit && npx vite build`；`cargo check`。
- sidecar 无 pytest-asyncio（测试同步函数 + asyncio.run）；仓内无前端单测框架——前端改动靠真机验收。
- chat.html 单文件会涨到 ~2000 行 + vendor ~250KB，接受（srcdoc 一次性加载，流式 panel_data 不带 html）。

## 风险

- 流式 markdown 重渲染性能：节流 + 仅重渲当前气泡；长消息（>50KB）退化为纯文本流式 + done 后终渲。
- vendor 三库合计 ~250KB 使 panel 事件变大：panel 打开才推 html，可接受。
- highlight.js common 包语言误判：代码块有语言标签才高亮，无标签不高亮（避免乱染色）。
