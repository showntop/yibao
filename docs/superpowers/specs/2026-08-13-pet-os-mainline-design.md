# 桌宠 OS 主线设计（2026-08-13）

> 方向决断：**以「感知 + 主动陪伴的桌宠 OS」为产品主线，coding 作为最深的垂直插件为副线，computer-use 收缩为「a11y 可及区可靠即可」的兜底能力。**
>
> 依据：`docs/research/2026-07-27-os-feel-design.md`（OS 感框架）、`docs/research/2026-08-09-capability-surfaces-design.md`（能力表面调用模型）。
> 本文是这两份调研的**收口执行版**：盘清哪些已落地、哪些是纸面，把剩下的坑排成四个阶段。

---

## 1. 为什么是这条线

三条候选主线各自的资产与天花板：

| 主线 | 现有资产 | 天花板 |
|---|---|---|
| 通用 computer-use | grounding/SoM/a11y 全套 | **地基是红的**：`docs/reports/2026-08-01-v1.1-slice1-baseline.md` 记录三轮尝试后，a11y 可及区 2/5、盲区 0/5（中位误差 367px）。全行业公认天花板（Anthropic 自述 OSWorld 起步 14.9%），且正面撞大厂 |
| coding 垂直 | 最大插件（2299 行）、r2 分支已有权限交互/rewind/plan mode | 赛道拥挤，但作为「一个插件」价值极高 |
| **桌宠 OS** | 感知 A/B/C 源 + 加密存储 + Distiller + watch 主动搭话 + Feed + 收件箱 + 自主权旋钮 + 十态桌宠 + 能力表面 Slice 1 | **资产最厚、别人最难抄** |

调研原文的判断（`os-feel-design.md` §2.2）已经指明这个交叉带：

> 第 8–11 条是现有竞品（Raycast 缺温度、豆包缺生命、桌宠缺能力）都没占住的交叉带——**「有生命感的常驻环境态 + 有真本事的上下文唤起」**。

译宝是目前唯一同时具备「真本事」（插件 + 风险闸门 + 感知）和「生命感」（桌宠 + 主动 + 记忆）两侧的实现。主线就是把这两侧焊死。

---

## 2. 现状盘点：主线上什么已经有了

**已落地（代码 + 测试双证）：**

- 桌宠十态（`Avatar.vue:41`：idle/listen/think/work/say/success/error/notify/drowsy/stretch）+ idle 随机眨眼 + 久坐 stretch 做操，reactive-pet 已实装
- 感知三源（A 前台 app / B 屏幕树+视觉 / C idle）+ Keychain 加密 SQLite + 三层隐私过滤（`perception.py`，36 条测试）
- 每日离线 Distiller → pattern/insight/event + 晨间反刍（`distiller.py`，25 条测试）
- watch 主动搭话 + 后台盯命令（`watch.py`/`watch_service.py`，36 条测试）
- Feed 主屏 + widget（agents/notes/reminders 三插件供卡）+ 回顾时间线
- 收件箱化：待批准三区 + 批量确认（替代连环弹窗）
- 设置：记忆管理三件套（可见/可改/可删）+ 自主权旋钮三档（`proactive.level` = quiet/bubble/full）
- **能力表面 Slice 1**：`panel` 事件不再抢顶层 tab；主屏内 Stage/Focus 两档（`Home.vue:76-179`）
- **SessionState 分层快照四层齐全**，且比原设计更进一步——见下节

**纸面未落（设计齐全、代码为零）：**

- **Inline 回执 / Peek 探窗**——`presentation` 当前只有 `"stage" | "focus"`（`Home.vue:77`），简单动作（存素材、查一条信息）也只能开 Stage，违反调研 §3.1 的「能在回执里完成，就不要打开面板」
- **sidecar 侧表面协议**——`ActionResult`（`ipc.py:28-33`）只有 `panel: str | None`，没有 `presentation`/`attention`/`object`/`origin`。宿主裁决完全靠前端猜
- **活动轨 Activity Shelf**——全仓库零实现。长任务退出表面后就「失踪」，违反调研 §4
- **TaskTimeline**——零实现。当前 ConversationHistory / Feed 两者混用，缺「给用户追溯当前任务」的第三层（调研 §12.8）
- **情感账户 / 照料循环**——调研 §2.1 反复验证的「舍不得关」机制，零实现
- **Dock 条**（主屏底部固定 4–5 个常用插件）——零实现

