# 「OS 感」设计调研：译宝作为 agent 入口层（2026-07-27）

> 目的：回答「进入译宝之后，怎么让人感觉面对的是一个 OS 而不是一个 chatbot」。
> 方法：三路并行网络调研（AI OS 业界实践 / launcher 入口产品手法 / OS UX fundamentals），本文收敛结论并落成译宝的设计框架，作为后续「Home 主屏化」等改造的设计依据。
> 边界：本文只管「OS 感」的设计方向；场景插件（娱乐/生活/办公）的铺设另行规划，主屏是载体、插件是内容。

---

## 0. 结论速览（TL;DR）

- **OS 感 ≠ 功能多。OS 感 = 空间 + 状态 + 控制 + 生命。** Rabbit R1 / Humane 四样全无（只有一个对话框），是反面教材。
- agent 时代全行业（微软/Dreamer/Cowork/LangChain）收敛出三个新部件：**主屏 = Dashboard+Feed（不是对话框）、任务管理 = 有状态的收件箱（不是通知流）、人格 = kernel（唯一面孔兼仲裁者）**——第三条与译宝「桌宠是唯一面孔」的架构同构，是方向正确的强验证。
- 译宝五个面正好映射 OS 五个部件：**桌宠=Home键+状态灯、大窗 Home=主屏、插件列表=Launchpad、设置=系统设置、任务中心=收件箱**。缺口最大的是主屏化和收件箱化，也是下一步工程主体。
- 最小 OS 感套餐（按收益排序）：①Home 主屏化（Feed+Widget+Dock+问候）②任务收件箱化 ③设置补记忆管理+自主权旋钮 ④桌宠环境态三态+idle ⑤全局唤起统一。

---

## 1. AI OS 业界实践与教训

### 1.1 四条路线（2024–2026）

| 玩家 | 路线 | agent 的操作面 | 对译宝的启示 |
|---|---|---|---|
| 微软 | 改造 Windows 为 agentic OS | MCP + agent 身份/工作区；任务栏做进度入口（hover/徽标/通知） | 系统状态做成环境光而非打断；「agentic OS」提法曾引发用户反弹——基础体验没做好前别喊口号 |
| 苹果 | 渐进增强，隐私优先 | App Intents 结构化意图（SiriKit 弃用，不做像素操作）；Siri 进 Spotlight + 独立 app 回看历史 | 跨 app 编排优先走结构化接口（AppleScript/Shortcuts/MCP/原生 API），像素操作只做长尾兜底 |
| OpenAI | Apps-in-AI：AI 即 OS | 模型=kernel、ChatGPT UI=shell、Apps SDK(MCP)+应用目录；agent 有自己的虚拟电脑 | 「应用目录 + 聊天内嵌交互组件」= 译宝插件列表 + schema 面板的对应物 |
| Anthropic | 模型能力 API 化先行 | computer use（官方自述慢、易错、OSWorld 起步仅 14.9%）→ Claude Cowork 桌面产品：重大动作前批准、文件夹级权限、定时任务、隔离执行 | 通用 computer-use 是兜底不是主路；批准队列 + 权限范围 + 后台任务已被验证为产品形态 |

### 1.2 失败教训：Rabbit R1 / Humane AI Pin（按批评声量排序）

1. **可靠性/延迟/幻觉是及格线**：「bad at almost everything it does basically all the time」（MKBHD）是最致命判词。
2. **「这为什么不是个 app？」是生死问题**：没有不可替代价值，载体即被证伪（R1 被扒「就是个 Android app」）。
3. **生态缺失**：承诺的跨 app 动作兑现不了（LAM 不存在、集成全坏）。
4. **过度承诺 + 抢发**：R1 卖出 10 万台，5 个月后日活仅 5,000。
5. **没有 OS 感**：没有应用生态，也没有系统级的状态、通知、权限概念——本质是包在硬件里的单一聊天接口。

对照案例：Adept（$4 亿融资）产品未发即被 Amazon 吸收——agent 层创业别同时背模型层的资本负担；/dev/agents（前 Android 团队，$56M 种子）做出 Dreamer 后团队加入 Meta——独立「agent OS」的窗口可能被平台方收编，**做深单平台比做全平台现实**。

