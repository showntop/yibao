# 小窗能力表面设计（2026-08-13）

> **一句话：** 让 Phase 1 建好的能力表面模型触达小窗——把「面板 = 弹独立浮窗」换成对话流里的**痕迹三态**，并补上声明式插件表达表面建议的渠道。
>
> **上位 spec：** `docs/superpowers/specs/2026-08-13-pet-os-mainline-design.md` §3 Phase 1.5
> **设计依据：** `docs/research/2026-08-09-capability-surfaces-design.md` §5（Inline）、§12.7（presentation 是建议非命令）、§16（反模式：多个自由漂浮面板）

---

## 1. 为什么

Phase 1 落地了裁决器、Inline 回执、Peek 探窗、活动轨，四道闸门全绿，但真机验收条 ①「记一下这句话」只出 Inline 不开面板」在**大窗小窗都不成立**。两个成因互相独立。

**缺口 A · 声明式插件没有声明渠道。** Phase 1 把 `presentation`/`attention` 加在 `ActionResult` 上，只惠及代码式 Skill。`plugins.py` 全程未解析这两个字段，`DeclarativeTool` 返回的 `ActionResult` 恒为默认值。而 notes、reminders 这批最该走 Inline 的轻量插件**全是声明式的**——`notes.keep` 在 manifest 里只有 `panel = "notes:list"`。于是大窗实际走：`suggested=null` → 回落 `stage` → 非 explicit 封顶 → 开 **Peek**，不是 Inline。

**缺口 B · 小窗未接裁决器。** 裁决器只接进了 `Home.vue`/`HomePlugins.vue`。`App.vue:727-738` 的 `case "panel"` 仍是无条件 `openPanel()`（`invoke("open_panel_window")`，独立 Tauri 窗口）外加宠物窗收回球形态。这比大窗的 Stage 更重，且正是调研 §16 列为反模式的「多个自由漂浮面板制造窗口管理负担」。而小窗是桌宠常驻主入口，「记一下这句话」这类轻量动作多数就发生在这里。

> **范围澄清：** 仓库有五处 `case "panel"`，只有 `App.vue` 是缺口。`PanelApp.vue` 是浮窗内部的内容渲染器（`setCurrent` 填 payload，不决定开窗）；`HomeChat.vue` 仅维护「⇢ 协作」关联气泡且注释已明确「不主动抢页面」；`CapabilityConversationRail.vue` 仅 push 一条活动行；`HomePlugins.vue` 已接裁决。四者职责各异，**不抽共享 `useBrainSurface` 层**——那会把职责不同的代码强行归一，造出错误的抽象。

---

## 2. 痕迹三态

小窗的物理形态是宠物球加可展开对话框（收起 320×300，展开 `lib.rs:82-113` 定为 **360×520**），**没有 Stage/Focus 的空间概念**，不能套用大窗四档。

改为：每个面板事件在对话流里留下一条**痕迹**，有三种密度。

| 态 | 形态 | 可点 | 何时出现 |
|---|---|---|---|
| **卡** | 白底描边带阴影，图标 + 两行文案 + 「收起」「展开」 | 是 | 最新事件且裁决为 `inline` |
| **活行** | 左对齐裸文字，accent 色 + `›`，无边无底 | 是 → explicit → 开浮窗 | 最新事件且裁决为 `peek` / `show=false`；或卡点「收起」后 |
| **死行** | 同上但 `#93a3b6`、无箭头 | 否 | 被更新的面板事件取代后 |

**转换规则只有两条：**

1. 新面板事件到达 → 上一条痕迹（无论卡还是活行）降为**死行**
2. 卡上点「收起」→ 降为**活行**

因此流里**永远最多一条痕迹是可操作的**。卡若被晾着直接来了新事件，跳过活行直接变死行——用户仍可从插件视图进入，不是死路。

**几条已定的细节：**

