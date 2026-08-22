# 代码库重构计划（全库 Review）

> 日期：2026-08-22
> 分支：`refactor/review-plan`（worktree：`../yibao-refactor`）
> 范围：`app/`（Tauri 前端 Vue3+TS 与 Rust 后端）、`sidecar/`（Python 大脑服务）、`plugins/`（Python/TS 插件）、`mobile/`（Capacitor 移动端）、`extension/`（Chrome 扩展）
> 原则：单一职责、单一事实源、依赖方向统一、可测试性、渐进式重构（每步行为不变、可独立合入）

## 执行状态（2026-08-22 二次复核修订）

> **完成口径**：标「✅ 完成」须同时满足 *任务描述的架构目标落地* + *验收标准对应项达成*；仅行数下降、结构未建者标「🟡 部分完成」并注明剩余项（对应收口任务）。首轮状态表存在的「降级交付记完成」问题已按此口径修正。
>
> **复核基线（全绿）**：app vitest 241 / sidecar pytest 1142 / Rust cargo test 11 / mobile vitest 83；`vue-tsc --noEmit` 零错误。Rust 编译环境已修复（`resources/bin/uv` 软链就位）。

| 状态 | 任务 |
|---|---|
| ✅ 完成 | R-00 基线；R-01 time.ts；R-02 truncate；R-03 icons；R-04（保留多窗口入口）；R-05 sidecar log()（print 残留 1 处，随下次 sidecar 改动顺手清）；R-06 extension 去重；R-07 命名清理；R-16 loop.py run 委托 arun；R-16b home-assembly.ts 拆分（assembly/ + composables/useAssembly）；R-22 Rust 测试移出源码；R-26 http_client.py 统一；R-28 CSS 体系（scrollbar.css/settings.css/home-feed.css） |
| ✅ 完成 | R-09 SettingsView（1751→80 行容器 + `settings/` 4 子组件）；R-10 HomeFeed（1550→230 行容器 + `feed/` 3 子组件）；R-11 HomeChat（useChatFlow 气泡域）与 HomeContextPanel（useContextApprovals，script 522→222） |
| ✅ 完成 | R-14 brain.ts 拆分（protocol/brain-types + services/brainClient + state/pending，brain.ts 余 8 行兼容 re-export）；R-17 统一错误处理；R-18 状态真源（`_pc` 迁 state/pending，零残留）；R-19 lib 去 Vue 耦合（lib/ 100% 无 Vue import）；R-23 mobile api/http.ts 收敛；R-24 StreamLike 唯一化；R-27 配置评估维持现状（显式决策，有记录） |
| ✅ 完成 | **R-12b lib.rs 分层收口（2026-08-22）**：lib.rs 1584→535 行（纯装配：run/setup/托盘/热键/命令注册）；按 3.2 落地三域——`braind.rs`（633 行：BrainState/spawn/桥/看门狗/退避/ensure_runtime + restart_brain/clear_brain_data）、`setup_config.rs`（146 行：.env 合并 + get/save 命令）、`system.rs`（262 行：热区穿透 + 窗口尺寸命令 + 划词 CGEvent）；invoke_tests 3 例迁 `tests/system.rs`；cargo test 25 全绿零警告 |
| 🟡 部分完成 | **R-13 server.py**：stdio 协议已拆 `transport.py` ✅；`serve_async` 巨闭包（315 行起）+ msg 分发 if/elif 巨链（~1230-1594）仍在。方案已定：ServerRuntime 类化 + handlers 查表，全计划风险最高项，独立会话执行 |
| 🟡 部分完成 | **R-15 coding.py**：1687→1095，已拆出 sessions.py / transcript.py / _brief.py / _cc_reader.py 等 ✅；继续按技能域收尾 |
| ✅ 完成 | **R-20 invoke 收敛（收尾 2026-08-22）**：App.vue/HomeChat.vue 的 `get_setup_config`（含泛型形式共 3 处）、`ensure_active_conversation`×2、`ensure_pet_conversation`、`get_conversation_messages`、`list_plugins`×2、`expand_chat`、`set_pet_expanded`、`hide_invoke_bar`、`close_home_window` 全部改走 brainClient（新增 expandChat/setPetExpanded/closeHomeWindow/hideInvokeBar/ensureActiveConversation/ensurePetConversation/getConversationMessages/listPlugins 封装 + PetMessage 入 protocol）；顺带修复 setupCfg snake/camel 映射 bug（baseUrl 此前恒为 undefined）。**显式豁免**：WebviewPanel.vue iframe 原生桥白名单（注释声明的设计，非大脑通信） |
| 🟡 部分完成 | **R-21 Rust 通信契约化**：`brain_cmd`/`brain_cmd_with` 收敛 9 处单行调用 ✅；强类型 CommandKind 枚举未做，commands.rs 仍余 write_to_brain 25 处字符串分发。剩余项 → **R-21b** |
| ✅ 完成 | **R-25 跨端协议契约（含 R-29 收口 2026-08-22）**：protocol-contract.md（通道/kind 对照/命名映射/漂移清单）+ **app 内部单源化**——RunMetrics 唯一定义于 protocol/brain-types.ts（state/types.ts re-export 兼容）、AvatarState（7 态共享集）单源（Home/HomeFrame/HomePlugins/PanelApp/usePetState/useHomeChatSession 全部引用）；双端类型显式决策「各自维护 + 契约文档对照 + 变更同步纪律」（protocol-contract.md §3.2.1） |
| ⬜ 未开始 | **R-08 App.vue 拆分**（1924→1711，仅 pet 域 composables 抽取减 213 行；组件级拆分未动）；补账任务余 **R-12b / R-21b / R-30 / R-31**（见阶段 5） |

