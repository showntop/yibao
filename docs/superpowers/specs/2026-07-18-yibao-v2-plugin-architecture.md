# 译宝 v2 方案：通用底座 + 垂直插件

日期：2026-07-18　状态：已定稿（终审待决项见 §12）

## 0. 定位与设计原则

**定位**：本地常驻 AI 伙伴 agent。通用底座 + 垂直插件，自媒体垂直主打。用户对话为主，GUI 操作为辅（垂直插件面板）。不是 app builder 平台。

**设计原则**（决策冲突时以此为准）：

1. 别过早实现，但留对抽象
2. 代码 vs Agent：确定步骤代码固化，模型只在「该判断/该生成」处出手
3. 先场景后抽象
4. 底座-插件单向依赖（底座不感知插件）
5. 风险闸门贯穿（L0–L4）
6. 记忆与数据按插件隔离
7. LLM 不生成宿主可执行代码（唯一例外④档 code-exec，必须沙箱）
8. 能确定就确定，少用 agent

## 1. 核心概念

- **Tool**：一个可被调用的动作 = function call 声明 + `run()` 执行 + 风险闸门 + 审计。function call 只是调用协议（LLM 只产 name+args，不执行），Tool 比它大。
- **Skill**（预留）：能力包 = 指令 + 工具集 + 参考资料，对齐 Anthropic Skill，后续阶段再引入。
- **两个正交维度**：过 agent = 要不要 LLM 判断/生成（智能维度）；过 gate = 要不要用户确认（安全维度）。由插件作者设计时声明，底座强制执行 + 有副作用必过 gate。
- **三层边界别混**：过壳（IPC 传输层）≠ 过 sidecar（进程边界，所有后端动作在此执行）≠ 过 LLM（只是 sidecar 内一条分支，只有该判断/生成的动作才走）。

## 2. 架构分层

- **底座**：常驻壳 / 对话+语音 / 操作（a11y+视觉）/ mem0 / 风险闸门 / 审计 / 插件运行时 / ToolInvoker / schema 引擎 / webview 运行时沙箱
- **插件**：工具组 + 领域记忆 + 数据 + 面板（工作流 = 复合 tool；定时触发器后续再说）

## 3. 插件机制

### 3.1 结构

```
plugins/<name>/
├── manifest.toml   # id / capabilities / 记忆命名空间 / 表结构+schema_version / 面板声明 / min_engine_version
├── tools/          # Python tool（代码插件）或声明式 tool
├── api.toml        # 面板可调方法白名单（见 §7）
└── panel/          # schema 定义 或 webview HTML
```

**代码与数据分离**：用户数据统一放应用数据目录（`~/Library/Application Support/yibao/`），按插件 id 分目录；插件目录只放代码与声明。删插件才删数据，升级不动数据。

### 3.2 加载与执行

- sidecar 启动扫描 → 校验 manifest → 建表/迁移 → 注册 tool/面板
- 单插件失败隔离：标记 disabled、告知用户，不拖垮底座
- **同进程执行**：插件 tool 在 sidecar 线程池跑；安全由闸门+审计管，不由进程边界管。进程沙箱只留给④档。热加载不做，守护重启生效
- 命名空间强制：插件 tool 必须带 `<plugin>.` 前缀，底座 id 保留
- ctx.db 每插件单连接 + 写锁，序列化访问

### 3.3 权限模型（capabilities）

manifest 声明、启用时类手机 App 展示、ctx 按授权注入：

| capability | 得到什么 |
|---|---|
| `db` | ctx.db（scope 到本插件） |
| `memory` | ctx.memory（scope 到本插件命名空间） |
| `http` | ctx.http |
| `llm` | ctx.llm（插件内调主模型） |
| `host` | ctx.host（截图/键鼠/a11y，最危险，默认不给） |

没声明的能力 ctx 里不存在。

### 3.4 插件分两档

- **声明式插件（agent 可自助创建）**：tool 不写代码，manifest 声明四种类型——`db` / `http` / `prompt` / `composite`。无代码 → 无需安全扫描。服务长尾个人插件。
- **代码插件（必须人工审核）**：tool 是 Python。真实边界是人工审核，静态扫描只是 lint 辅助，不声称安全边界。自媒体主垂直属此档。