**阻塞项（与主线无关但卡住一切）：**

- `npm run build` 红：`SettingsView.vue:664-666` 三个类型错误（`KEY_PROVIDERS.includes()` 不收窄），自 `d43c856`（8/9）起潜伏。因 `tauri.conf.json:9` 的 `beforeBuildCommand: npm run build`，**release 打包链是断的**，无法真机验收
- **零 CI**：仓库无 `.github/`，900 条 pytest + 59 条 vitest + cargo check 全靠本地手跑。上一条就是这么漏的

### 2.1 SessionState 分层快照：已完成，且比原设计更进一步

commit `f6ba072` 那段 8 行 TODO（scene/panel/chat/interact 四层、逐层容错、不重放工具）曾写在 `app/src/lib/capability-snapshot.ts` 里，该文件后来在 `3b831e6` 被删——**不是丢失，是被 `app/src/state/` 这套模块取代**。四层现状：

| 层 | 落点 | 容错配置 |
|---|---|---|
| scene（布局壳） | `state/domains/surface.ts` `SCENE_KEY` | TTL 24h |
| panel（面板数据） | 同上 `PANEL_KEY` | TTL 24h + 500KB 配额，超限只保壳 |
| chat（草稿/滚动/筛选） | `state/domains/conversation.ts` `ConversationUIState` | 记录级校验，非法即丢 |
| interact（面板内交互态） | `surface.ts` `INTERACT_KEY` | TTL 1h + 50KB 配额，**超限不落盘** |

TTL 与配额的分档正好落实了原 TODO 的优先级排序（草稿/进行中活动 > 滚动筛选 > 面板内部状态），interact 的「乐观可失效」也照做了。逐层容错见 `surface.ts:5-7`（scene 坏跳整条链 / panel 坏降级为有壳无数据 / interact 坏静默跳过）；幂等硬约束被逐字实现在 `restore-orchestrator.ts:9-12`——「恢复只重建 UI 呈现，不重放工具/模型动作」，并额外加了 restored 标记抑制「启动即写」回环与可重入保证。

**比原设计更进一步的地方**：原 TODO 设想的是前端一个 `yb-session-v1` 大快照；实际落地按**持久性分权**——耐久数据（会话消息 + 元数据）整体搬到 Rust `session_db.rs`，主进程做唯一写者，从架构上消灭多窗双写；前端只保留单窗易失 UI 态走 IndexedDB。分工写在 `conversation.ts:9`、`:63`、`:100`。

因此本主线**不再规划这一项**。唯一的后续动作是：Phase 1 扩 `presentation` 到四档时，须同步升 `surface:scene` 的 schema version（`types.ts:124` 当前仍是 `"stage" | "focus"`）——已写进 Phase 1 计划 Task 4。

---

## 3. 四个阶段

### Phase 0 · 止血（前置，半天）

不属于主线，但主线每一阶段的验收都要真机跑，打不出包就没有验收。

修类型错误 → 跑通一次 release 打包 → 建 CI（pytest + vue-tsc + vitest + cargo check）。

> 范围外：仓库分支/worktree 清理已延后（不阻塞任何验收）；`feat/coding-r2` 由**单独计划**管理，不在本主线的任何阶段内评估或处置。

计划：`docs/superpowers/plans/2026-08-13-phase0-stopbleed.md`

### Phase 1 · 能力表面收口（Slice 2 + 3）

**目标：让「任务拥有屏幕」真正成立。**

Slice 1 已经消灭了「跳页」，但只做了 Stage/Focus 两档重表面，留下两个反效果：简单动作被迫开大面板；长任务退出表面后无处可寻。