⚠️ **提交纪律（立即生效）**：当前工作区堆积 73 文件 +779/−9196 未提交改动，已违反「每项任务独立可合入、可回滚」原则。**任何后续任务开工前，先按域分批提交现有改动**（重构提交与 feat/fix 提交分开，不混一个 commit）；此后每完成一项任务立即 commit。

---

## 1. 概述

本次 Review 覆盖全库约 30+ 万字符源码（排除编译产物与 `node_modules`），发现的问题集中在四类：

1. **大文件 / 上帝组件**：多个 500~2600 行的文件同时承担 5+ 互不相干的职责。
2. **重复代码 / 冗余抽象**：类型重复定义、工具函数各写各的、同一请求/错误处理样板重复 8~35 次。
3. **命名不规范**：单字母变量泛滥、camelCase/snake_case 跨边界混用、拼音缩写 CSS 类名、语义不清的缩写。
4. **分层不明确**：通信层与 UI 耦合、纯逻辑文件耦合 Vue 运行时、状态真源（source of truth）多套并存、测试内联在源码文件。

### 1.1 各子项目规模概览

| 子项目 | 技术栈 | 源文件数 | 最大文件（行数） |
|---|---|---|---|
| `app/src` | Vue3 + TS | 70 ts + 45 vue | `App.vue` 1924、`SettingsView.vue` 1751、`HomeFeed.vue` 1550 |
| `app/src-tauri` | Rust (Tauri 2) | 5 个源文件 | `lib.rs` 2594 |
| `sidecar/src` | Python (asyncio) | 43 py | `server.py` 1696 |
| `plugins/*` | Python + TS + Vue | 33 py + 32 ts + 19 vue | `coding/skills/coding.py` 1687 |
| `mobile/src` | Vue3 + TS + Capacitor | 26 ts + 10 vue | `StationView.vue` 637、`session.ts` 485 |
| `extension` | Chrome MV3 JS | 4 js + 2 html | 全部 < 100 行 |

### 1.2 问题优先级分级

- **P0 高收益低风险**：提取公共工具、删除死代码、统一命名，行为不变，可随改随合。
- **P1 结构性拆分**：上帝文件/组件按职责拆分，行为不变，拆一个验证一个。
- **P2 分层收敛**：通信层/状态层/领域层解耦，消除多套并行状态。
- **P3 架构统一**：跨端协议契约、错误处理框架、状态管理统一。

---

## 2. 问题详单

### 2.1 app 前端（Vue3 + TS）

#### 2.1.1 大文件

| 文件 | 行数 | 混杂职责 |
|---|---|---|
| `app/src/App.vue` | 1924 | 桌宠小窗根 + Avatar 状态机 + 气泡流 + 审批确认 + 插件启动器 + 语音/打断 + 划词截图唤起 + 窗口热区 + 深链恢复 + Setup 向导，`<script setup>` 内 18 个 unlisten 事件监听 |
| `app/src/components/SettingsView.vue` | 1751 | 外观/模型/语音/快捷键/搜索/浏览器扩展/手机伴生/健康节律/感知开关/系统权限/统计 8+ 个互不相干的设置域 |
| `app/src/components/HomeFeed.vue` | 1550 | 问候条 + 时间线 + 待批收件箱 + 插件 widget + coding 会话路由 + 误报反馈 |
| `app/src/components/HomePlugins.vue` | 1238 | 插件列表 + 面板宿主（SchemaPanel/WebviewPanel）+ 动画 + 工作台条 |
| `app/src/components/HomeChat.vue` | 1172 | 气泡流 + 会话恢复 + 技能 chips + 权限 + 跨窗镜面 + 过程行 |
| `app/src/components/HomeContextPanel.vue` | 1143 | 上下文面板 + 过程行时间线 + 快批 |
| `app/src/lib/brain.ts` | 1018 | 类型库 + 事件订阅 + 待批队列状态机（`_pc`）+ 8+ 个 `XxxOnce` 样板 |
| `app/src/components/PanelApp.vue` | 933 | 面板窗根 + SchemaPanel 宿主 + 输入条 |
| `app/src/Home.vue` | 871 | 大窗根 + 主题 + surface 裁决 + ⌘K 命令面板 + 全局快捷键 |
| `app/src/lib/home-assembly.ts` | 859 | 零件目录 + 4 套预设常量 + 吸附算法 + 布局解析，且 `import { ref } from "vue"` 耦合运行时 |
| `app/src/components/InputBar.vue` | 831 | 输入 + 附件 + @ 引用 + 麦克风 |

#### 2.1.2 重复代码

- **类型重复定义（无单一事实源）**：
  - `RunMetrics`：`lib/brain.ts:70-80` 与 `state/types.ts:50-58` 字段完全一致。
  - `AvatarState`：在 `App.vue:58`、`Home.vue:27`、`PanelApp.vue:53`、`HomeFrame.vue:23`、`HomePlugins.vue:31` 各定义一份且枚举成员不一致。
  - `RunRef`：`lib/home-chat-session.ts:16` 与 `state/types.ts:43`。
  - 会话消息类型 `BubbleMsg`/`ProcProjection`/`MessagePayload` 两套并存。