## 4. ToolInvoker：一个动作，两个入口

后端动作统一抽象为 tool；**ToolInvoker** 是 tool 的唯一执行器（查 registry → capability 校验 → 闸门 → 执行 → 审计 → 结果按来源路由）：

```
对话入口：用户说话 → LLM 选 tool+参数 → 闸门 → ToolInvoker → tool.run
面板入口：面板 action(direct) → api.toml 白名单 → 闸门 → ToolInvoker → tool.run
```

tool 抽象的边界：

| 面板上的事 | 归谁 |
|---|---|
| 过滤/排序/翻页/表单校验 | 前端引擎，不出进程 |
| 增删改查/状态流转/拉数据 | direct tool |
| 要 LLM 生成/判断 | 走 agent 的 tool |
| 长任务进度 | tool + 事件推送（v1 简化为 loading；任务队列后续排） |
| 跨插件编排 | agent，不是单个 tool 的职责 |

**安全交互一套**：确认/报错统一由壳层渲染，面板与对话只是触发源，不各造一套。

## 5. 可见性与面板呼出

**解耦两个概念**：

- **面板激活 = UI 焦点**：一次只展示一个插件面板
- **tool 暴露 = LLM 上下文**：底座常驻 + 当前面板插件全量 + 各插件 `global=true` 少量 + LLM 可用底座 tool `use_plugin` 按需展开任意插件
- 用户无需知道「激活」，跨插件请求由 LLM 路由

**面板呼出三触发源**：

| 触发 | 例子 | 过 LLM？ |
|---|---|---|
| 用户明确要 | 「打开获客看板」/ 托盘 / 热键 | 对话触发要；托盘/热键不要 |
| **tool 结果驱动** | 查完「上周联系过的线索」自动弹看板 | 查询要，弹板不要 |
| 面板内导航 | 点某条线索 → 详情视图 | 不要 |

结果驱动的实现：`ActionResult` 带可选 `panel` 字段（tool 作者声明结果用哪个面板看），ToolInvoker 见到 → 发 panel 事件 → 壳打开面板 + 注入数据；同时对话流出一条文字摘要（留存记录 + 语音场景）。

**面板生命周期**：首屏数据随触发注入（不空启动）→ 驻留（本地操作前端处理，数据操作 direct 刷新）→ 被动刷新（同面板已开只更新数据）→ 关闭/切换。

**导航与取数拆开**：视图切换纯前端（schema 引擎切 view / JS 路由）；数据已够就直接渲染，要补查走 api.toml 白名单 direct 调用。

**过不过 LLM 的判断**：自然语言 → 结构化查询参数这一步要 LLM（理解意图，原则 2 前半）；查询执行是纯代码（原则 2 后半）。参数已结构化（面板筛选器/快捷按钮）就永远不过 LLM。

## 6. 面板前端 ↔ Python/数据

**schema 面板（无 JS）**：导航声明在 schema 里（`on_item_click: {action, params, target}`），引擎解释执行——视图切换引擎做，取数发白名单调用。没有任何 JS 被生成或执行。

**webview 面板（有 JS）**：沙箱注入唯一桥对象，两个方法：

```js
const lead = await window.yibao.invoke("growth.get_lead", { id });  // 请求-响应
window.yibao.on("growth.sync_done", cb);                            // 订阅推送
```

链路：JS → 壳 IPC → sidecar → api.toml 白名单（方法存在？direct？）→ 闸门 → ToolInvoker → tool → ctx.db → 原路返回 Promise。

约束：JS 只发「方法名+参数」，Python 永不 eval JS 送来的东西；webview 无 Node/fs、CSP 锁死，`window.yibao` 是唯一外部对象；`invoke` 走 `[[method]]` 白名单，`on` 走 `[[event]]` 白名单。

## 7. api.toml 设计

面板的唯一咽喉，管三件事：哪些方法可调、走不走 LLM、能订阅什么事件。

