# R4 多工位 + 插件前端运行时重构 spec(coding studio)

日期:2026-08-17 | 前篇:specs/2026-08-17-p2-supervisor-wall.md(R4 会话墙)、specs/2026-08-15-coding-panel-r3-ui.md | 依据:docs/research/2026-08-16-yibao-collab-patterns.md(P2 督导)、docs/research/2026-08-15-coding-agent-competitive.md(Emdash 三栏壳)

## 范围

1. **插件前端运行时("module 面板")**:面板从「启动时读进内存的单文件 HTML 字符串」改为「plugins/ 下独立构建的静态 bundle,运行时经自定义协议按需加载」——真·运行时扩展,第三方插件不重建 app 可装;多文件工程、资源、代码分割解锁;热加载免重启 sidecar
2. **coding 前端重写**:chat.html(2943 行 vanilla JS)拆成多文件 Vue 工程重实现,能力对齐现状(对话流/自动回放/引擎 picker/跨引擎交接/rewind/mode/@ chips/粘贴截图)
3. **R4 多工位**:新面板 `coding:studio`——左栏会话列表(原会话墙)+ 中部并排工位(默认 2 最多 3)+ 底部共享输入条按聚焦路由;单一自适应 UI,窄窗(面板窗)自动收成单工位、侧栏变抽屉;coding 唯一入口,chat.html 与 wall.schema.json 退役
4. **统一 review 栏**:studio 右栏,全量待批按会话分组,单条/组级裁决走 `coding.decide`;与 L2 确认条/收件箱/手机端同一 `_PERM` 注册表双通道幂等,后端补「已裁决」广播保证各处同步消失

## 关键设计决策(侦察依据)

### A. module 面板运行时

- **声明**:manifest.toml `[[panel]]` 新增 `type = "module"`,`src = "panel/dist/index.html"`;旧 `webview`/`schema`/`widget` 类型不动(tools.html/editor.html/gen 面板继续走旧 srcdoc 通道,后续各自迁移,本期不改)
- **sidecar 侧**:`_load_panels`(plugins.py:404-448)对 module 型不读文件全文,只登记 `{type:"module", entry, mtime}`;`panel_payload`(plugins.py:361-375)下发 `{webview:{url:"yibao-plugin://<pid>/panel/dist/index.html", v:<mtime>}}` 引用——全文随事件重传问题消失;每次打开面板读最新 mtime 作版本号,文件变更前端整页刷新即热加载
- **协议层**:Rust 注册 `yibao-plugin://<pid>/<path>` 自定义协议,从 plugins_dir 只读服务文件(路径防穿越 + MIME 映射);serve index.html 时在 `<head>` 注入 CSP + 桥 SDK `<script>`(BRIDGE_JS 注入手法从 srcdoc 挪到协议层);顺带修 `plugins_dir()`(lib.rs:1213)prod 下依赖编译机路径的隐患
- **CSP/沙箱**:CSP:sandbox iframe(仅 allow-scripts)下 'self' 不可实现,实现为显式 scheme 白名单——`default-src 'none'; script-src/style-src yibao-plugin: 'unsafe-inline'; img-src/font-src yibao-plugin: data:; connect-src 'none'`(终审记录:按 pid 收窄 script-src 与 form-action 'none' 为阶段二跟进项)。iframe 保留 `sandbox="allow-scripts"`。
- **前端分流**:WebviewPanel 按 payload 分叉——带 `url` 用 iframe src(新),带 `html` 走 srcdoc(旧)
- **桥 SDK**:postMessage 协议语义原样保留正式化(`invoke/onInit/onMessage/emitEvent`,`window.yibao`,协议带版本号);方法调用仍经 panel_action → sidecar api.toml 白名单裁决,安全模型不变;`native:pick_folder/save_attachment` 白名单保留
- **插件工程**:repo 提供共享构建脚本 + 模板(Vite);Vue 由宿主在协议源上提供共享 `vue.esm-browser` 构建,import map 解析,插件 bundle 外置 Vue(锁宿主 Vue 大版本);第三方插件可零框架纯 JS,只依赖 `window.yibao`

### B. coding studio(多工位)