- **工具函数各写各的**：
  - 手写 `padStart(2,"0")` 时间格式化 ≥4 处（`HomeFeed.vue`、`SessionList.vue:96`、`DataView.vue:137`、`lib/schema.ts:88`）。
  - 相对时间/时长格式化散落 5+ 组件（`fmtHours`/`relativeTime`/`elapsedSince`/`UsageBar.vue`）。
  - 文本截断 `slice(0,N)+"…"` ≥4 处（`App.vue:561`、`HomeChat.vue`、`HomeFeed.vue:384`、`DataView.vue:65`），而 `lib/proc.ts:14` 已有 `truncate(s,n)` 未复用。
  - 单行化 `replace(/\s+/g," ").trim()` 3 处。
  - `djb2`/`ICON_PALETTE`/`initial` 图标逻辑在 `App.vue:441-460`、`HomePlugins.vue:155-172`、`QuickPanel.vue:57` 三份。
- **组件逻辑复制**：面板事件 → surface 裁决在 `App.vue`/`Home.vue`/`HomePlugins.vue` 各实现一遍；待批准确认 UI 在 `App.vue:1316` 与 `HomeFeed.vue:600` 各自独立实现。
- **样式重复**：`.segmented` 分段控件两处；`s-*` 设置样式 `DataView.vue` 整份复制 `SettingsView.vue`（源码注释自认）；`::-webkit-scrollbar` 美化多文件重复。

#### 2.1.3 命名问题

- 单字母变量泛滥：`brain.ts:647/705` 的 `n`、`work-thread.ts` 的 `b`、`home-assembly.ts` 的 `d`、`schema.ts` 的 `n/d` 等，属常态。
- camelCase 与 snake_case 混用：`BrainEvent` 内 `conversationId` 与 `task_id`/`exit_code` 并存（`brain.ts:99-110`）；`state/types.ts` 中 `ConversationMeta.createdAt` camel 与 `MessagePayload` camel 混在同一个协议体系。
- 语义不清：`MessagePayload.panelLink`；`work-thread.ts` 中 `talkBeats/talkTurns/paperStamps/spine` 等英文命名承载中文产品隐喻，注释与代码语言割裂。
- CSS 类名拼音/中文隐喻缩写：`pcard`、`pl-*`、`tl-*`、`rd-*`、`ap-*`、`m-*`、`s-*`、`wb-*`，可读性依赖脑内映射。
- `brain.ts` 的 `XxxOnce` 系列（`getFeedOnce`/`getWidgetsOnce`/`getMemListOnce`/`getSettingsOnce`/`getHttpPairInfoOnce`…）API 形态不统一（有的返回 Promise、有的 fire-and-forget）。

#### 2.1.4 分层与组织

- **通信层与 UI 耦合**：Tauri `invoke` 被组件直调（`HomeChat.vue:921`、`App.vue:318`、`Home.vue:394`、`InputBar.vue`），绕过 `lib/brain.ts` 封装；错误处理各写各的（静默 `.catch(()=>{})` / `pushWarn` / 局部回滚）。
- **无统一错误处理**：`SettingsView.vue` 有 `autonErr/watchErr/searchErr/ttsErr/mobileErr/perceptionErr` 6 个错误 ref，文案高度相似「设置未生效（大脑不在线？）」重复 10+ 遍。
- **纯工具层耦合 Vue 运行时**：`lib/home-assembly.ts` 顶层 `import { ref } from "vue"`；`lib/` 30 个文件混入 UI 概念函数（`work-thread.ts`、`home-desk-presence.ts`）与状态（`home-chrome.ts` 用 `ref`）。
- **状态真源多套并存**：`App.vue`/`HomeChat.vue` 各自维护 `bubbles`/`pendingConfirms`，与 `brain.ts` 模块级 `_pc` 队列、`state/` conversation 域三套并存。
- **入口碎片**：根目录遗留 `home.ts`/`panel.ts`/`snip.ts`/`invoke.ts` 与 `main.ts`、`lib/`、`state/` 并存，疑似废弃入口。
- **模块级副作用**：`brain.ts:674-719` 被 import 即执行 `listen(...)`，有隐性时序风险。

### 2.2 app Rust 后端（src-tauri）

#### 2.2.1 大文件

| 文件 | 行数 | 混杂职责 |
|---|---|---|
| `app/src-tauri/src/lib.rs` | 2594 | ~80 个 Tauri command + sidecar 守护（spawn/watchdog/backoff）+ 配置管理 + 前端桥事件转发（30+ `app.emit` 分支）+ 窗口管理 + 系统集成（剪贴板/CGEvent/热键/托盘）+ 插件 manifest 手写解析 + 全局静态状态 + 内联测试 |
| `app/src-tauri/src/session_db.rs` | 542 | SQLite 会话存储 + 145 行测试内联 |
| `app/src-tauri/src/event_recorder.rs` | 452 | 事件落库状态机 + 185 行测试内联（实现与测试近 1:1） |
| `app/src-tauri/src/plugin_proto.rs` | 241 | 相对单一 |

#### 2.2.2 重复代码