```toml
# 直调方法：不过 LLM
[[method]]
name = "list_leads"           # 面板侧方法名；与 handler 分离保证面板 API 稳定
handler = "growth.list_leads" # 落到哪个 tool（强制插件前缀）
direct = true
# risk 省略：继承 tool 的 default_risk（单一事实源）

[[method]]
name = "delete_lead"
handler = "growth.delete_lead"
direct = true
risk = "L3"                   # 可选覆盖：只许比 tool 自身更高（收紧），不许降低
refresh = "growth.list_leads" # 可选：直调成功后跟一次查询 tool（本插件只读），面板拿刷新数据而非操作回执

# 意图方法：转给 agent，过 LLM
[[method]]
name = "draft_followup"
handler = "growth.send_dm"
direct = false
intent = "给线索 {id} 起草跟进私信，参考最近互动记录"  # 意图模板，面板参数填入

# 面板可订阅的事件白名单
[[event]]
name = "sync_done"
```

规则：

- `risk` 单一事实源在 tool，api.toml 只能收紧，防面板入口成降权后门
- `refresh` 解决「写操作后面板数据过期」：直调成功 → 执行 refresh 指向的本插件只读 tool → 面板事件携带新数据；刷新 tool 意外需要确认则静默跳过（不弹确认打断用户）
- 对话路径同理：manifest `[[tool]] refresh = "list"`（短名自动补插件前缀，点号形式必须本插件前缀，目标必须已注册）。写操作（insert/delete 的 data 是回执 `{"id":…}`）经 LLM 触发时，面板事件同样拿刷新数据——否则「记一下」后面板显示空（2026-07-19 实装）
- `direct=false` 不带 risk：意图经 LLM 展开后以最终 tool 调用的 risk 过闸，确认只弹一次
- 参数 schema 以 tool 声明为准，ToolInvoker 校验，api.toml 不做参数收口
- 对话路径不经过此文件：LLM 入口看 ToolRegistry 可见性，面板入口看 api.toml，两份白名单互不授权

## 8. UI 三层与安全边界

| 层 | 内容 | 生成 | 安全 |
|---|---|---|---|
| 伙伴本体 | 对话/形象/常驻 | 固定产品 UI | — |
| schema 工作台 | 轻面板 | LLM 生成 schema | 白名单组件，未知降级 |
| webview 面板 | 重 UI | LLM 生成 HTML/JS | 沙箱 + `invoke`/`on` 白名单 |

铁律：LLM 不生成宿主可执行代码；本机操作 = tool 白名单 + L0–L4 + 审计。

### 实装记录（webview，2026-07-20）

- **面板类型**：manifest `[[panel]] type = "webview" src = "panel/x.html"`；`_load_panels` 读 HTML 文本存 `_PANELS["pid:name"] = {"type": "webview", "html": …}`（schema 面板仍是 JSON dict，靠 `type=="webview" and "html" in` 区分）。`panel_payload` 对 webview 面板发 `{panel, schema: null, webview: {html}, data}`，schema 面板 payload 不变。
- **沙箱宿主**：`WebviewPanel.vue` 用 `<iframe sandbox="allow-scripts">` + `srcdoc`（禁 allow-same-origin，iframe 内无 Tauri IPC）。桥 JS 由父侧注入到插件 HTML 的 `<head>` 后（须在插件自有脚本前，否则 `window.yibao` 未定义），提供 `yibao.invoke(method, params) → Promise` 与 `yibao.onInit(cb)`。
- **桥协议**（postMessage）：iframe→父 `{src:"yibao-webview", id, method, params}`；父→iframe 回包 `{src:"yibao-host", id, ok, result|error}` + 初始化 `{src:"yibao-host", type:"init", data}`。父侧校验 `event.source === iframe.contentWindow`，并粗筛 method 须以当前面板插件 id 开头（如 `zimeiti.`）；最终裁决仍在 sidecar api.toml 白名单 + L0–L4 闸门（L2 确认条由 PanelApp 闭环）。回包经 `action_result` 的 action.id（`pa_<rid>`）关联；sidecar 拒绝/超时 reject 给 iframe。
- **api.toml `panel` 字段**：`[[method]] panel = "pid:name"`（可选，须指向本插件已声明面板，跨插件/未声明则 method 跳过）。direct 直调成功后 sidecar 用该面板发 panel 事件，覆盖 tool 自带 `result.panel`——用于 `zimeiti.open_editor`（handler 复用 `zimeiti.get`，面板引到 `zimeiti:editor`）与 `save_article`（保存后停在编辑器，回执 data 经桥回包给 iframe，重发的同面板事件不重建 iframe）。
- 首个实例：`plugins/zimeiti/panel/editor.html`（手写模板，单文件无外部依赖，<30KB）。