- **布局**(大窗四区):左栏会话列表(原会话墙能力:状态/cwd/prompt/时间卡片,「加入工位」「停止」,顶部新建会话选 cwd+引擎)| 工位区(默认 2 栏,「+」加到最多 3,工位头显示会话名/引擎/状态,可换绑/移出,点击聚焦高亮)| 右栏统一 review | 底部共享输入条
- **自适应**:一套 UI 随宽度伸缩;面板窗窄宽自动单工位 + 侧栏抽屉化,行为等价今天 chat 面板;无模式分叉
- **数据流**:后端 `emit panel_data` 的 panel 字段 `coding:chat` → `coding:studio`(载荷本有 session_id);studio 单实例收全量会话流,按 sid 分拣进 per-sid 缓冲,未绑工位的 sid 进后台缓冲、绑上即回放对齐——替代现有每 iframe 自筛(chat.html:1604)
- **输入条**:studio 自实现(iframe 内复用不了宿主 InputBar),能力对齐:@ chips(会话+文件,经 `coding.sessions` + `native:pick_folder`)、粘贴截图(`native:save_attachment` 落盘 chip)、busy 排队(Codex Queue 式)、esc 中断;按聚焦工位路由,输入条左侧显示目标会话 chip;`coding.start` 支持在指定工位开新会话;takeover-input 转发层(PanelApp.vue:291)随之退役
- **统一 review**:数据源为各会话事件流 `permission_request`(rid 形如 `perm_<sid>_n` 天然带归因);卡片区操作类型/命令/路径摘要,单条「允许/拒绝」+ 组级「全批这组」,裁决调 `coding.decide`(既有,与 confirm_batch 同写 `_PERM` 注册表,幂等先到先得);后端补 `permission_resolved` 广播事件,任一通道裁决后所有表面(确认条/收件箱/手机/review 栏)同步消失;工位流内不再镜像待批卡
- **「接管」语义**:会话列表行动作从「接管」变为「加入工位/聚焦」;`coding.attach` 仍用于历史回放

### C. 模块拆分(coding 前端工程)

- `stores/` — 会话状态(per-sid 事件缓冲/回放对齐)、待批聚合、工位布局(栏位/绑定/聚焦)
- `components/` — Station、MessageList、ToolCard、Composer、SessionRail、ReviewRail、EngineChip/Picker、HandoffDialog、RewindMenu
- `lib/` — 桥 SDK 封装、markdown 渲染(marked/dompurify/highlight 从 vendor 内联改为正常依赖)、chips 解析

### D. 错误处理

- 工位粒度隔离:会话流断/引擎崩只标红该工位;iframe 加载失败给「重载面板」
- `coding.decide` 60s 超时按拒绝并提示;invoke 失败在对应工位内联报错,不弹全局打断
- 输入条目标工位被换绑/移出时清空草稿归属并提示

### E. 测试与验收

- sidecar pytest:module 面板加载、引用式 payload、decide 幂等、permission_resolved 广播
- 前端 vitest:sid 分拣/回放对齐、待批聚合分组、工位布局 store;studio 关键链路组件测试(发消息→流式→待批→裁决)
- 手工验收:双工位并行两引擎、统一 review 批处、宽窄窗自适应、热加载(改 panel 文件不重启 sidecar 生效)

### F. 落地顺序(每步独立可验收)

1. 插件运行时:`yibao-plugin://` 协议 + SDK 注入 + manifest module 类型 + 构建模板(hello 面板打通全链路)
2. coding 前端重写:单工位能力对齐现状,替换 chat.html
3. 多工位:工位区 + 会话列表左栏 + 聚焦路由输入条
4. 统一 review 栏 + permission_resolved 广播
5. 收口:wall.schema.json 退役、文档更新;顺带清留档小修四件(codex resume brief fallback、usage baseline 持久化、手机端 coding 待批卡、takeover 非 file contexts 丢弃)

## 非目标

- Best-of-N(多工位同屏手动对比已覆盖核心诉求,组概念/对比视图留档)
- toolbox/zimeiti/gen 面板迁移(旧 srcdoc 通道暂留,后续各自迁移)
- worktree 隔离、cursor CLI(v3 留档)、会话成本持久化(留档)
- 移动端渲染插件面板(现状即不渲染,本期不改)

## 验收标准

A. module 面板:hello 面板经 `yibao-plugin://` 加载,invoke/event 双向通;改 panel dist 文件后重开面板即新代码(不重启 sidecar);CSP 禁跨插件/网络资源;旧 webview/schema 面板行为不回归
B. coding 重写:单工位能力全对齐——对话流(markdown/工具卡)、自动回放、引擎 picker、跨引擎交接、rewind、mode、@ chips、粘贴截图;P1/P2 全部既有验收不回归
C. 多工位:大窗打开 studio,左栏列表加 2~3 个会话进工位并排跑(可不同引擎);点击聚焦切换,输入条路由正确;busy 排队、esc 中断、停止、换绑、移出正常;第 4 栏不可加
D. 窄窗自适应:面板窗宽度自动单工位,左栏/review 抽屉化,能力不缺失
E. 统一 review:两会话同时挂起待批 → review 栏按会话分组;单条裁决、组级全批生效;L2 确认条/收件箱裁决后 review 栏卡片同步消失(反之亦然);60s 超时 deny 兜底
F. 热加载:dev 下改 coding panel 源码重新构建 dist,重开面板即生效,sidecar 不重启
G. sidecar pytest 全绿(含新增);前端 vitest 全绿(含新增)