- **`write_to_brain` 样板约 35 处**：`run_input`/`get_feed`/`distill_now`/`mem_delete`/`set_settings`/`voice_start`/`dock_list`… 全部是 `write_to_brain(&state, serde_json::json!({...}))` 单行体，仅靠 `"type"` 字符串区分，字段名直接暴露给 sidecar（如 `perception_delete` 传 `"per_id"`），无类型约束，改一处易漏。
- **会话 DB 访问样板约 15 处**：重复「锁 state → `session_db.as_ref()` → 空则 Ok(None)」模式。
- 多个 `if let Some(win) = app.get_window(...)` + 相同 `set_focus`/`show` 窗口操作重复。

#### 2.2.3 命名与分层

- 命名整体符合 Rust 惯例，但 `lib.rs` 无任何模块化分层：commands、进程守护、配置、窗口、系统集成、插件解析全部平铺在一个文件；模块内注释分段代替模块边界。
- 测试 `#[cfg(test)] mod tests` 内联在源码文件，`event_recorder.rs` 测试代码接近实现代码量。
- 缺少强类型请求/响应契约：sidecar 通信靠字符串 `"type"` 分发。

### 2.3 sidecar（Python 大脑）

#### 2.3.1 大文件

| 文件 | 行数 | 混杂职责 |
|---|---|---|
| `sidecar/src/yibao_brain/server.py` | 1696 | stdio 协议（ReadMsg/WriteMsg）+ HTTP 路由 + 业务编排（`serve_async` 内 100+ 嵌套闭包）+ 确认闸门 + feed 构建 + 记忆 + 提醒 + 感知线程管理 + push 注册 |
| `perception.py` | 1025 | 感知加密存储 `PerceptionStore` + 两个 Skill + 采样线程 |
| `voice.py` | 953 | STT + 录音(VAD) + TTS（edge/cosyvoice/dashscope 多 provider） |
| `plugins.py` | 688 | 插件加载器 + 声明式 tool（db/http/prompt/composite 四类）+ 面板注册 |
| `loop.py` | 654 | `AgentLoop` 同步 `run` 与异步 `arun` 两条近乎重复的实现 |
| `llm.py` | 609 | LLM provider 抽象 + 视觉 client + 多个 vision 辅助函数 |
| `skills_real.py` | 545 | 8 个真实原子技能 |
| `distiller.py` | 514 | `DistillerStore` 存储 + `Distiller` 编排 + 纯函数三合一 |
| `skills_composite.py` | 449 | 5 个复合技能 + HTTP 抓取工具 |
| `http_api.py` | 427 | 独立 aiohttp 面（与 server.py 内 HTTP 代码职责重叠） |

#### 2.3.2 重复代码

- **`_sibling()` 在 3 个文件重复定义**：`plugins/agents/skills/agents.py:34`、`plugins/agents/skills/sandbox.py:30`、`plugins/coding/skills/coding.py:37`。
- **HTTP 客户端各写各的**：`skills_composite.py`（`_fetch` 系列）、`plugins.py`（`HttpClient` 类）、`zimeiti/hot_topics.py`（`_fetch_json`）、`zimeiti/mat_save.py`（`_fetch_text`），全部基于 urllib，无统一封装。
- **日志不统一**：`print(f"[yibao]...")` 在 18 个模块中重复（`server.py` 39 次、`distiller` 12 次…），未使用 `logging`。
- **`loop.py` 双实现**：同步 `run` 与异步 `arun` 几乎同样逻辑两套。

#### 2.3.3 命名与分层

- Python 侧命名整体良好（snake_case），少量单字母参数（`m/t/r/s/c/f/b64`）。
- TS/Vue 侧：`StationView.vue` 637 行、`session.ts` 485 行、`session.test.ts` 796 行（测试比实现还大）。
- `server.py` 单函数 `serve_async` 承担全部编排（100+ 嵌套闭包），不可测试、不可复用。
- 配置访问散落：各模块直接 `import config` 读函数/常量，无统一配置对象注入。
- 测试组织良好：`tests/test_<模块>.py` 与 src 一一对应，`fakes.py`/`conftest.py` 共享；但 `test_server.py` 132KB 与 `server.py` 同样膨胀。

### 2.4 plugins

- `plugins/coding/skills/coding.py` 1687 行：单文件承载全部 coding 技能（编辑/搜索/代码执行等），与 `_runner.py`/`_codex_runner.py` 的职责边界不清（两个 runner 并存）。
- `plugins/agents/skills/agents.py` 394 行 + `sandbox.py` 263 行：与 coding 插件重复 `_sibling`、runner/reader 逻辑。
- TS 侧缩写命名：`curAgent/curSessAgent/dstate/deps/refs/opts`。

### 2.5 mobile（Capacitor 移动端）

- 文件规模整体健康（最大 `StationView.vue` 637、`session.ts` 485），但存在与 app **双端重复类型**：`FeedItem/FeedStats/RunningTask`（`state/feed.ts:5-32` vs `app/src/lib/brain.ts:326-357`）、`PendingConfirm`、`MemoryItem`，字段命名风格（snake vs camel）都不一致，手工维护易漂移。
- **请求样板重复**：`useFeed/useMemories/useReminders/useSessions/useApprovals` 各自重复「fetch → error → loading → finally」。
- **状态管理**：跨组件共享靠模块级单例 `ref`（如 `pending-badge.ts`），缺 Pinia/provide-inject；`Chat.vue` 多层 `ref` 解包耦合紧。
- **事件订阅生命周期样板重复**：多个页面重复 `onMounted → start / onUnmounted → stop` + `disposed` 竞态守卫。
- 命名小瑕疵：`chat.ts` 的 `mine`（语义不清）、`uuid()`（通用名）、两处重复的 `StreamLike` 接口。