### 1.3 Dreamer（/dev/agents）：目前对「个人 agent OS」最具体的公开描述

- **人格化常驻入口**：Sidekick 个人 agent（可命名、有性格）——「sidekick is like the kernel, the agents and apps are like users… different rings（权限环）」。agent 之间不互调，一律经 Sidekick 按用户授权仲裁。**≈ 译宝的桌宠 + 风险闸门。**
- **主屏 = Dashboard + Feed + Widgets**：「agents do things in the background when you're not looking, and the feed is how they let you know what they've been up to」。
- **应用列表 = Gallery**（社区 agent 商店，可自然语言 fork）；**工具层 = Tools**（tool builders get paid）。
- **跨应用编排**：会议承诺 → 自动进待办 → 调招聘 agent 完成。
- **交付到已有表面**：「show up in the other apps that you already use」——agent 产物落进 Apple Podcasts 等用户已有 app，比自建内容面板省力且更 OS。

### 1.4 学界/业界反复出现的设计原则

- **Karpathy LLM OS 类比**：LLM=CPU/内核进程，上下文窗口=RAM，向量库/文件=磁盘；2025 改口 context engineering。
- **MemGPT/Letta**：记忆即分页问题（上下文=RAM、归档=磁盘，模型自己换页）——「限制多数 agent 的不是智能，而是记忆管理」。
- **Agent UX（NN/g 定义衍生）**：设计任务不是隐藏自治，而是「**make autonomy bounded, reviewable, and reversible**」。2026 年各家收敛的五大模式族：确认门、流式状态、信任信号、错误恢复、通知纪律。信任研究警告：「users who cannot follow an agent's reasoning lose trust **even when the output is correct**」——要更透明的界面，不是更简单的界面。
- **LangChain agent inbox**：后台 agent 需要一个收件箱，三模式 **Notify / Question / Review**；「The chat box defined the first wave of AI. The agent inbox will define the next.」通知≠收件箱：通知无状态不可批准；收件箱是有状态、可审计、可批量但绝不盲目的审批面。
- **Malleable software（Ink & Switch）**：用户能以最小摩擦重塑自己的工具——对应译宝 LLM 生成面板/webview 的路线。

---

## 2. Launcher / 入口类产品的「OS 感」手法

### 2.1 关键案例

- **Raycast**：CEO 原话定位「an operating system inside of your operating system」。手法：接管 Cmd+Space（装机第一仪式）、<50ms 延迟当工程硬约束、结果即可执行（不只搜索）、Extension Store（1000+ 社区扩展）、AI 做成系统能力（选中文字→快捷键→改写，不离开原界面）、原生材质（"feels like a first-party Apple app"）、discovery 优先（模糊搜索+内联提示）。用户：「I can't use a Mac without Raycast anymore.」
- **Alfred / uTools / Listary**：共性 = 召唤反射（Alt+Space/双击 Ctrl）+ 一个万能框 + 用完即走（"呼之即来，用完即走"）+ 越用越懂你（学习排序）+ 自动化积木（Workflows「感觉更像 macOS 的基础层而不是启动器」）。
- **国产 AI 桌面端（豆包/ima/夸克）**：手段趋同——全局热键 + 划词/截图唤起 + 常驻条/钉桌面 + AI 搜索首页。**被验证有用**：贴上下文的唤起（划词/截图）、知识库沉淀（ima「我的东西都住在它这里」）；**被惩罚**：额度套路、弹窗打扰、长而不实的功能清单。
- **桌宠情感设计（VPet/Shimeji/MateEngine）**：被反复验证的四件套——**idle 自主行为**（发呆乱走爬窗，「需要挂机才能看到」）、**身体性交互**（摸头/喂食/提起）、**照料循环**（心情/饱食度消耗，「舍不得关」）、**对环境有反应**（随音乐起舞、眼球跟随鼠标——「它注意到你」比「它陪你玩」更戳人）。VPet 上架 4 天 3000+ 评测 98% 好评。反面教材：Clippy/BonziBuddy——常驻信任破产即卸载。
- **Dynamic Island / 菜单栏环境态**：三要素 = 小（只放最重要的一眼信息）+ 活（随状态呼吸）+ 可展开（轻点进工作区）。「the Live Activity is the ambient signal, the app is the workspace」——常驻层的职责是「在正确的时刻把人拉进来」（done/blocked/needs input）。常驻权靠克制维持。