四件交付：

1. **表面协议下沉到 sidecar**——`ActionResult` 扩 `presentation`（inline/peek/stage/focus，**建议非命令**）、`attention`（quiet/suggest/focus）、`object`、`origin`；manifest 声明插件支持的表面范围与最小宽度
2. **Inline 回执卡**——简单结果在过程行原地收束为宿主原生卡（最多两个动作：撤销/展开），不开面板
3. **Peek 探窗**——从调用锚点 matched-geometry 长出的轻浮层，Esc/点空白/完成即缩回。首次授权在此完成
4. **宿主裁决器 + 活动轨**——模型/插件自动最多到 Peek，Stage/Focus 必须用户明确意图；运行中/待批准/已完成统一进 Activity Shelf 胶囊，点击恢复上次表面与滚动位置

计划：`docs/superpowers/plans/2026-08-13-phase1-inline-peek-shelf.md`

**验收线**（取自调研 §15，可自动化的部分写成测试）：简单动作默认不开面板；模型建议不自动展开 Stage/Focus；对话/草稿/滚动/当前对象在展开收起后保持；后台任务退出表面后仍可追踪、停止、恢复。

### Phase 2 · TaskTimeline 与可恢复会话

**目标：让「它在我不看的时候干的活」可追溯、可恢复。**

Phase 1 的活动轨需要一个权威存储支撑，否则胶囊一刷新就没。这一阶段建三层数据的清晰分工（调研 §12.8）：

| 数据 | 作用 | 归属 |
|---|---|---|
| ConversationHistory | 给模型继续推理 | 已有（`history.py` 按 conversation_id 分桶） |
| **TaskTimeline** | 给用户追溯当前任务 | **本阶段新建**，sidecar 权威存储 |
| Feed / Inbox | 跨任务提醒 | 已有（`feed.py`） |

TaskTimeline 的存储归属沿用 §2.1 已确立的分权原则：**耐久数据归 Rust / sidecar 权威存储，前端只做投影**——不要在 IndexedDB 里再造一份任务历史。

另一件事是让大窗/浮窗共享同一 `SurfaceSession` 而非两份副本（调研 §15 验收条 10）。

**硬约束**（写进测试）：恢复只重建 UI 呈现，**不重放工具/模型动作**（`restore-orchestrator.ts` 已确立此约束，TaskTimeline 恢复须沿用）；时间线只存用户可理解的摘要与引用，不复制完整敏感工具结果。

### Phase 3 · 桌宠生命感与情感账户

**目标：占住差异化护城河——竞品缺的那一侧。**

- **环境态**：任务状态透进桌宠（Live Activity 式：小、活、可展开），`Coding · 改 4 个文件`、`日历 · 等你确认` 一眼可知，但不抢焦点
- **照料循环**：调研 §2.1 反复验证的禀赋效应机制（VPet 4 天 3000+ 评测 98% 好评的核心）。需单独设计，避免做成廉价的饱食度数值
- **idle 深化 + 对环境有反应**：「它注意到你」比「它陪你玩」更戳人——感知 A/C 源已有数据，接到桌宠表现层
- **打扰纪律**：在已有 `proactive.level` 三档之上加「打扰预算」，用保守策略换长期居住权（Clippy 教训）

Phase 3 开工前需要一份独立 spec——照料循环是产品设计问题不是工程问题，不能直接写实施计划。

### Phase 4 · coding 旗舰化 + 分发

- coding 接入 Focus 场 + 活动轨，成为「专注场」这一档的旗舰验证场景（调研 §13 场景 C）

> coding 插件自身的能力演进（权限交互 / rewind / plan mode 等）走单独计划，本阶段只负责**把 coding 接进能力表面体系**，不规划其内部功能。
- 签名公证 + 打包分发——当前 `macOSPrivateApi: true` 已排除 App Store，需开发者证书站外分发。这是「从自用到能给别人用」的唯一门槛

---

## 4. 明确不做