### 2.6 extension（Chrome MV3）

- 全部文件 < 100 行，但 **`background.js:34` 与 `popup.js:17-21` 错误判定逻辑完全重复**（`Cannot access`/`Cannot script` 判定 + 相同文案），应下沉 `shared.js`。
- `shared.js:16-29` 的 `saveToYibao` 与 `options.js:18-28` 的测试连接各自手写 `fetch` + header + 错误文案。
- 命名：`$`、`t`、`conf`、`ok` 等缩写，无类型（JS 可接受，建议补 JSDoc）。

---

## 3. 目标架构

### 3.1 前端（app/src）目标分层

```
app/src/
├── main.ts                 # 仅装配（createApp + boot）
├── windows/                # 窗口级根（App.vue → pet/PetWindow.vue；Home.vue → home/HomeWindow.vue；PanelApp.vue → panel/PanelWindow.vue）
├── views/                  # 页面级（home/FeedView、PluginsView、DataView、SettingsView 拆子面板、chat/ChatView）
├── components/
│   ├── pet/                # 桌宠：Avatar、Bubble、StatusBadge
│   ├── chat/               # BubbleFlow、InputBar、SessionList、ProcessRail
│   ├── panel/              # SchemaPanel、WebviewPanelHost、PanelDock
│   └── common/             # Segmented、Modal、EmptyState、ScrollBar 样式
├── composables/            # useBrainEvents、usePendingConfirms、useClock、usePanelSurface
├── protocol/               # 与 sidecar 的契约：事件 kind 枚举 + payload 类型（唯一事实源）
├── services/               # brainClient（IPC 封装 + onceWithTimeout 工具 + 统一错误）
├── lib/                    # 纯函数（不 import vue）
│   ├── time.ts / text.ts / icons.ts      # 通用工具
│   ├── assembly/           # 主屏装配域（由 home-assembly.ts 拆出）
│   │   ├── parts.ts        # 零件目录 HOME_PARTS + 注册表 PARTS + registerPart/syncPluginParts/resetPluginParts（插件零件注册）
│   │   ├── widgets.ts      # 零件显隐/大小/材质配置（由 home-widgets.ts 迁入，去 Vue 化）
│   │   ├── faces.ts        # 零件 faces 外观映射（home-glance-faces.ts）
│   │   ├── presets.ts      # 4 套预设 rails/desk/salon/canvas + 几何常量（SPINE_W/NOTE_W/TILE/RAIL）
│   │   ├── snap.ts         # 吸附算法（snapToGuides/snapAxis/snapBox/settleSnap）
│   │   └── layout.ts       # 解析与渲染几何（resolveGrid/resolveAssembly/gridStageStyle/frameStyle…）
│   ├── surface/            # pet-surface、surface-policy
│   └── …（其余纯逻辑，如 proc/schema/at-mention）
├── composables/            # useBrainEvents、usePendingConfirms、useClock、useAssembly（零件/装配响应式状态，吸收 livePluginIds）
├── state/                  # SessionStore domains（+ 新增 pending/bubbles 域）
└── assets/                 # tokens.css
```

关键约束：`lib/`、`protocol/`、`services/` 不得 import Vue 运行时；组件只负责呈现与事件订阅，业务编排进 composables/services；`views/` 只做组装。

### 3.2 Rust 后端目标分层

```
src-tauri/src/
├── lib.rs                # 仅装配：生成器 + setup + 注册 commands
├── commands/             # 薄层：参数解析 + 调 service，mod.rs 统一导出
├── bridge/               # brain 通信：send_to_brain(CommandRequest) + 事件转发器
├── services/             # braind（spawn/watchdog/backoff）、config、plugins（manifest）、windows
├── domain/               # 请求/响应契约（强类型枚举 CommandKind + payload），与 protocol/ 对齐
└── storage/              # session repository（迁移 + 数据访问）
```

测试移入 `src-tauri/tests/`，源码文件不再内联大段 `#[cfg(test)]`。

### 3.3 sidecar（Python）目标分层

```
sidecar/src/yibao_brain/
├── __main__.py           # 入口组装
├── transport/            # stdio 协议（ReadMsg/WriteMsg）+ HTTP 挂载
├── router/               # kind → handler 路由表（替代 serve_async 巨型函数）
├── handlers/             # run/feed/memory/reminders/perception/… 各自模块
├── services/             # loop、distiller、perception、voice、plugins_loader
├── store/                # DistillerStore、PerceptionStore、MemoryStore…
├── llm/                  # provider 抽象、vision、prompts
└── common/               # logging 统一、errors、http_client、config
```

### 3.4 跨端协议契约（P3）

app（Tauri IPC）与 mobile/extension（HTTP `/v1/*`）共用同一大脑，但**事件 kind 与数据形状相同、类型定义双份手写**。建议新增 `shared/protocol/`（TypeScript 类型 + 命名规范映射表 snake→camel），由 `docs/` 中维护协议 schema 文档，两端 import 或生成，消除漂移。

---

## 4. 重构任务清单

> 编号规则：`R-<序号>`。每项含：目标 / 动作 / 涉及文件 / 风险 / 验收。

### 阶段 0：基线（1~2 天）

| 编号 | 任务 | 说明 |
|---|---|---|
| R-00 | 建立回归基线 | 跑通现有测试：`app`（vitest）、`sidecar`（pytest 63 文件）、Rust `cargo test`；记录覆盖率基线；确认 CI 或本地命令。所有后续任务合入前必须全绿。 |