### 2.2 营造 OS 感/入口感的手法清单（12 条）

1. 全局单一召唤键，极简到成为反射
2. 一个输入框收敛所有意图（找/开/算/问/控同一处）
3. 结果即可执行，而非只给信息
4. 用完即走的瞬态 UI，不打断心流
5. 速度是平台感的地基（<50ms）
6. 插件生态 + 商店（每个插件都是沉没成本和迁移壁垒）
7. 做「只有 OS 能做的事」（剪贴板历史、窗口管理、选中即问、截图即搜）
8. 环境态常驻：小、活、可展开
9. 自主 idle 行为制造生命感
10. 情感账户：让用户投入照料（禀赋效应→「舍不得关」）
11. 人格化主动交互，但克制打扰（频率与场合用保守策略换长期居住权）
12. 说 OS 的语言（系统原生材质/符号/动效，「像系统自己长出来的」）

> 对译宝：第 8–11 条是现有竞品（Raycast 缺温度、豆包缺生命、桌宠缺能力）都没占住的交叉带——**「有生命感的常驻环境态 + 有真本事的上下文唤起」**。

---

## 3. OS 感的八个构成要素（UX fundamentals）

| # | 要素 | 定义 | 桌面 OS 实例 |
|---|------|------|--------------|
| 1 | 稳定的空间隐喻 | 东西有固定「位置」，靠识别而非回忆 | 桌面/文件夹层级、Dock 恒在底部、菜单栏恒在顶部 |
| 2 | 默认返回点（家） | 所有旅程的起点和终点，提供安全感与松弛 | 手机主屏（Home 键必回）、游戏 hub world |
| 3 | 常驻状态层 | 系统在后台活着，零成本透出状态 | 菜单栏时钟/电量、widgets、Dock 运行指示点 |
| 4 | 统一控制入口 | 能检查并改变系统一切行为的地方 | macOS 系统设置、控制面板 |
| 5 | 一致的设计语言 | 同一套视觉/交互词汇贯穿所有模块，知识可迁移 | Apple HIG 让第三方 app 像「本地人」 |
| 6 | 全集清单（启动器） | 系统能力的穷举式可见清单 | Launchpad、开始菜单 |
| 7 | 个性化与所有权 | 定制 → 心理所有权 → 「我的」系统 | 壁纸、主屏布局、动森的家 |
| 8 | 生命周期仪式 | 标记系统阶段转换的固定仪式 | 开机动画、登录问候、锁屏、更新提示 |

补充认知：

- **主屏 vs app 列表**：主屏是「我的」（用户策展过的空间），app 列表是「系统的」（穷举清单）。主屏的编排权 = 用户对自己注意力的治理权。
- **设置的价值在于「它存在」**：统一设置入口 = 可控性 + 所有权的承诺（Nielsen #3 用户控制与自由；Sundar & Marathe 2010：customization 产生所有权，personalization 不产生）。很多设置项用户从不动，但它把「一堆别人做的功能」转化为「我的系统」。
- **游戏 hub 的可迁移手法**：① 一个「无任务的松弛空间」作为默认归宿，与干活界面分层；② 常驻空间承载仓储（收藏/历史）、社交（agent 人格）、叙事（共同记忆）三重功能；③ 允许布置空间以产生心理所有权；④ 外出完成任务后有明确「回家」过渡，形成节奏感。

---

## 4. 译宝设计框架：五个面 = OS 五个部件

> 一句话：**桌宠带你回家（1），家里有动态和快捷入口（2），全家当清单在抽屉里（3），保险柜钥匙在你手上（4），它在外面干的活都记账给你看（5）。**

### 4.1 桌宠 = Home 键 + 状态灯

- OS 职能：默认返回点（不管在哪，点它必回家）+ 常驻状态层（不解锁也一瞥可知）。
- 现状：五个部件里最成熟（常驻桌面、点开即对话、有待命/聆听/思考态）。
- 缺口：状态语义只到「运行态」，缺「它想找你」态；idle 生命感未做。
- 方向：三态表情（忙/闲/有事找你）一眼可辨；轻量 idle（发呆、跟随鼠标）；有事时气泡轻提示而非弹窗。