### 实装记录（设计语言统一，2026-07-22）

- **面板/webview 设计标杆**：`plugins/toolbox/panel/tools.html`——卡片式布局（白卡 14px 圆角 + `0 1px 2px rgba(90,70,50,.04), 0 6px 16px rgba(90,70,50,.05)`）、分段控制器页签、徽章统计、toast 反馈、主/ghost 两级按钮（`#ff8a5c`/`#f2703f` + `#e3d7c4` 边）。新 webview 面板照抄其 `:root` token 块。
- **已对齐**：SchemaPanel（list/board/detail/form 全部卡片化 + 两级按钮）、主对话框（Bubble/InputBar/头部/启动器）、zimeiti 编辑器；`app/src/assets/tokens.css` 关键值已回填（`--yb-text #3f372e`、`--yb-bg #f6f1ea`、默认圆角 14px、新增 `--yb-accent-deep`）。
- **空态规范**：双行结构（主句 600 次要色 + 引导句淡色），引导用户回对话（如「去跟译宝说一句试试」），不硬编码插件名；webview 面板可用 `:placeholder-shown` 纯 CSS 做空态显隐（editor.html 先例）。
- **提醒**：底座技能 `reminder_set/list/cancel`（下划线命名——底座 id 禁点号），`reminders.json` 落盘，serve 调度循环 10s 一拍，到期亮窗 + 气泡 + 空闲 TTS；LLM 时间语义靠 loop 注入的当前时间 system 消息。

### 实装记录（code-exec，2026-07-26）

- **④档落地形态**：agents 插件内 `code_exec` 技能（L3 走确认），LLM 生成脚本（python/node）经 macOS Seatbelt（`sandbox-exec -f policy.sb`）真沙箱执行；任务并入 agents 任务体系（tasks 表加 `kind` 列：`agent`/`script`，additive 迁移自动补列；task_status/task_stop/任务面板对两种任务通用），完成播报复用同一套 `_wait`（抽到 `skills/_common.py`，摘要抽成 callable；沙箱脚本摘要=log 尾部 500 字）。
- **同步短窗口（2026-07-27 修）**：spawn 后同步等 25s——窗口内完成的短任务把输出直接作为工具结果返回（LLM 当轮给用户答案，落库 done、不播报）；窗口装不下的长任务才转 daemon `_wait` 后台播报；超时预算 ≤ 窗口时窗口耗尽即同步杀掉。`_PROCS` 登记在 wait 之前，同步窗口内 task_stop 照样能停。修法前一律「立即返回+播报」，LLM 想马上答只能反复 task_status 轮询，8 步耗尽报「达到最大步数仍未完成」。
- **沙箱边界**：读全放 + 写限根（cwd + 任务目录 + /tmp，全部 realpath 规范化后拼进 profile；/tmp 与 /private/tmp 双写）+ 默认断网。**macOS 15.6 实测坑**：deny-default + process-exec 组合下 file-read 若加 subpath 过滤，dyld 在沙箱内加载被拒直接 SIGABRT——所以 file-read 必须全放，不能加过滤器。读全盘不构成泄露通道：无网络 + 写受限（读到的数据写不出限定目录）+ L3 审批向用户展示完整代码。
- **.git 写保护**：每个写根追加一条 `(deny file-write* (subpath "<根>/.git"))`，脚本改不动仓库历史。
- **可审计**：脚本本体、policy.sb（即实际执行的沙箱规则）、output.log 全部落盘在任务目录（插件数据目录 `sandbox/<task_id>/`）。
- **网络**：v1 仅开关（`network=true` → profile 末尾追加 `(allow network-outbound)`，审批文案展示），域名白名单后续再议。
- 解释器：python 优先 `/usr/bin/python3`（系统 3.9.6，沙箱兼容实测 ok）再 PATH；node 走 PATH；缺哪个报错哪个。非 macOS 无 sandbox-exec → 报「当前环境不支持沙箱执行」。