### 阶段 1：P0 低风险清理（可随改随合）

| 编号 | 任务 | 目标 / 动作 | 涉及文件 |
|---|---|---|---|
| R-01 | 抽取 `lib/time.ts` | 统一 HH:MM、相对时间、时长格式化；替换 `HomeFeed.vue`/`SessionList.vue`/`DataView.vue`/`schema.ts` 等 ≥4 处手写 `padStart` 与 5+ 处相对时间实现 | app/src 前端 |
| R-02 | 复用 `truncate` | 用 `lib/proc.ts` 现有 `truncate` 替换 4 处 `slice(0,N)+"…"` 与单行化逻辑；补齐导出 | app/src |
| R-03 | 抽取 `lib/icons.ts` | 收敛 `djb2`/`ICON_PALETTE`/`initial` 三份实现（App.vue、HomePlugins.vue、QuickPanel.vue） | app/src |
| R-04 | 入口文件核实与清理 | **修正**：`home.ts`/`panel.ts`/`snip.ts`/`invoke.ts` 是 Tauri 多窗口正式入口（`vite.config.ts` 多页配置 + 各 `.html` 引用），**保留**；任务改为核实各入口文件内无死代码/冗余导入 | app/src |
| R-05 | 统一日志 | sidecar 用 `logging` 替代 `print(f"[yibao]…")`（18 模块），保留行为 | sidecar |
| R-06 | extension 去重 | 错误判定逻辑下沉 `shared.js`；统一 `fetch` 封装（`request` 函数）；补 JSDoc | extension |
| R-07 | 命名清理（安全项） | 单字母/缩写局部变量改语义化命名（`b`→`bubble`、`d`→`distance`、`n`→`items` 等），仅限无行为影响的局部重命名；`XxxOnce` 统一为明确语义（见 R-14） | app/src、mobile |

### 阶段 2：P1 大文件拆分（行为不变，拆一验一）

| 编号 | 任务 | 目标 / 动作 | 涉及文件 |
|---|---|---|---|
| R-08 | 拆 `App.vue`（1924） | 按功能域抽子组件：`pet/PetWindow.vue`（壳）、`Avatar.vue`（已有）、气泡流 → `chat/BubbleFlow.vue`、审批确认 → `composables/usePendingConfirms.ts` + 组件、插件启动器 → `components/PluginLauncher.vue`、唤起/热区 → composables；根组件只留装配与路由 | app/src |
| R-09 | 拆 `SettingsView.vue`（1751） | 按设置域拆 8 个子组件：`AppearanceSection`/`ModelSection`/`VoiceSection`/`ShortcutSection`/`CompanionSection`/`PermissionsSection`/`DataSection`/`PerceptionSection`；共享 `s-*` 样式抽 `assets/settings.css` | app/src |
| R-10 | 拆 `HomeFeed.vue`（1550） | 拆 `FeedTimeline`/`InboxView`/`PluginWidgets`/`RecapView`；审批卡与 App.vue 的审批 UI 合并复用 | app/src |
| R-11 | 拆 `HomeChat.vue`（1172）与 `HomeContextPanel.vue`（1143） | 气泡/过程行/技能 chips 抽 `chat/` 组件；跨窗镜面逻辑进 composables | app/src |
| R-12 | 拆 `lib.rs`（2594） | 按 3.2 分层：`commands/`（按域拆 5~6 个模块）、`services/brain`（spawn/watchdog/backoff）、`services/config`、`services/windows`、`domain/`（强类型请求枚举）；commands 层保留薄壳 | app/src-tauri |
| R-13 | 拆 `server.py`（1696） | `serve_async` 拆为 `router/`（kind→handler 表）+ `handlers/*.py`；stdio 协议入 `transport/`；HTTP 路由职责移入 `http_api.py` 统一 | sidecar |
| R-14 | 拆 `brain.ts`（1018） | 拆为 `protocol/types.ts`（类型，唯一事实源）+ `services/brainClient.ts`（IPC + `onceWithTimeout` 通用工具替代 8+ `XxxOnce` 样板）+ `state/pending.ts`（`_pc` 队列状态机迁入 state 域） | app/src |
| R-15 | 拆 `coding.py`（1687） | 按技能域拆模块（edit/search/run/…），统一 `_runner` 与 `_codex_runner` 为一个 runner 抽象；合并 `_sibling` 到插件公共工具 | plugins/coding |
| R-16 | 拆 `loop.py`（654）双实现 | 同步 `run` 改为薄封装调用唯一异步实现，或抽公共协程 | sidecar |
| R-16b | 拆 `home-assembly.ts`（859）——零件独立成域 | 按 3.1 拆为 `assembly/parts.ts`（零件目录+注册表+插件零件注册）、`presets.ts`（4 套预设+几何常量）、`snap.ts`（吸附算法）、`layout.ts`（解析/渲染几何）；`livePluginIds` 等响应式状态迁入 `composables/useAssembly.ts`，`home-chrome.ts` 收编为装配编排入口 | app/src/lib |

### 阶段 3：P2 分层收敛

