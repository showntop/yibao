一、产品定位与设计原则
定位：常驻桌面的 AI 桌宠 agent。透明窗宠物形象（团子）+ 主窗三区（收件箱 / 对话 / 技能）+ 小窗面板。它长时间在屏幕上不被操作，且始终叠在用户的真实工作窗口之上。
由此推出四条硬性原则，后续每个决定都回溯到它们：

静默优先：不交互时视觉噪声趋近于零。动效只在状态真变化时发生，绝不做无意义循环装饰。
状态可辨优先于精致：24–36px 下九态必须能区分。小尺寸辨识度 > 大尺寸细节。
叠加友好：半透明与阴影要在任意底色（白文档、深色 IDE、照片壁纸）上都不糊字。
克制的生命感：呼吸/眨眼/张望/招手全部保留——这是产品灵魂，但幅度收小、节奏拉长。


二、当前问题清单（已核实）
#问题证据1暖棕文字色留在冷天青表面tokens.css --yb-text: #3f372e、--yb-body-ink: #3f372e；Avatar.vue:133 影子 fill="#3f372e"。奶油主题遗留，与 --yb-text-dim: #8a9aac（冷调）同屏冲突2动效令牌形同虚设--yb-dur/--yb-ease 已定义，但组件内 transition: transform 0.15s ease（Avatar.vue:355）等硬编码遍布 15 个文件3圆角/字号硬编码全量统计 167 处 border-radius:/font-size:/transition: 字面量4待批准琥珀色未进 token--yb-state-work: #f2a03c 被当作装饰色借用（Avatar.vue:261 spark 直接写 #f2a03c），无语义层5团子双重变换.av{transform:scaleY(0.78)} 压全局，.face 再用 matrix(1,0,0,1.282,0,-16.9) 反向拉回 → 描边粗细不均、天线被压扁6光晕过大aura 是 r=58 满幅圆（几乎等于 viewBox 半径），侧边栏 36px 下糊成蓝雾7小尺寸九态难辨状态信息集中在 3.4px 的天线灯点上8UI emoji 当图标⏰💬🔐📌🍡👋📄⚠️（HomeFeed 262/264/503/709、PanelApp 378、SetupWizard 50、PermissionsBanner 27、App 701）跨平台字形不可控、与线性 UI 割裂

三、令牌层重构（app/src/assets/tokens.css）
全量重写为语义分层。组件只消费语义名，不再消费原始色值——这是深色模式免改组件的前提。
结构：

原始色板（--yb-c-slate-*、--yb-c-sky-*、--yb-c-amber-* …）→ 私有，仅在映射块内引用
语义层：surface（1/2/3 + glass）、text（strong/base/dim/onAccent）、border（base/strong/focus）、accent、state-*、intent-*
修掉 #1：--yb-text: #1e2a38（冷墨蓝黑）、--yb-text-dim: #6b7d91、--yb-body-ink: #24313f、影子改 --yb-shadow-ink
修掉 #4：新增 --yb-intent-pending（琥珀，待批准/需人工确认）、--yb-intent-danger、--yb-intent-ok，与「天线状态灯」--yb-state-* 彻底分开语义
修掉 #2/#3：补全 --yb-dur-fast: 0.12s / --yb-dur: 0.2s / --yb-dur-slow: 0.36s；--yb-ease-out（标准出场）/ --yb-ease-spring（弹簧，仅用于形象与入场）/ --yb-ease-inout；补 --yb-fs-xs、--yb-lh-tight/base/loose、--yb-radius-pill、--yb-focus-ring、--yb-shadow-1/2/3
深色模式 ~~（按你的选择，本次不交付）~~：**已于 slice2c 交付**（tokens.css :402-651 系统跟随 + 显式双通道全量覆盖块，Home 三态切换）；2026-08-20 补齐插件面板层（genpanel/zimeiti editor 天青化 + dark 媒体查询，coding studio 待 R4 收尾落定后补）。删掉已过期的「过渡别名」块（--yb-idle 等，注释说明 Task 2 后可删——本次正是那个时机）。

排版规则（统一到令牌，落到组件）：

字阶四级封顶：xs 11px / sm 11.5px / md 12.5px / lg 13.5px / xl 15px，禁止新增中间值
行高：正文 1.6（长文可读）、UI 单行 1.3、标题 1.25
字重只用 400 / 500 / 600，去掉 700（小字号下 700 在 PingFang 上糊）
数字与代码统一 --yb-mono + font-variant-numeric: tabular-nums（计数、时间、统计不跳动）


四、图标系统（替换全部 UI emoji，问题 #8）
新增 app/src/components/YbIcon.vue：单文件内联 SVG sprite，viewBox 0 0 24 24，stroke-width 1.75、currentColor、stroke-linecap round。统一线性风格，尺寸经 size prop（默认 16）。

首批图标：clock（提醒）、chat、lock（权限）、pin、doc（上下文附文）、wrench（过程行进行中）、check、x、stop（打断）、alert（待批准）、inbox、sparkle、wave（欢迎）、plug（技能）、settings
替换点：HomeFeed.vue:262/264/503/709、PanelApp.vue:378（🍡 空态占位改用团子剪影而非 emoji）、SetupWizard.vue:50、PermissionsBanner.vue:27、App.vue:701/719、PanelApp.vue:349
过程行（🔧→✅/❌）：HomeChat.vue 已有 b.proc.done / procOk() 结构，直接换图标即可。App.vue、PanelApp.vue、HomePlugins.vue 三处是把 emoji 拼进字符串（text: "🔧 " + procLabel(...)）——给消息对象加 pstate: "run" | "ok" | "fail" 字段，文案保持纯净，图标由状态渲染。这样过程行未来能加「详情」展开而不必解析字符串。