### 实装记录（过程展示，2026-07-27）

- **技能短标签 `label`**：Skill 基类加 `label = ""` 类属性（中文短名如「运行沙箱脚本」），所有底座/插件技能已填；声明式 tool 从 manifest `[[tool]] label` 读（可选）。`Action.label` 由 invoker.propose 填（回退 skill_id），随 `action_proposed`/`action_result` 事件到前端。description 是长路由文案，不能当标题用。
- **过程气泡行**：工具调用在对话流留痕——`action_proposed` 插一行淡色小字「🔧 标签」（sys/hint 同款调性），`action_result` 原地更新 ✅/❌（失败带 error 摘要，60 字截断）。四个对话场景同款逻辑：宠物窗（App.vue，sys 气泡）、大窗对话页（HomeChat.vue，可点「详情」展开参数 pretty JSON + 结果，各截 800 字）、面板浮窗（PanelApp.vue）与大窗插件页（HomePlugins.vue）（t-row.proc 时间线行）。共享小工具 `app/src/lib/proc.ts`。
- **规则**：`use_plugin` 不插行（成功有 notice 轻提示，重复；失败由 LLM 下一句转告）；`action_result` 无匹配过程行（面板直调只发 result 不发 proposed）不补行，失败仍走原 errorText 细条。思考过程不另做：模型调工具前的文本本就流式进气泡。
- 顺带修：PanelApp.vue 重复 `case "action_result"`（JS switch 首个匹配生效，后一个死代码——「看 PRD」类直调失败无反馈的根因），已合并。

### 实装记录（记忆稳定 + 调度可靠性，2026-07-27）

- **记忆稳定**：mem0ai+fastembed 挪默认依赖（bootstrap 不再 `--extra memory`，防漏装失忆）；Mem0Memory key 前置人话检查 + `custom_instructions` 保中文事实原文进出；LazyMem0 重试 3×2s→5×3s + 报错人话化（qdrant 锁→「记忆库被另一个译宝实例占用」）。
- **委派可靠性（C-1/C-2）**：根因——claude 无沙箱时 `permission_denials` 空跑烧钱；大脑重启后 running 任务成僵尸（`_wait` 线程死、无人落库）。修法：dispatch_task 的 claude 套 Seatbelt（写限 cwd/logs/`~/.claude`、放开网络、`--dangerously-skip-permissions`，policy 落盘 logs/ 可审计；无 sandbox-exec 直接人话拒绝），codex 用内建 `--sandbox workspace-write`；两者 `stdin=DEVNULL`（claude/codex 都读 stdin）；`_find_cli` 在 PATH 找不到时补查 `~/.local/bin` 等（GUI spawn 的 brain PATH 很薄）。tasks 表加 `pid` 列：重启后 `make_tools` 对账——pid 活着挂 `_reap_detached` 探活线程收尾（done + 播报，exit_code 保持 -1 未知），已死落 `interrupted`；task_stop 句柄丢失时按 pid SIGTERM 兜底。坑：测试里僵尸子进程会骗过 `kill(pid,0)` 探活（生产上孤儿被 launchd 收尸无此问题）。
- **precheck 路由纠偏（C-3）**：`Skill.precheck(params) -> str|None`——返回人话原因则拦截（不执行、不弹审批），loop 两条路径在 propose 后 decide 前插入，原因作为 tool 结果回喂让模型换工具重试。比 LLM 自觉读 description 可靠，比风险审批轻。首个用户：dispatch_task 拦「短描述+一次性任务关键词」（统计/转换/整理/计算/格式化/生成文件）指路 code_exec。
- **会话内免确认（C-4）**：确认弹窗加「本会话不再询问这个操作」勾选，批准后同技能本会话免确认：confirm 消息带 `remember` → sidecar 记入会话级集合 → `Gate.session_allowed` 命中即 AUTO（连 confirmation_needed 事件都不发）。只活内存重启失效；拒绝不记忆（防自动拒绝陷阱）。