| 编号 | 任务 | 目标 / 动作 | 涉及文件 |
|---|---|---|---|
| R-17 | 统一错误处理 | `brainClient` 增加统一错误类型（`BrainOffline`/`Timeout`/`Rejected`）与 fallback；收敛 SettingsView 6 个错误 ref → 一个 composable + 统一文案；组件内 `.catch(()=>{})` 改为显式处理 | app/src |
| R-18 | 收敛状态真源 | 待批队列、bubbles 迁入 `state/` domains；`brain.ts` 模块级 `_pc` 移除；明确唯一真源 | app/src |
| R-19 | 切断 lib 对 Vue 的耦合 | R-16b 拆出后，`home-chrome.ts`/`home-widgets.ts` 残留的 `ref`/`reactive` 状态全部上移到 composables/state，lib/ 保持纯函数 | app/src |
| R-20 | 组件直调 invoke 收敛 | 所有组件改走 `brainClient`；禁止绕过 | app/src |
| R-21 | Rust 通信契约化 | `write_to_brain` 样板 35 处替换为 `send_to_brain(CommandKind::X, payload)` 强类型枚举；DB 样板抽 `with_db(state, f)` 助手 | app/src-tauri |
| R-22 | 测试移出源码 | `session_db.rs`/`event_recorder.rs` 内联测试迁至 `src-tauri/tests/` | app/src-tauri |
| R-23 | mobile 请求收敛 | 请求样板收敛到 `api/` 层统一封装；事件流订阅抽象公共 hook（`useEventStream`） | mobile |
| R-24 | mobile 状态管理 | 模块级单例 ref → provide/inject 或 Pinia；消除 `StreamLike` 重复接口 | mobile |

### 阶段 4：P3 架构统一

| 编号 | 任务 | 目标 / 动作 | 涉及文件 |
|---|---|---|---|
| R-25 | 跨端协议契约 | 建立 `shared/protocol`（事件 kind + payload 类型 + snake/camel 映射）；app/mobile/extension 引用同一来源；消除 FeedItem/PendingConfirm/MemoryItem/RunMetrics 双份漂移 | app、mobile、sidecar |
| R-26 | HTTP 客户端统一（Python） | 抽 `common/http_client.py`，替换 skills_composite/plugins/zimeiti 四处 urllib 封装 | sidecar |
| R-27 | 配置统一注入 | sidecar 各模块改为注入 config 对象，去掉散落的 `import config` 直接读 | sidecar |
| R-28 | CSS 体系收敛 | 抽取公共样式（scrollbar、segmented、`s-*` settings、`--yb-*` token 完整清单）；组件样式瘦身，只留差异 | app/src |

### 阶段 5：补账与收口（2026-08-22 复核新增）

> 来源：全库复核发现的两类缺口——(1) 首轮执行的降级交付（R-12/R-21/R-25 记完成但架构目标未落地）；(2) 原任务清单与第 3 节蓝图、问题详单之间的映射缺口（HomePlugins、AvatarState 等列出问题却无任务承接）。
> 原则：**架构实质项优先，物理形态项（R-31）最后**。区分标准：依赖方向/单一事实源/职责分离/强类型契约 = 架构实质；目录命名与位置 = 物理形态，纯机械、零行为变化，等逻辑拆分全部完成后再一次性做，避免路径变更叠加进逻辑 diff。

| 编号 | 任务 | 目标 / 动作 | 涉及文件 | 优先级 |
|---|---|---|---|---|
| R-12b | lib.rs 分层收口（R-12 剩余） | 按 3.2 落地：sidecar 守护域（spawn_brain/spawn_bridge/spawn_watchdog/restart_with_backoff/on_brain_down/boot_brain/ensure_runtime）→ `services/`；配置域（read_env_file/merged_env/get_setup_config/save_setup_config/emit_setup）→ 独立模块；窗口/热区/剪贴板系统集成（set_main_size/expand_chat/spawn_click_through/grab_selected_text/pb_* 等）→ 独立模块；lib.rs 收敛为装配（run + setup + 命令注册）。每拆一域 `cargo test` | app/src-tauri | 高（架构实质） |
| R-21b | Rust 通信强类型化（R-21 剩余） | 二选一并记录决策：(a) write_to_brain 25 处 → `CommandKind` 强类型枚举 + payload 结构体（domain/），与 protocol-contract.md 对齐；(b) 显式接受「字符串分发集中在 write_to_brain 单点 + brain_cmd 辅助」现状，同步修订第 7 节验收标准第 6 条 | app/src-tauri | 中 |
| R-29 | 类型单源化（R-25 剩余 + 原清单遗漏） | `RunMetrics` 二合一（state/types.ts 改 re-export protocol/brain-types）；`AvatarState` 4 处定义收敛到 protocol/——**注意枚举成员不一致**（Home.vue/HomeFrame.vue 7 态含 success/error vs HomePlugins.vue/PanelApp.vue 5 态），统一前先裁决缺态组件是否需补齐；`FeedItem`/`PendingConfirm` app 与 mobile 双端收敛方案落地，或显式记录「双端各自维护 + protocol-contract.md 对照」决策 | app/src、mobile/src | 高 |
| R-30 | 拆 HomePlugins.vue（1210） | 问题详单 2.1.1 列为上帝组件（插件列表 + 面板宿主 + 动画 + 工作台条）但原清单漏配任务：按域拆子组件，面板宿主与 PanelApp 的宿主逻辑评估复用 | app/src | 中 |
| R-31 | 目录重组收口（物理形态，最后做） | 纯机械 mv + import 修正，单独 PR、零行为变化：App.vue/Home.vue/PanelApp.vue → `windows/{pet,home,panel}/`；components 平铺 47 文件四分 `pet/chat/panel/common`（settings/feed 保持）；Rust commands.rs → `commands/` 目录（可并入 R-12b 顺势做）；lib/ 的 pet-surface/surface-policy → `lib/surface/`。**前置：R-08/R-12b/R-13/R-15 全部完成后执行**，否则路径变更会叠加进后续逻辑拆分 diff | app/src、app/src-tauri | 低（视觉一致） |