- **按钮文案「忽略」改「收起」。** 忽略不再意味着消失而是降密度，原词在骗人。
- **死行统一显示「面板名 · 计数」**，不保留卡上的「已记录」。痕迹的职责是「哪个能力被碰过」，事情本身上方的对话已说清。
- **计数取 `data.rows?.length`，取不到就只显示面板名。** `plugins.py:211` 的 db query 恒返回 `data={"rows": rows}`，panel 事件本就透传 `data`，**零协议改动**。但这只对 db 类声明式工具成立，http 工具与代码技能的 `data` 形状不保证——因此计数不得写进任何断言，缺失是正常降级路径。
- **卡收缩成行用高度塌缩 + 淡出，不复用 Phase 1 的 matched-geometry。** 活行是裸文字、卡是白底描边，视觉差异大到无法做几何匹配。沿用现有动效 token 与 `prefers-reduced-motion` 跳过。

### 2.1 开窗动作不需要携带面板身份

卡上的「展开」与活行点击是同一个动作：置 explicit 标志后 `openPanel()`。

**它们不需要记住自己对应哪个面板。** 因为可操作的痕迹永远是最新那条，而浮窗内容渲染器（`PanelApp.vue` 的 `case "panel"` → `setCurrent`）本就跟着最新面板事件走——两者天然指向同一个面板。

这条性质直接消掉了 Phase 1 里 `peekPanel` 那类「面板 A 的标题配上面板 B 的内容」的错位 bug 的**整个可能性**，而不是靠再打一次快照去躲。「只有最新一条可点」这个约束的收益不止于克制，也在于此。

### 2.2 为什么死行不可点

「统一可回溯」的准确边界是：**可回溯的是「发生过什么」，不是「回到那一刻」**。

死行若可点，等价于在对话流里散落一堆入口，正是 §16 要避免的窗口管理负担的变体。而「回到闪念列表」永远有路——小窗本就有插件视图（`PetView = "chat" | "plugins"`，`App.vue:1112` 切换钮，`launchPlugin` 直调 `{id}.list`）。

于是职责清晰分离：**历史归对话流，导航归插件视图。**

这一决定同时避开了两个坑：不必为旧痕迹存快照数据（对话流不会变成数据坟场，也不与 Phase 2 TaskTimeline 的权威存储职责冲突），也不必重放工具动作（不触碰 Phase 2「恢复只重建 UI 呈现，不重放工具/模型动作」的硬约束）。

---

## 3. 小窗表面映射：`decideSurface` 原样够用

**不需要给裁决器加 `autoMax` 入参**（Phase 1.5 初版计划中的该任务作废）。小窗的形态本就不是四档，直接映射裁决器现有输出即可：

| `decideSurface` 输出 | 小窗 |
|---|---|
| `inline` | 卡 |
| `peek` / `show=false` | 活行 |
| `stage` / `focus` | 直接开浮窗（`openPanel()` + 收回球形态） |

关键性质：裁决器在非 explicit 时**本就封顶到 `peek`**（`surface-policy.ts:11` 的 `AUTO_MAX`），所以 `stage`/`focus` 只可能在 explicit 时出现——**「模型自动调用绝不在小窗开浮窗」这条硬规则自动成立**，无需额外代码。

`attention=quiet` 与「`stage` 非 explicit」在小窗合流为同一个活行。这是有意简化：小窗没有 Peek 那一档，两者本来就没有可区分的表现。

---

## 4. explicit 的三个来源，全在前端

前端本就看得见用户输入文本，也有 `loadPlugins()` 拿到的插件名列表，因此**无需协议加 `explicit` 字段**。三个来源统一置同一个时间窗标志，`case "panel"` 复用 `HomePlugins.vue:321` 同构的判断（`requestedPlugin === plugin && Date.now() <= requestedUntil`）：

1. **插件视图点击**（`App.vue:449-456` `launchPlugin`）——**这是回归修复**：接上裁决器后若不置标志，点插件将开不了窗，而今天它能开纯粹因为 `case "panel"` 无条件开窗
2. **活行点击**
3. **窄规则文本匹配**——用户消息命中「打开/展开/给我看/显示」+ 已知插件名或面板名

窄规则**宁缺毋滥**：只认强信号（动词与宾语都来自有限集合），漏报即退化为活行、用户点一下，语义仍然通顺；误报的代价是抢屏，因此宁可漏。