### 实装记录（主屏，2026-07-27）

- **依据**：`docs/research/2026-07-27-os-feel-design.md` §4.2——大窗落地页从对话页改为「主屏」（问候 + 动态 + 常用），本轮只做主屏页与四页导航；Widget schema 协议、收件箱化、设置增强后置。
- **Feed 存储**：`sidecar/src/yibao_brain/feed.py` FeedStore——append-only SQLite（数据目录 `feed.db`，与审计库同目录），kind ∈ task/reminder/event，meta JSON；**写失败只 print 不抛**（Feed 是增强面，不许拖垮主链路）。
- **记录点**：插件主动事件 `_on_plugin_event` 先落 Feed 再转发壳（ev 带 `task` 键则 kind=task——agents `_wait`/`_reap_detached` 播报已带 `{id,status,label,prompt 截120}` meta）；提醒触发在 `_reminder_loop` 落 reminder。
- **查询协议**：`{"type":"feed","limit":60}` → `{"type":"feed","items":倒序,"stats":{pending_reminders,running_tasks,done_24h}}`；stats 各自容错缺啥补 0，running_tasks 只在 agents `data.db` 已存在时读（不为统计凭空建库）。Rust reader 转发 `brain-feed` 事件 + `get_feed` 命令；前端 `brain.ts getFeedOnce`（once 与 3s 超时竞速，失败兜底空 Feed）。
- **大窗四页化**：Home.vue 加「主屏」默认落地页（NAV 第一位），四页仍 v-show 常驻挂载。新组件 `HomeFeed.vue`：问候条（时段问候 + 日期 + stats chips，全 0 时「今天安安静静」）；动态区（kind 图标 + 两行截断 + 相对时间）；常用 Dock（`list_plugins` → 首字圆图标，点击 `panelAction("<pid>.list")` 开面板，HomePlugins 事件监听自动接管切页）；底部常驻 InputBar（submit 走 `runInput(text,"pet")` 并切对话页）。
- **Feed 点击追问**：动态点击生成**自包含**草稿（`关于任务「label」：`——大脑看不到 Feed 内容，上下文必须前端拼好）→ Home.vue `onFeedChat` 先清空再 nextTick 设回 `chatDraft`（同一条重复点击也触发 watch）→ HomeChat 透传 → InputBar 新 `draft` prop watch 填入并聚焦。

## 9. 数据存储

| 类型 | 存储 | 规则 |
|---|---|---|
| 语义记忆 | mem0，命名空间 `plugin:user` | 只放用户级偏好，**禁双写** |
| 业务数据 | 每插件独立 SQLite（数据目录下） | 权威；跨插件关联 = 快照拷贝，不跨库引用 |
| 二进制素材 | 文件系统，元数据入 SQLite | — |

迁移只做 additive（加表/加列带默认值），manifest 带 `schema_version`，破坏性变更走导出重建。

## 10. 插件创建

- 创建者：阶段 0–1 人工 → 其后 agent 生成声明式 → 远期模板分享
- **agent 生成流程（含自愈）**：生成到暂存区 `_staging/` → agent 自测（自动构造冒烟用例，失败自动修，修不好如实告知）→ 展示 capabilities/表/tool 清单 → 用户点头移入 `plugins/` 启用
- **迭代是一等公民**：改字段走同一流程（additive 迁移 + 重新确认）；运行期报错回流给 agent 触发修复建议
- 配套：插件测试套件 `yibao_brain.testing`（fake ctx 一键构造），阶段 0 交付

## 11. 路线图