---

## 5. 执行路线图

```
阶段 0 基线 ──→ 阶段 1 低风险清理（R-01~R-07，约 3~5 天）
     │
     ├────→ 阶段 2 大文件拆分（R-08~R-16，约 2~3 周，拆一验一，并行度受限）
     │         每拆一个文件：先建回归基线（现有测试 + 手动冒烟），合入单独 PR
     │
     ├────→ 阶段 3 分层收敛（R-17~R-24，依赖阶段 2 的 brain.ts / lib.rs / server.py 拆分）
     │
     ├────→ 阶段 4 架构统一（R-25~R-28，可提前并行准备 protocol 文档）
     │
     └────→ 阶段 5 补账与收口（R-12b/R-21b/R-29/R-30 → 最后 R-31 物理重组）
              ⚠️ 前置：先分批提交当前工作区堆积改动，恢复可回滚性
```

依赖关系：
- R-14（拆 brain.ts）是 R-17/R-18/R-20 的前置。
- R-12（拆 lib.rs）是 R-21/R-22 的前置。
- R-13（拆 server.py）是 R-26/R-27 的前置。
- R-25（协议契约）建议与 R-14 的 `protocol/` 提取合并推进，避免二次搬运。
- R-12b 承接 R-12 已交付部分（守护/配置/系统集成分域续拆）；R-21b、R-29 分别是 R-21、R-25 的收口。
- **R-31 是 R-08/R-12b/R-13/R-15 的后置**（逻辑拆分全部完成后才做物理重组）；R-29 依赖 R-14 的 protocol/ 已就位，可立即做。
- R-20 收尾（4 处直调改走 brainClient）无前置，可随时插入。

每阶段完成标准：全测试绿 + 无新增 lint 错误 + 行为 diff 为零（重构不改变功能）。

---

## 6. 风险与注意事项

1. **行为保持**：所有 P0/P1 任务必须行为不变。涉及 UI 的拆分以「截图冒烟对比」验收；涉及协议的以「事件流抓包前后一致」验收。
2. **状态迁移风险**：R-18 把 `_pc` 队列迁入 state 时，注意 `App.vue` 与 `HomeFeed.vue` 是不同窗口（小窗/大窗），需验证跨窗一致性与持久化。
3. **`brain.ts` 模块级副作用**（R-14 前置）：先确认 listen 初始化时序，拆 services 时保留同序初始化，避免事件丢失。
4. **Rust 拆分体量**：`lib.rs` 2594 行含 80 commands，建议按域分批（每批 15~20 个命令），每批保持 `#[tauri::command]` 注册表更新并 `cargo test`。
5. **`server.py` 的 `serve_async` 巨型闭包**：拆分前先用测试锁定行为（`test_server.py` 已较全），拆 handler 时按 kind 逐个迁移。
6. **不要顺手做功能改动**：重构期间发现的小 bug 记录到独立 issue，不在重构 PR 中夹带。
7. **CSS 拆分注意 scoped 语义**：组件样式抽出时保留 scoped 作用域，避免样式泄漏。

---

## 7. 验收标准（2026-08-22 复核标注现状）

- [x] 全库（app/sidecar/plugins/mobile/extension）测试通过，无 lint error。（241/1142/11/83 全绿，vue-tsc 零错误）
- [ ] 所有 >1000 行的源文件拆分完毕（前端组件、`lib.rs`、`server.py`、`coding.py`、`brain.ts`）。（brain.ts ✅ 8 行；余 App.vue 1711、HomePlugins.vue 1210、server.py 1671、coding.py 1095 → R-08/R-30/R-13/R-15）
- [ ] 跨端共享类型单一事实源（无 `RunMetrics`/`FeedItem` 等双份定义）。（→ R-29；`AvatarState` 4 份且枚举不一致同属此项）
- [x] 前端 `lib/` 与 `protocol/` 不再 import Vue 运行时。（复核确认零残留）
- [ ] 组件不再直接 `invoke`，统一走 brainClient。（余 4 处：App.vue:143、HomeChat.vue:209、WebviewPanel.vue:104、Home.vue:394 等 → R-20 收尾）
- [ ] Rust 侧无字符串 type 分发，全部强类型枚举。（write_to_brain 余 25 处 → R-21b 二选一决策）
- [ ] sidecar 无 `print` 日志（统一 logging），无重复 HTTP 封装。（print 余 1 处；HTTP 封装 ✅）
- [ ] 命名规范落地：无拼音命名、无单字母局部变量、跨边界命名映射表维护在协议文档。（映射表 ✅ protocol-contract.md；其余渐进达成）
- [ ] 每项任务独立可合入，可回滚（git revert）。（⚠️ 当前工作区 73 文件未提交，违反本项——分批提交为第一优先动作）
- [x] （新增）执行状态表与代码现状账实一致：完成口径 = 架构目标落地 + 验收项达成，降级交付必须标「部分完成」并指向收口任务。（本表即按此修订）