### 4.2 大窗 Home = 主屏（最大缺口，最大杠杆）

- OS 职能：解锁后第一眼、所有旅程的起点和终点；「我的」策展空间。
- 现状：大窗 = 对话页 + 插件列表，仍是 chatbot 结构。
- 四大改造：
  1. **Feed 流**：后台干完的事按时间摊开（提醒触发、委派任务完成、code_exec 结果、新记住的东西）。数据全现成（tasks 表 + 审计日志 + 提醒记录）。「它在我不看的时候也在干活」是 agent OS 与普通 app 的分水岭。
  2. **Widget 区**：插件给主屏供一瞥卡片（今日提醒、看板计数、任务进行态）——schema 协议加 `widget` 类型复用现有面板机制，不另起框架。
  3. **Dock 条**：常用插件固定 4–5 个在主屏底部，肌肉记忆区。
  4. **问候仪式**：早晨首次打开一句「早上好，今天有 2 个提醒、1 个任务昨晚跑完了」——最便宜的生命信号。

### 4.3 插件列表 = Launchpad

- OS 职能：能力全集的穷举清单（「它会什么」），主屏管效率、清单管安全感。
- 现状：双击桌宠出插件面板、大窗插件页——雏形已在，「被动合格」。
- 方向：插件多了再加分组（娱乐/生活/办公）与搜索；每个新插件自动获得清单位置 + 可上主屏 Dock。

### 4.4 设置 = 系统设置

- OS 职能：统一控制入口 = 可控性 + 所有权。对常驻 agent：「我管得住它」是用户敢让它开机自启的前提。
- 现状：设置页起步（权限授权、开机启动、清数据）。
- 方向：**记忆管理**（看它记了什么、可改可删——「它记得我什么」必须可见，MemGPT 分层的产品化）、**自主权旋钮**（主动行为频率：多久主动找我一次）、插件独立开关。

**实装记录（2026-07-28）**：

- 记忆管理三件套齐：可见（命名空间分组列出 + 筛选 chips + 点击展开全文）、可改（行内编辑，链路 `mem_edit` IPC → `Memory.update` → mem0 按 `memory_id` 更新）、可删（单条二次确认 + 数据区清空）。
- 自主权旋钮落地为**触达强度三档**（`proactive.level`，默认 `full` 兼容旧行为）：`quiet` 提醒与播报只落 Feed/历史不打扰；`bubble` 桌宠冒泡 + 标「有事找你」不亮窗不出声；`full` 亮窗 + 气泡，TTS 仍由 `proactive_voice` 单控。管辖边界：Feed/历史照落（可追溯底线），`error`/确认闸门不受旋钮管辖（信任信息必达）；挂载点 = `_reminder_loop` 与 `_on_plugin_event` 两个主动推送点。
- 验证：sidecar 全量 `527 passed`；`vue-tsc --noEmit`、Vite build、`cargo check`、`cargo test` 全部 exit 0。
- 待真机验收：编辑记忆后召回生效、三档下提醒/播报实际表现、quiet 档 error 事件仍送达。

### 4.5 任务中心 = 收件箱（不是通知）

- OS 职能：agent 时代的任务管理器。通知是无状态信息流（看完就没），收件箱是有状态控制面（待办、可批、可追溯）。
- 现状：agents 任务列表 + 完成播报 + 确认弹窗，零件都有但散装（播报散在对话里、批准是一次性弹窗、无统一视图）。
- 方向：任务面板升级——进行中/待批准/已完成三区；待批准排队一键批/拒（替代连环弹窗，配合已有「本会话不再询问」）；完成事项进 Feed（与主屏联动）；Notify/Question/Review 三分级。

---

## 5. 落地路线（最小 OS 感套餐）

按单点收益排序：

1. **Home 主屏化**（Feed + Widget + Dock + 问候）——最大杠杆
2. **任务收件箱化**（三区 + 待批队列 + 与 Feed 联动）
3. **设置补两件**：记忆管理页 + 主动权旋钮
4. **桌宠环境态强化**：三态表情 + 轻量 idle
5. **全局唤起统一**：一个反射键 + 划词/截图上下文唤起（偏系统层，可并行）