- ❌ 不追通用 computer-use 盲区点击精度。三轮打不绿说明是方法天花板不是投入不足。a11y 可及区维持现有防回归线即可
- ❌ 不铺新插件冲数量。调研 §6 反模式第一条：「把功能清单堆长当 OS 感」
- ❌ 不做 Windows 基座。调研 §1.2 结论：「做深单平台比做全平台现实」
- ❌ 不在 Phase 1/2 动视觉抛光。`docs/plan/ui-plan.md` 的 8 项 UI 债留到表面模型稳定后一次性收
- ❌ 不恢复 plan checkbox 纪律（见下）

---

## 5. 文档纪律的调整

29 份 plan 里只有 4 份 checkbox 勾满，但代码大多已落地——superpowers 的 spec→plan→勾选闭环事实上已失效，且造成「文档显示没做、其实做了」的反向误导（如 coding 产品化计划显示 0/40，而 `_cc_reader.py`/`HistorySkill` 早在 main 上）。

**新纪律：plan 的 checkbox 不再要求维护，改为在对应 spec 末尾追加「实装记录」段**（记录落地日期、关键决策变更、验证命令与结果、待真机验收项）。这是仓库里已被证明能坚持下来的形式——`os-feel-design.md:140-145`、watch 系列 spec 都是这么写的。

---

## 6. 阶段间的硬依赖

```
Phase 0（止血）
   └─> 所有阶段（真机验收前提）

Phase 1（Inline/Peek/活动轨）
   └─> Phase 2（TaskTimeline 为活动轨提供权威存储）
        └─> Phase 4（coding 接 Focus + 活动轨）

Phase 3（生命感）—— 与 1/2 无强依赖，可并行；但需先出独立 spec
```

Phase 1 是最大杠杆：Slice 1 已把路铺好（scene 持久化、Stage/Focus、panel 不抢页），补上 Inline/Peek/活动轨之后，能力表面这套模型才算闭环，之后每个插件都自动受益。

---

## 7. 实装记录

### Phase 0（2026-08-13）

**止血与 CI：**
- 修 `SettingsView.vue` 搜索 key 类型收窄三错（`KEY_PROVIDERS.includes()` 不收窄联合类型），`vue-tsc`/`vite build` 恢复 exit 0；`tauri build --debug` 真机打包出 `译宝.app`，release 打包链恢复。
- 新建 `.github/workflows/ci.yml` 四道闸门：sidecar pytest（uv，dev extra）、frontend（npm ci + vue-tsc + vite build + vitest）、rust（prepare-dist + cargo check + cargo test）。同步了 `package-lock.json`（此前与 package.json 不同步，`npm ci` 必挂）。

**验证：** sidecar 905 passed；vue-tsc / vite build / vitest 65 passed / cargo check 全绿。CI 尚未在 GitHub 端实跑（本地预演通过；仓库未 push，分支保护待开通）。

### Phase 1（2026-08-13）

**协议新增字段与默认值（`ipc.py` `ActionResult`）：**

| 字段 | 类型 | 默认值 | 语义 |
|---|---|---|---|
| `presentation` | `"inline"\|"peek"\|"stage"\|"focus"\|None` | `None` | 技能**建议**的展示级别，非命令 |
| `attention` | `"quiet"\|"suggest"\|"focus"` | `"suggest"` | 打扰级别：quiet 只进活动轨 |
| `object` | `{type,id,title}\|None` | `None` | 跨应用接力对象，不依赖面板 DOM |

`loop.py` 的 `_with_surface_hints(payload, result, origin)` 把三字段 + `origin`（发起 action id）并进 panel 事件 payload——必须在 `_redirect_to_focused_webview` **之后**并入（它重建 dict 会丢新字段）。旧插件不声明 → `presentation=None`、`attention="suggest"`，宿主按老规则推断，向后兼容。