| 阶段 | 内容 |
|---|---|
| 0 趟路 | 数据目录分离 + capability 模型 + ToolInvoker + 插件加载 + 测试套件 + schema 协议（3 组件）+ 闪念盘插件（声明式验证全链路） |
| 1 需求磨刀 | forge 插件（代码 tool）：录 → 快筛 → 挑战+竞品扫描 → 裁决（档案+记忆飞轮）→ PRD/HTML 原型；schema 第 4 组件 board + detail actions；guides/ 按需加载 = Skill 能力包雏形（2026-07-19 定：原「自媒体接 AIHOT」取消——无此系统；Agora 集成过重暂缓，仅抄方法论文本） |
| 2 第二垂直 + webview | 反推接口；webview 随首个重 UI 需求引入（候选：原型双向交互、自媒体 Agora） |
| 3 ④档 | code-exec（带沙箱） |

## 12. 终审待决项

1. ~~面板形态：独立浮窗 vs 小窗抽屉？~~ **已定：独立浮窗**（2026-07-19 实装：面板事件 → 开面板窗 + 宠物收球；关闭只 hide 保状态；面板内闭环确认/报错）
2. ~~`use_plugin` 展开未激活插件时，对话里要不要让用户知情？~~ **已定：要知情**（2026-07-20）：展开时回一句轻量提示（如「我打开了 xx 插件」），不弹窗不打断。注：use_plugin 本身尚未实现，当前全量暴露 18 个 tool ≈ 2k tokens/调用（2026-07-20 实测）；tool 数 <40 前不做路由式暴露（原则 1 别过早实现）
3. ~~webview 协议留口时机~~ **已定：阶段 2 随自媒体写作编辑器引入**（2026-07-20）——schema 组件做不动富文本编辑器，这是第一个真实重 UI 需求
4. ~~自媒体第一尖刀~~ **已定：选题+写作**（2026-07-20）——对话流天然契合；剪辑+素材太重，后续阶段再看
5. ~~schema 协议 v1 够吗~~ **已定：够用，继续**（2026-07-20）——阶段 0/1 验证：4 组件（list/detail/form/board）+ bind + 开放 type + 未知降级覆盖了闪念与 forge 全部面板
6. ~~业务数据语义搜索~~ **已定：v1/v2 不做**（2026-07-20）——等真需要「按意思找旧选题」的场景出现再单列底座方案（原则 3 先场景后抽象）
7. ~~MCP 接入算不算 v2 方向~~ **已定：不算**（2026-07-20）——留 v3 再评估；当前先把自有 tool 体系跑顺

### 实装踩坑记录（Tauri 侧）

- **新窗口必须配 capability**：Tauri v2 里窗口无匹配 capability = 无任何 IPC 权限（`listen`/`invoke` 插件与 core 命令全拒，自定义命令除外）。窗口事件订阅需 `core:event:allow-listen/unlisten`，标题栏拖动需 `core:window:allow-start-dragging`（见 `app/src-tauri/capabilities/panel.json`）。
- **事件先发、窗口后开的竞态**：`app.emit` 只送达当时已存在的窗口；后创建的窗口要靠 Rust 侧缓存载荷 + 窗口挂载后 `invoke` 补拉（`get_current_panel`）。


## 附录 A：schema 协议 v1

panel schema 是一个 JSON 文件（manifest `[[panel]] src` 指向），描述面板结构；前端引擎按白名单渲染，**engine version = 1**（`"version": 1`）。

顶层：`{"version": 1, "type": "list" | "detail" | "form" | "board", ...}`。

任意 type 可声明 `back: {label, method, params?}`：面板左上角渲染「‹ 返回」链接，本质是一个 action（走 api.toml 白名单 + 闸门），用于详情 → 看板这类回跳（2026-07-20 实装）。board 的 `columns[]` 可带 `color`（CSS 色值），渲染为标签前的标识色点。

### 四个组件

