# Coding 面板 R3 实施计划

- Spec：`docs/superpowers/specs/2026-08-15-coding-panel-r3-ui.md`（验收标准 A1-D4 以此为准）
- 分支：`feat/coding-r3`（worktree `.worktrees/feat-coding-r3`）
- 执行：subagent 驱动——每任务 implementer + reviewer；台账 `.superpowers/sdd/progress.md`
- 关键约束：chat.html 单文件，任务全部串行（并行必撞车）

## Task 1：vendor 三库 + sidecar 占位注入

**改动**：
- 新增 `plugins/coding/panel/vendor/`：`marked.min.js`、`dompurify.min.js`、`highlight.min.js`（npm 官方 registry `npm pack <pkg>@<latest stable>` 取 minified UMD 构建，验证挂载全局 `marked`/`DOMPurify`/`hljs`；保留许可证头注释；版本精确锁定）。
- 新增 `plugins/coding/panel/vendor/README.md`：三库版本/来源 URL/许可证/用途。
- `sidecar/src/yibao_brain/plugins.py` `_load_panels`（:389 读 text 后）：扫描 `<!--inject:vendor/xxx.js-->` 占位注释 → 替换为 `<插件根>/panel/vendor/xxx.js` 内容；内容中 `</script` → `<\/script` 转义；无占位符原样透传；文件缺失保留占位 + log 告警。

**测试**（`sidecar/tests/test_plugins_inject.py` 新建）：
- 占位符被替换且内容含标志性全局名
- `</script` 转义生效
- 无占位符 html 原样透传
- 缺失文件：占位保留 + 不抛异常
- 现有插件加载回归（`_load_panels` 对 tools.html/editor.html 无影响）

**验证**：`cd sidecar && uv run pytest -q` 全绿。

## Task 2：chat.html markdown 渲染基座（A3-A5）

**改动**（仅 `plugins/coding/panel/chat.html`）：
- `<head>` 后 vendor 占位：三个 `<!--inject:vendor/xxx.min.js-->` 各包一层 `<script>`。
- `appendTextToBubble`（:552）改造：气泡内文本累积到 `dataset.raw`，渲染 = marked.parse(GFM/breaks) → DOMPurify.sanitize → innerHTML → 代码块 hljs 高亮（仅有语言标签的 fence）。
- 流式节流：rAF + 最小间隔 150ms 合并重渲当前气泡；`done`/`stopped` 后终渲。
- 长消息护栏：raw > 50KB 时流式期纯文本，`done` 后终渲 markdown。
- 代码块样式：深色块 + 语言标签 + 复制按钮（点击复制原文，✓ 反馈）；行内码、表格、列表、标题、引用块样式。
- 字体：正文系统无衬线，pre/code 用 --mono。
- 降级：marked/DOMPurify 未加载（vendor 缺失）时退回现有纯文本行为，不报错。

**自测**：implementer 手写一段含标题/列表/代码块/表格/行内码的模拟流式文本走渲染路径（临时探针，验证后移除）。

## Task 3：工具卡单行折叠 + 长输出折叠 + 错误降噪（B1-B4）

**改动**（chat.html）：
- `appendToolUse`（:748）/ `populateToolUseBody`：默认折叠单行 = chevron + 工具图标（现有/新增 emoji 映射） + 工具名 + 意图摘要（`summarizeTool(tool, input)`：Bash→命令截 60 字、Read/Grep/Glob→路径或 pattern、Edit/Write/MultiEdit→路径、WebFetch→URL、Task→description、其它→首参数截断）；`tool_result` 到达后单行尾追加结果计数（Grep matches 数、Read 行数、diff 统计沿用现有）；点击头行展开/折叠（现有 toggle 逻辑保留）。
- `appendToolResult`（:818）：>8 行的输出折叠为 `… +N lines` 单行 + 点击展开；is_error 时整卡红 ✗ 态。
- `appendError`（:573）改为 turn 级错误条：底部固定红底细条（错误摘要 + 「详情」展开/收起），不再插红气泡进消息流；`error` 事件里如含协议 XML/堆栈，只进详情区。
- 工具失败：对应单行工具卡标红 ✗（不新增卡片）。

## Task 4：审批动态动词 + 状态分态 + 悬浮 pill + 成本聚合（C1-C5）

**改动**（chat.html）：
- `appendPermission`（:825）：主按钮文案按工具映射（Bash「运行命令」/ Edit、MultiEdit、Write「保存修改」/ WebFetch「抓取网页」/ WebSearch「联网搜索」/ 默认「允许」）；绿实心主按钮 + 灰「拒绝」次按钮，双按钮通栏；等待中卡片琥珀色脉冲边框，`permission_done` 后收敛为单行记录（「✓ 已允许 xxx」灰字，可保留现有记录形态但降噪）。
- 执行中工具卡区分态：spinner（现状）与等待审批的琥珀态明确不同。
- `setStatus*`/ticker（:476-503）改为底部居中悬浮 pill：spinner + 秒表 + 累计 token + 红 Stop（复用现有 stop 逻辑 :993+）；运行结束 pill 消失，完成行（setStatusDone :849）保留为灰小字。
- 顶栏右侧成本聚合：`N tok · $X.XXXX`，`done` 事件 usage 累加到会话级变量；newChat/resumeSession 清零或按会话恢复（前端内存即可）。

## Task 5：输入区三段式 + 排版打磨（D1-D4）

**改动**（chat.html）：
- `<footer>`（:411-423）重构三段：① 输入框（2px 左竖条 accent 边）② 上下文行：cwd 药丸（从顶栏 :398-407 迁入）+ mode pill（:417 迁入）+ @files 提示 ③ 快捷键行：键白动作灰（`↵ 发送` `⇧↵ 换行` `@ 文件` `esc 停止`）。
- 输入框占位文案：`输入消息，@ 引用文件…`。
- 顶栏收窄：留 标题 + 成本聚合 + 历史/新会话按钮。
- 全量排版 token 打磨：消息间距节奏、气泡 max-width、卡片圆角/描边统一、时间戳/元信息灰小字规范。
- @ 补全（:1419-1486）与历史抽屉（:1334-1396）功能不破坏，样式随 token 对齐。

## Task 6：终审 + 修复波

- 独立 reviewer 通读全部 diff（对照 spec A1-D4 逐项核）；修复波处理 Major/Minor。
- 基线：`uv run pytest -q` 全绿；chat.html 无前端编译管线，人工核 vendor 注入后 srcdoc 渲染（真机验收取决于用户）。

## 验收（用户真机）

- AI 回复 markdown（标题/列表/代码高亮/表格）+ 流式不闪烁
- 工具卡默认单行、点击展开、结果计数；长输出 +N 折叠
- 审批卡动词按钮（运行命令/保存修改）+ 琥珀等待态
- 悬浮 pill（耗时/token/Stop）+ 顶栏成本聚合
- 输入区三段式 + 快捷键行
- 回归：⌘⇧U/I、diff 卡、rewind、历史抽屉、@ 补全、📁 选目录（8de331e 修复后首验）