> 不做「让 LLM 自行声明 explicit」：守门人不能是被守的人，模型有系统性动机把自己的调用标成用户要求的，而误判方向不对称。

---

## 5. 协议改动

只有一处。`[[tool]]` 上新增 `presentation`（`inline|peek|stage|focus`）与 `attention`（`quiet|suggest|focus`）。

**声明位置的关键区分：属于 `[[tool]]` 而非 `[[panel]]`。** `[[panel]]` 上已有的 `surfaces`/`min_width` 描述**面板的能力范围**（静态属性）；`presentation` 是**单次调用的建议**——同一个 `notes:list` 面板，被 `keep` 触发时该 inline（只是「记下了」的回执），被 `list` 触发时该 stage（用户要浏览）。声明在 panel 上无法区分。

同理，`explicit` 是**单次调用**的属性，静态声明覆盖不了，这也是它不进 manifest 的原因。

**实现：** tool 的 manifest 字典在 `plugins.py:555-562` 整体传进 `DeclarativeTool(pid, spec, registry)`，故在 `DeclarativeTool.__init__`（`:127`）内读取即可，无需改 loader。合法值集合复用 `_load_panels`（`:363`）中 `surfaces` 的校验常量，非法值静默过滤。`run()` 有 **7 处 `ActionResult(success=True, ...)`**，用私有 helper 统一注入避免漏改；**失败分支不带**——失败结果不该建议展开面板。

**声明：** `notes.keep` 加 `presentation = "inline"`。`list`/`delete` 不加，保持默认推断。

---

## 6. 明确不做

- ❌ **小窗不做 ActivityShelf。** 大窗的底部横条存在是因为大窗主体是工作面、对话只占一栏；小窗主体就是对话流，痕迹天然按时间排列，再挂横条是重复。
- ❌ **不加 `count` 协议字段**（`data.rows` 已够，且缺失可自然降级）。
- ❌ **不加 `explicit` 协议字段**（前端信息已足）。
- ❌ **不做 `autoMax` 入参化**（裁决器现有封顶已使硬规则自动成立）。
- ❌ **不抽共享 `useBrainSurface` 层**（五处 `case "panel"` 职责各异）。
- ❌ **旧痕迹不存快照、不重放动作**（见 §2.2）。

---

## 7. 测试策略

**sidecar（pytest）：** 声明式 tool 携带 `presentation`/`attention` 进 `ActionResult`；非法值静默过滤；不声明时回落默认（`None` / `"suggest"`）；失败分支不带表面建议。

**前端（vitest）：** 窄规则匹配是纯函数，必须可测——命中「打开闪念」置 explicit、未命中不置、动词或宾语任一不在集合内不置。痕迹三态的转换（新事件降死行、卡收起降活行）为纯状态函数，一并单测。

`decideSurface` 本身**不改动**，其 11 条既有单测即为回归防线。

**真机验收**（自动化不可覆盖）：

1. 小窗说「记一下这句话」→ 卡，**不开浮窗**、宠物窗不收回球形态
2. 小窗任何模型自动调用 → 不开浮窗
3. 小窗说「打开闪念列表」→ 窄规则命中 → 直接开浮窗
4. 小窗点插件视图里的插件 → 开浮窗（**回归项**）
5. 连记三条 → 只有最新是卡，其余为死行，流不刷屏
6. 大窗说「记一下这句话」→ Inline 回执而非 Peek（补齐 Phase 1 验收条 ①）

---

## 8. 实施顺序

1. sidecar 声明式 tool 表面声明 + `notes.keep` 声明 —— 独立可验，解缺口 A，同时使大窗验收条 ① 成立
2. 痕迹三态组件与状态机 —— 纯前端，可单测
3. 小窗接裁决器（`case "panel"` 改写 + `BubbleMsg` 扩痕迹字段 + `launchPlugin` 置标志）—— 解缺口 B 并修回归
4. 前端窄规则 explicit
5. 四道闸门 + 真机验收 + 实装记录