- **list**：列表。`bind.items` 绑定数组数据；`item` 描述每行：`title` / `subtitle`（可绑定）+ `actions`（行级操作数组）。
- **detail**：详情。`fields: [{label, value}]`，`value` 可绑定；可选 `actions`（操作数组，params 走 `$data` 上下文）。
- **form**：表单。`fields: [{name, label, input: "text" | "textarea" | "number"}]`；`submit` 是一个 action，提交时把表单值并入 params。
- **board**：看板（2026-07-19 随 forge 插件引入）。`bind.items` 绑定数组；`bind.column` 对每行求值得列归属（如 `$item.status`）；`columns: [{key, label}]` 声明列（按顺序渲染，值不匹配任何列的行归入首列，不丢数据）；`card` 描述卡片：`title` / `subtitle` + `actions`（同 list 的 `item`）。可选 `drag: {method, params}` 声明拖拽换列触发的 action（params 里 `$column` = 目标列 key），`quick_add: {method, params, column?}` 声明列内快捷新增（params 里 `$text` = 输入内容，`column` 指定落入列）——两者本质都是 action 声明，走同一 api.toml 白名单校验（2026-07-20 实装）。

### 绑定语法

- `$data.x`：绑定 panel 事件注入的数据（`ActionResult.data` 里的键，如 `$data.rows`）。
- `$item.x`：item 上下文，仅 list 的 `item` / board 的 `card` 模板内可用（如 `$item.text`、`$item.id`）。
- 绑定可出现在任何字符串字段：整串恰好是一个绑定时取原值（保留类型），否则做字符串插值；查不到的键渲染为空串。
- 绑定可带管道过滤器（v1 仅 `date`）：`$item.created_at|date` 把 unix 秒渲染为 `M月d日 HH:mm`；未知过滤器原样透传。

### action

```json
{"label": "删除", "method": "notes.delete", "params": {"id": "$item.id"}}
```

`method` 必须在 api.toml 白名单；`params` 值支持同一套绑定语法。本地操作（filter/sort）后续版本再加。

### 降级

未知 `type`（或 schema 缺失/`version` 更高）：前端降级为 JSON 展示，不报错。


## 附录 B：focus 协议与工作台条（2026-07-20 实装）

定位：**面板是手、译宝是脑**——面板管确定性操作（direct action），译宝是唯一智能体；工作台里跟译宝说话走同一大脑，不引入第二个助手。

### focus 协议

- 壳面板窗内容变化（panel 事件刷新 / 补拉缓存）时，前端从面板数据推导焦点并上报：`{plugin, panel, item?}`。`data.rows` 恰好一条 → 该条为选中条目 `item = {id, title, status}`；多条/无 → 只有面板无条目。面板关闭/窗口销毁上报 `focus = null`。
- 通道：壳→脑新增 `panel_context` 消息（`report_panel_context` 命令透传）；脑侧存于 `_FOCUS`，`AgentLoop.focus_provider` 惰性取用。
- 注入：每次 run 把焦点渲染成一条 system 消息（「用户当前正在看「插件」的 X 面板，选中条目…；『这个/它』默认指该条目；用户没问到时不要主动提及」）。无焦点/异常 → 不注入。有条目才给指代提示，避免指代落空。

### 工作台条

面板窗底部常驻：团子（Avatar，状态经 brain-event 同步，可拖动面板窗/长按语音）+ 上下文 chip（有选中条目时显示「在看：{title}」）+ InputBar（文字/语音/打断）。提交走同一 `runInput`；流式回复在条上方浮气泡展示，final 后 ~6s 淡出，完整历史留在宠物窗。面板内确认仍走内嵌确认条，不打断对话。

### 协作回响（2026-07-20）

在工作台里边看边让译宝改，闭环不靠人来回切：

- **focus 重定向**：写操作（如 article_save）成功后的回跳面板，若用户正盯着同插件某 webview 面板（如写作编辑器）的同一条目（focus.item.id 匹配），改落到该 webview 而不是硬切 detail——编辑器收到 rows 重推自行刷新稿件（`loop._redirect_to_focused_webview`）。
- **refresh 传参交集**：tool 声明的 refresh 执行时，参数取「action 入参 ∩ refresh tool 声明参数」（save{id,content} → get{id}）；无交集传 {}（list 类刷新不带条件）。
- **槽位自愈**：新请求排队等上一任务的宽限为 `_PREEMPT_GRACE_S`（8s），超时强制取消——hung 任务不会把后续所有请求静默堵死（「点了没反应」类故障的根）。