之后：场景插件（娱乐/生活/办公各立一个旗舰）按节奏铺，每个自动获得主屏 widget 与清单位置；系统级深集成（通知中心、分享扩展、URL scheme）随打包分发一起做。

## 6. 反模式清单（别做什么）

- ❌ 把功能清单堆长当 OS 感（夸克式营销层功能，被用户点名）
- ❌ 弹窗轰炸式审批（→「全点同意」或漏掉关键一条；进收件箱排队）
- ❌ 宽而碎的技能上线（R1 死因；做不到「几乎每次都对」的技能宁可藏起来）
- ❌ 过度主动打扰（Clippy 教训：常驻信任破产即卸载；主动频率用保守策略换居住权）
- ❌ 基础体验没做好就喊「OS」口号（微软 agentic OS 提法遭反弹的教训）
- ❌ 通用 computer-use 当主路径（全行业天花板；结构化接口优先，像素做兜底）

---

## 附录：主要来源

AI OS 业界：
- The New Stack — OpenAI aims to make ChatGPT the operating system of the future: https://thenewstack.io/openai-aims-to-make-chatgpt-the-operating-system-of-the-future/
- Thurrott — Ignite 2025: Windows 11 agents on the taskbar: https://www.thurrott.com/a-i/329763/ignite-2025-windows-11-is-getting-agents-on-the-taskbar-and-more-ai-features
- Apple Newsroom — Siri AI（2026-06）: https://www.apple.com/newsroom/2026/06/apple-introduces-siri-ai-a-profoundly-more-capable-and-personal-assistant/
- Anthropic — Developing computer use: https://www.anthropic.com/news/developing-computer-use
- Latent Space — Dreamer（/dev/agents）长访谈: https://www.latent.space/p/dreamer
- The Verge — Rabbit R1 review: https://www.theverge.com/2024/5/2/24147159/rabbit-r1-review-ai-gadget
- The Verge — /dev/agents 报道: https://www.theverge.com/2024/11/27/24307525/android-leaders-dev-agents-ai-agent-operating-system-startup
- LangChain agent-inbox: https://github.com/langchain-ai/agent-inbox ；Coommit 解读: https://coommit.com/blog/agent-inbox
- AgenticWire — Agent UX 设计模式清单: https://www.agenticwire.news/article/agent-ux-design-patterns
- AIOS（arXiv:2403.16971）: https://arxiv.org/html/2403.16971v5 ；MemGPT（arXiv:2310.08560）

Launcher / 入口 / 桌宠：
- The Verge — Raycast 采访: https://www.theverge.com/2024/9/25/24253375/raycast-mac-launcher-ios-windows
- Trackr — Raycast 研究报告: https://www.trytrackr.com/share/showcase-raycast-2026w10
- 虎嗅 — Raycast 与 AI 入口: https://www.huxiu.com/article/4482817.html
- VPet Simulator（Steam）: https://store.steampowered.com/app/1920960/_/ ；Shimeji: https://shimejis.xyz/ ；MateEngine: https://store.steampowered.com/app/3625270/MateEngine/
- Infinum — Dynamic Island 设计: https://infinum.com/blog/start-designing-for-dynamic-island-and-live-activities/
- vp0 — Live Activity 作为 agent 环境信号: https://vp0.com/blogs/ios-dynamic-island-live-activities-ai-agent

OS fundamentals：
- Kent de Bruin — The Desktop Metaphor: https://kentdebruin.com/the-desktop-metaphor/
- WIRED — homescreen 编排: https://www.wired.com/story/homescreen-apps-ios-android/
- Sundar & Marathe 2010 — Personalization vs Customization: https://terpconnect.umd.edu/~nan/738readings/Sundar%20Marathe%202010%20personalization.pdf
- CHI 2025 — Psychological Ownership of Interactive Virtual Objects: https://dl.acm.org/doi/full/10.1145/3706598.3713750
- WIRED — Hub Worlds: https://www.wired.com/story/how-hub-worlds-shape-video-game-design/
- InRhythm — Apple HIG 解读: https://inrhythm.com/blog-post/a-comprehensive-introduction-to-apples-human-interface-guidelines/