**manifest 声明（`_load_panels`）：** `[[panel]]` 新增 `surfaces`（合法值 inline/peek/stage/focus，非法值静默过滤、全非法回落全档）与 `min_width`（非正数忽略）；解析结果随 `panel_payload` 顶层透传（`surfaces`/`min_width`），供宿主裁决回落。

**裁决器硬规则（`app/src/lib/surface-policy.ts`）：** 模型/插件**自动最多展开到 peek**——stage/focus 必须有用户明确意图（explicit）；attention=quiet 一律只进活动轨不展开；面板不支持的档位向下回落，**连最低支持档都超过自动上限时不自动展开、只记账进活动轨**；已开着的 stage/focus 不因新结果降级，但降级豁免不得越过面板支持范围。11 条单测固化。

> **Review 修正（`ab19488`）：** 首版存在三处击穿。① `supported` 回落的兜底会把档位抬回 stage/focus，而自动上限在回落**之前**施加、不再复查——面板声明 `surfaces=["stage","focus"]`（coding 类面板的自然声明）时，模型自作主张即可开 stage/focus，正是本阶段要根治的「结果一回来就跳页」；且兜底取的是未排序原数组首元素，结果依赖 manifest 里的声明顺序。② `current` 抬升绕过 `supported`，会给只支持 inline/peek 的面板返回 focus。③ Inline「展开」/ 活动轨点开硬编码 stage、不查 `supported`。
> 修正要点：支持范围先升序规整（消除顺序依赖）；**自动上限挪到回落之后施加**；explicit 路径统一走裁决器。该缺陷当时未触发——`_load_panels` 未声明时默认全档，而当时无插件声明 `surfaces`；但它会在该特性首次被真正使用时立刻发作。

**接线：** HomePlugins 本地时间窗推断降级为 `explicit` 的**来源之一**（用户点插件库确属明确意图），`presentation`/`attention`/`surfaces` 改从 panel 事件 payload 读取；Home 的 `onPanelAvailable` 改用 `decideSurface` 裁决，按结果分发到 活动轨 / Inline / Peek / Stage/Focus。

**Inline/Peek 复用的动效时序来源：** PeekSurface 的 grow/collapse 复用 `HomePlugins.vue` Slice 1 的 matched-geometry 常量（240ms `cubic-bezier(0.22,0.61,0.36,1)` 弹性生长 / 200ms 反向缩回，`clip-path` + `fill:forwards` + `prefers-reduced-motion` 跳过），未另起一套转场。`--yb-z-inline`/`--yb-z-peek`/`--yb-z-popover` 补入 `tokens.css`（禁止裸 z-index 数字）。

**活动轨取舍：** 三态胶囊（运行中/待批准/已完成）数据源本阶段接**内存态**（`panelState` + 待批准队列 `onPendingConfirms` + quiet 结果）；刷新即丢，权威持久化推迟到 Phase 2 TaskTimeline——已知且可接受的分期取舍。**位置决策变更**：计划原定「顶栏右侧」，实装改为底部固定横条——顶栏右侧已被当前能力胶囊 + 窗口按钮占满且无横向滚动空间；底部横条不抢任何 tab 的既有控件，窄窗自然横滚。`surface_id` 本阶段用 `panel` ref 代替。

**验证：** sidecar 905 passed（+5 新测：表面透传×2、manifest 声明×3）；vue-tsc / vite build / vitest **70 passed**（裁决器 11 条：初版 6 + review 回归 5）；cargo check Finished。

**待真机验收**（自动化无法覆盖，对应计划 Task 7 Step 2 七条）：①「记一下这句话」只出 Inline 不开面板；②模型自作主张最多 Peek、不切顶层导航；③插件库明确点击直达 Stage；④Peek Esc 缩回原锚点、背后对话保持；⑤coding 长任务 → 活动轨运行中胶囊 → 点击恢复；⑥待批准琥珀胶囊不抢输入焦点；⑦`surfaces=["inline","peek"]` 的插件被要求 focus 回落 peek 不崩。真机打包已通过（`tauri build --debug` 出 `.app`），七条交互待用户跑 App 逐条确认。
