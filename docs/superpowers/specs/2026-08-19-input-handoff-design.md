# 输入条 handoff(Composer 接管)spec

日期:2026-08-19 | 前篇:specs/2026-08-17-coding-studio-r4-design.md(R4 多工位)、specs/2026-08-16-p1-takeover.md(P1 接管,已于 98da05a 退役)

## 背景与定性

R4 退役 takeover 转发层后,面板窗内回到两个输入框上下叠放(studio Composer + 壳译宝条),P1 当年的「两个太怪了」复现。本方案用 **handoff(输入所有权移交)** 替代转发:coding 面板打开期间壳译宝条整行让位,iframe 撑满到窗底,Composer 几何复刻译宝条、落进同一槽位——用户感知是「底部那条框原地换成 coding 模式」。

与 P1 takeover 的本质区别:takeover 是「一个框,两套实现」(壳条收键、逐条 postMessage 转发、状态双向同步,每加能力做两遍);handoff 是「一个框,一套实现」(译宝条退场,Composer 本人接管,只在切换瞬间移交一次草稿)。转发层当年两天攒一串边界修复(esc 闸门 a1d5226、状态补报 7a71c3d、@ 塞纯文本 c043539)是结构税,本方案无边界可税。

**全程零后端改动、零新协议基础设施**:随迁走现有 `postToIframe`(宿主→iframe 单向无回执),逃生口在壳自己的标题栏(不过桥),`emitEvent` 通道不需要新增消息。

后续:声明制四模式 + 大窗扩展见 specs/2026-08-19-panel-input-modes-design.md(本 spec 的 coding:studio 硬编码判定已收编为读声明)。

## 范围

1. 壳让位:PanelApp handoff 判定 + bench-bar 整行移除(bench 容器保留,对话浮层挂在里面)
2. 逃生口:标题栏团子 + 浮层 mini 输入(复活 30dd8c9 模式,直问大脑)
3. 草稿随迁:单向,译宝条 → 聚焦工位 Composer
4. Composer 复刻:几何/材质对齐 InputBar,家具收编进同一圆角容器,诚实差异保留

## 关键设计决策

### A. 壳侧让位(PanelApp.vue)

- handoff 判定:`current.panel === "coding:studio"`(coding 唯一入口;大窗无 bench 条,天然不受影响,无模式分叉)。
- handoff 时 `.bench-bar` 整行 `v-if` 移除;`.bench` 容器保留(对话浮层 `.thread` 挂在里面,逃生口依赖)。flex 布局下 `.content` 自动撑满到窗底,聚焦工位 Composer(现有 `--dock-h` 停靠机制不动)落进原 InputBar 槽位。
- 交还(切到其它面板/关闭):bench-bar 原样恢复,不做动画,一个 `v-if` 的事。
- InputBar 草稿持久化在外部 store(`sessionStore.conversation.getUIState(id).draft`),组件卸载不丢稿,`v-if` 移除安全。

### B. 逃生口:标题栏团子

- handoff 时团子(Avatar 组件,状态动画保留)从 bench-bar 搬到 `.titlebar` 左端;点击 `openLayer()` 开对话浮层。不参与窗口拖拽(× 按钮已有先例)。
- 叙事即语义:译宝退到壳上,把输入让给 coding。
- 浮层底部复活 P1 mini 输入行(30dd8c9 模式):**仅 handoff 时出现**;提交直走 `runInput` 问大脑(面板 focus 本就在大脑上下文里,「这个/它」有解);IME 守卫照抄 InputBar(compositionend 50ms 窗);浮层收起即清空残稿。
- 非 handoff:团子不搬、mini 输入不出现,一切照旧。

### C. 草稿随迁(单向)

- 触发:`setCurrent` 检测「非 coding → coding:studio」。
- 流程:InputBar 草稿 trim 非空才迁 → `webviewRef.postToIframe({type:"handoff-draft", text})` → 清 InputBar 草稿**含持久化副本**(`persistDraft("")`)。
- 时序:iframe 可能刚挂载未就绪——壳侧在 WebviewPanel iframe load 后投递,未就绪先暂存(只存最后一条)。
- studio 侧:收到 → 填入**聚焦工位** Composer 的 textarea(非受控组件,直写 DOM 对齐既有模式)并 `focus()`——「接管」手感。
- 反向不迁:交还多由面板切走触发,iframe 直接销毁无握手机会;Composer 草稿本就随工位实例存活(v-show 保活),交给既有语义。

### D. Composer 复刻(panel 工程内)

- 三个行段包进一个圆角容器,几何/材质抄 InputBar `.bar`:圆角半径、内边距、字号行高、背景、边框、聚焦描边。样式值手抄并在注释标明源头——iframe 隔源无法共享 CSS,InputBar 改版需同步(已知维护点,显式取舍)。
- AtRefsChips 对齐 InputBar `context-list` 样式,置于输入框上方。
- 发送 accent 钮挪进输入行右侧(InputBar 的 mic/send 槽位);中断 ghost 钮 busy 期现身于其左;keys-row 退役,状态提示收成输入框上方一行淡小字(有内容才占位)。
- ctx-row(cwd/mode/引擎 chip + 各自浮层)保留为卡内细行——诚实差异区,ghost 小药丸视觉降级。
- **诚实差异两条**:目标工位指示保持可见(发错会话比样式差异更糟);**不放语音钮**(不装假能力,禁用态是界面谎话)。

### E. 错误处理与边界

- handoff 期间其它插件的 L2 确认条照常从顶部进(confirm-bar 不在 bench-bar 内,不受影响)。
- `handoff-draft` 到达时聚焦工位 busy:照填草稿,发送时才走既有排队语义。
- 竞态:`postToIframe` 时 iframe 已销毁 → 空安全丢弃;「迁走即清」保证草稿同一时间只在一处。
- 语音在 handoff 期不可达(面板窗),与 P1 takeover 期一致,不新增处理。
- 主窗 InputBar、大窗 studio 均不涉及(无 bench 可让)。

### F. 测试与验收

- desktop vitest:handoff 判定、bench-bar 显隐、随迁消息发出 + 草稿双侧清空、标题栏团子渲染与点击、mini 输入提交走 runInput。
- panel vitest:Composer 收 `handoff-draft` 填草稿 + 聚焦;发送/排队/中断/@/粘贴截图行为不回归(现有用例兜底)。
- 视觉:截图自查器过 handoff 前/后两态。

## 非目标

- 反向草稿随迁(iframe 销毁时机不受控,握手不可靠——显式放弃)
- 语音输入接入 coding(handoff 期语音不可达,同 P1 takeover 期)
- 主窗 InputBar、大窗 studio 的任何改动
- takeover 转发层复活(本方案即其替代)

## 验收标准

A. handoff:coding 面板打开,面板窗内只有一个输入框(Composer),落在原译宝条槽位;切走/关闭面板后译宝条原样恢复
B. 逃生口:handoff 期标题栏团子可见,点击开浮层,mini 输入直问译宝有回复落时间线;浮层收起残稿清空;非 handoff 时团子/mini 输入不出现
C. 随迁:译宝条打草稿 → 打开 coding → 草稿进聚焦工位 Composer 并聚焦;译宝条草稿(含持久化副本)清空;空草稿不触发
D. 复刻:Composer 与 InputBar 肉眼同规格(圆角/高度/字号/发送钮位置);ctx-row 家具保留且视觉降级;无语音钮
E. 回归:Composer 发送/排队/中断/@/粘贴截图全绿;app + panel vitest 全绿;宽窄窗自适应不回归