五、形象团子重构（Avatar.vue，问题 #5/#6/#7）
关键：宽高比精确不变
你认可当前比例，所以我用中心锚定映射把压扁烘进几何，数学上与现状等价：
y_new = 60 + (y_old - 60) × 0.78

推导：旧管线 .av 的 scaleY(0.78) 以元素中心（user space y=60）为原点，故身体 y=18..102 的最终位置就是 27.2..92.8。烘进 path 后移除 CSS 压扁，渲染结果逐像素一致——宽 48 单位不变，高 65.5 单位不变，比例 48:65.5 完全保持。
脸部的匹配收益：matrix(1,0,0,1.282,0,-16.9) 后再经 0.78 压扁，净变换是 y → y - 0.008，即脸部现有坐标本就等于最终坐标（这正是反向矩阵的用意）。所以直接删掉整个 matrix，脸部坐标一个都不用改。九套表情（Avatar.vue:160-240）零改动，风险极低。
天线不再被压扁（你点出的问题）：天线灯、think 虚线环、notify 徽标、声波原先都吃了 0.78。现在把它们的中心点按同一公式重定位（如灯 y=8 → 19.4），但半径保持圆形——r=3.4 的灯与 r=6.5 的环恢复为真圆，描边粗细全图均匀。
光晕收紧（#6）
r=58 满幅圆 → 贴身接触光：椭圆 cx=60 cy=64 rx=30 ry=24，透明度上限从 1.0 降到 0.42。新增 compact prop，在渲染 <40px 处（Home.vue 侧边栏 36px、Feed 行内）整体不渲染 aura，避免蓝雾。
小尺寸九态可辨（#7）
状态不再只靠 3.4px 灯点，改为三通道冗余编码：

色（保留现有九色）
灯的形状/节奏：idle 呼吸暗淡、listen 快脉冲、think 转环、work 慢脉冲、say 辉光、success 星芒、error 抖动、notify 快脉冲+招手、drowsy 全体减速 + Zz
compact 下灯半径放大到 4.6 并加 1px 白描边（在任意底色上都拓出轮廓），think 环 dasharray 加粗

生命感动效全部保留：breathe、blink、yb-look 张望、wave 招手、zfloat。仅把硬编码时长换成 --yb-dur-* / --yb-ease-spring，并全局包一层 @media (prefers-reduced-motion: reduce) 关掉循环动画（常驻软件的无障碍与省电底线）。

六、界面层级与交互（主窗 + 宠物窗）
层级三档（修掉当前卡片阴影/边框各写一套的问题）：

L1 页面底 --yb-bg，无阴影
L2 内容卡 --yb-surface-1 + --yb-border-base + --yb-shadow-1
L3 浮层（气泡/弹窗/Dock）--yb-glass + blur + --yb-shadow-3
玻璃材质只用于 L3。L2 用实色——修掉 #3「叠加友好」：卡片半透明叠在用户文档上会糊字。

收件箱三区靠左侧 3px 意图色条区分，而非各自换底色（当前待批准区整块染色，视觉过重）：待批准 --yb-intent-pending、进行中 --yb-accent、已完成 --yb-text-dim。
交互反馈统一四态：hover（--yb-dur-fast 抬升/染底）、active（scale(0.97)）、focus-visible（--yb-focus-ring，键盘可达）、disabled（opacity 0.45 + cursor not-allowed）。当前多数按钮缺 focus 环。
动效清单（全部走令牌）：气泡入场 --yb-ease-spring + translateY(6px)→0；Feed 行进出 --yb-dur；批准决定后行折叠而非瞬间消失；流式输出光标；宠物拖拽跟随。不加任何页面级转场或视差。

七、走查画布（DesignPreview.vue 扩展）
扩展为完整设计走查页，我用它逐项截图验证，你之后也能长期用：

形象九态 × 三尺寸（24 / 36 / 64px）——直接验证「小尺寸可辨」与光晕
色彩令牌全色板 + 语义名
字阶与行高样张（含中英混排、数字对齐）
图标全集（16 / 20 / 24px 三档）
按钮四态矩阵、输入条、开关、复选
收件箱三区（待批准含批量选择态 / 进行中 / 已完成）
Feed 行三态（未读 / 跟进 / 忽略）+ 置顶
对话流（用户 / AI / 过程行 / 提醒 / Markdown 表格）
空态、Dock、层级 L1–L3 对照


八、执行顺序与验证

tokens.css 全量重写（语义分层 + 深色骨架注释）
YbIcon.vue 新建
Avatar.vue 几何烘焙 + 光晕 + compact + 动效令牌化
DesignPreview.vue 扩展为走查页
核心界面收敛：Home.vue、HomeFeed.vue、HomeChat.vue、InputBar.vue、Bubble.vue、App.vue、PanelApp.vue、HomePlugins.vue
次级视图仅修可见冲突（暖棕残留、focus 缺失）：SettingsView.vue、SchemaPanel.vue、SetupWizard.vue、PermissionsBanner.vue、ConfirmDialog.vue

验证：design.html 与 home.html 用 agent-browser 截图；重点比对团子重构前后在 64px 下必须像素级接近（证明比例没变），并在 24/36px 下确认九态可辨。
边界：不改任何业务逻辑、Tauri 命令、brain.ts 数据流；过程行加 pstate 字段是唯一的数据结构改动，且向后兼容。不引入图标库或动画库（零新依赖）。我正在