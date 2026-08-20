# 主屏装配：命名槽 + dock + 预设

日期：2026-08-21  
状态：已被 `2026-08-21-home-place-engines-design.md` 取代（结构预设用 grid，画布才用框）。kind / 摊法 / 目录仍有效。

## 一句话

零件是积木。预设自带一张格子图（自己起名），再把积木放进去或贴到另一块上。三栏和整桌是两份快照，不是两套产品。没有一张全球槽位表。

## 三个词

**格子。** 位置是预设里的区域名，不是坐标，也不是框架写死的 `work` / `board`。`desk` 可以有 `book`、`compose`、`mind`；`rails` 可以有 `left`、`main`、`right`；下一份预设自己起名。框架只认识「这份快照里有哪些区、怎么排」。

**dock。** 可选。零件贴的是**另一块零件的边**，不是全球槽。书脊 `dock: { to: "chat", edge: "start" }`。被贴的那块一藏，贴上去的跟着收；不必为此先登记一个 `work-start`。

**预设。** 「格子图 + 谁进哪 + 哪种摊法」存成一个名字。`rails` / `desk` 是两份出厂快照。点名字就是套用。

不是：每块零件一个 x/y/宽/高。也不是：先申请加入全球五槽，第三套桌才能诞生。

## 正交轴（改完之后）


| 轴      | 职责                 | 扩展                            |
| ------ | ------------------ | ----------------------------- |
| theme  | 深浅                 | `data-theme`                  |
| finish | 釉/半径/影/色温          | `FINISHES`                    |
| **零件** | 有哪些积木、什么种类、能怎么摊、贴谁 | 目录登记                          |
| **装配** | 当前谁在哪一格、用哪种摊法      | 预设 + 用户改动                     |
| **框架** | 按装配画格子             | 一个 `HomeFrame`，不再按模式分两个 shell |


组件读零件字段和当前装配，不写 `if (chrome === 'desk')`。

## 零件种类

种类不同，样子就该不同。不要做成同一套圆角卡换标题。


| kind      | 是什么             | 例子                  | 样子                            |
| --------- | --------------- | ------------------- | ----------------------------- |
| `work`    | 工作面（可没有）        | 对话流、纸               | 摊开的面，不是瓷片                     |
| `input`   | 说话的入口           | 输入条                 | 可贴某块零件，也可单独进一个区               |
| `nav`     | 在工作面之间切换        | 会话                  | `list`（栏里的列表）或 `spine`（贴纸切口）  |
| `context` | 当前这一次           | 本次                  | `inspector`（右栏）或 `note`（纸边便条） |
| `glance`  | 桌上的一瞥           | 认知、今日、身份、需要你、进行中、提醒 | 瓷/玻璃，小中大                      |


`kind` 只说明样子和默认贴法，**不是场上必须出现的角色**。可以没有 `work`：只要瓷片、只要会话列表、只要输入，都是合法装配。


同一零件可以有多种 **摊法（presentation）**：会话是 `list` 或 `spine`，工作面是 `thread` 或 `paper`。换摊法不是换零件。

插件主屏卡（已有 `[[panel]] type = "widget"`）登记为 `glance`，来源是插件，不另做一套。

## 加载与渲染

已落地：`resolveAssembly` 合成 `Assembly`，`HomeFrame` 按格子图画并 `provide`。零件用 `useLiveAssembly()` + `faceOf` 读摊法。`chat` 按 presentation 选 `HomeChatThread` / `HomeChatPaper`。插件 widget 启动后 `syncPluginParts` 并进目录，进预设的 `pluginRegion`。`yibao-chrome` 仍是预设名。

### 数据（三份，加载时合成一份）

**1. 目录** — 代码常量，不进 localStorage。

```ts
{ id: "chat", kind: "work", presentations: ["thread", "paper"] }
{ id: "sessions", kind: "nav", presentations: ["list", "spine"] }
{ id: "mind", kind: "glance", presentations: ["map", "tile"], defaultSize: "l" }
```

每条另挂一个视图组件。插件 glance 启动后再并进这份表。

**2. 出厂预设** — 代码常量。`rails` / `desk` 各一份。

```ts
{
  id: "desk",
  grid: {
    columns: "156px minmax(0,1fr) 156px",
    rows: "auto auto minmax(0,1fr) auto auto",
    areas: [
      "mind book today",
      "need book tasks",
      "plug book remind",
      "me book .",
      ". compose .",
    ],
  },
  place: [
    { id: "chat", region: "book", presentation: "paper" },
    { id: "sessions", dock: { to: "chat", edge: "start" }, presentation: "spine" },
    { id: "now", dock: { to: "chat", edge: "end" }, presentation: "note" },
    { id: "composer", region: "compose" },
    { id: "mind", region: "mind", presentation: "tile" },
    { id: "today", region: "today" },
    { id: "need", region: "need" },
    { id: "tasks", region: "tasks" },
    { id: "remind", region: "remind" },
    { id: "identity", region: "me" },
  ],
}
```

`rails` 同形，格子是三栏（`left` / `main` / `right`），没有 dock。

**3. 用户偏好** — `localStorage`。旧键继续用：`yibao-chrome` 当预设名，`yibao-home-widgets` 当瓷片偏好。

```ts
{
  preset: "desk",          // 缺省 rails
  hidden: ["remind"],
  size: { mind: "m" },
  material: { today: "glass" },
  order: ["identity", "mind", "today", /* … */],
}
```

偏好不写格子图、不写 dock。换预设时格子和摊法跟出厂走，hidden/size/material/order 可留下。

### 合成（一次函数，不写在 Vue 里）

```
catalog × PRESETS[preset] × prefs
  → 逐条 place：
      id 不在目录 / hidden / region 不在这份 grid    → 跳过
      dock.to 不在场（被跳过或根本没放）             → 跳过
  → 同一 region 内按 order 排
  → Assembly { grid, items[] }
```

不打回出厂。坏的那一条没有，其余照画。空区塌掉。

### 渲染

1. `home.ts`：theme → finish → 读偏好里的预设名 → mount `Home`
2. `HomeFrame` 吃 `Assembly.grid`，铺 CSS grid（`grid-template-areas`）
3. 每个 `item`：
   - 有 `region` → 放进对应区（`grid-area: mind`）
   - 有 `dock` → 作为 `to` 那一块的子节点，按 `edge` 贴（chat 的 start = 书脊，end = 便条）
4. 视图只读自己的字段：`presentation` / `size` / `material`。会话气泡、输入提交仍由现有会话宿主管，不进装配数据

窄窗是预设上的媒体规则（desk ≤960 只留 `book`+`compose` 相关 item），不是第三份预设。

### 和现在代码的对应

| 现在 | 合成之后 |
|---|---|
| `HOME_CHROMES[id]` + `CHROME_SHELL[id]` | `PRESETS[id].grid` + `.place` |
| `surface` / `sessionVariant` / `peekDensity` | 各零件的 `presentation` |
| `widgetSlot` | `place[].region` 或 `place[].dock` |
| `HomeRails` / `HomeDesk` | 一个 `HomeFrame` |
| `HomeChat` 里按 chrome 分 thread/paper 两棵树 | `PART_VIEWS.chat` + `viewOf("chat", face)` |

## 怎么写一份清单

三层分开写，不要揉成「每块零件一个 x/y/宽/高」。


| 层        | 答什么                      | 今天落在哪                                                                    | 记什么                                      |
| -------- | ------------------------ | ------------------------------------------------------------------------ | ---------------------------------------- |
| **目录**   | 场上能有哪些积木                 | `HOME_WIDGETS`（缺 work / input）                                           | `id` · `kind` · 能怎么摊 · 默认大小/材质 · dock 约束 |
| **预设**   | 这份底板里谁进哪一格、用哪种摊法         | `HOME_CHROMES.widgetSlot` + `surface` / `sessionVariant` / `peekDensity` | `{ part → { slot, presentation } }`      |
| **用户偏好** | 这块瓷片开没开、小中大、瓷还是玻璃、同槽谁先谁后 | `yibao-home-widgets`                                                     | `hidden` · `size` · `material` · `order` |


大小只对 `glance`（以及 rails 里当列表的会话）用 `s` / `m` / `l`。不记像素。工作面填满 `work`；书脊宽 28px、便条宽 176px，是摊法自带的尺寸，不是用户可调的 size。

位置写这份预设里的区名，或 dock 到某块零件的哪一侧。`order` 只在同一区里排先后。跨区是改 `region`，不是改 order。

### 当前目录


| id         | kind    | 默认大小      | 默认材质 | 能怎么摊                      |
| ---------- | ------- | --------- | ---- | ------------------------- |
| `chat`     | work    | （填满 work） | —    | `thread` · `paper` · `talk` |
| `composer` | input   | （内容高）     | —    | `bar`                     |
| `sessions` | nav     | l         | 瓷    | `list` · `spine` · `cards` |
| `now`      | context | m         | 瓷    | `inspector` · `note`      |
| `identity` | glance  | m         | 瓷    | `tile` · `seat`（会客坐下）   |
| `mind`     | glance  | l         | 瓷    | `map`（三栏全图）· `tile`（整桌瓷片） |
| `today`    | glance  | s         | 瓷    | 瓷片                        |
| `need`     | glance  | m         | 瓷    | 瓷片                        |
| `tasks`    | glance  | m         | 瓷    | 瓷片                        |
| `remind`   | glance  | s         | 瓷    | 瓷片                        |
| `plugin:*` | glance  | m         | 瓷    | 启动后并进目录，进预设 `pluginRegion` |


`chat` 和 `composer` 在目录里，不在 `HOME_WIDGETS`（瓷片偏好只管 glance/nav/context）。

### 当前两份预设的位置

`**rails`（三栏）** — 三根栏就是这份预设的三个区。左栏里的零件按 `order` 从上往下叠。

```
[ left: identity → mind → today → need → tasks → remind → sessions ]
[ main: chat(thread) ]
[ composer 在 main 里、对话下面 ]
[ right: now(inspector) ]
```

`**desk`（整桌）** — 格子名是 desk 自己的。书是复合体：书脊 dock 到 chat 的 start，便条 dock 到 chat 的 end；输入进 `compose` 区。

```
mind(156) |              book               | today(156)
need      |  spine dock chat.start          | tasks
plug      |  + paper + peek dock chat.end   | remind
me        |                                 |  .
 .        |  compose（左垫 36px 对齐纸）      |  .
```


| 零件         | rails                         | desk                                      |
| ---------- | ----------------------------- | ----------------------------------------- |
| `chat`     | 区 `main` · thread            | 区 `book` · paper                         |
| `composer` | 在 `main` 里、对话下               | 区 `compose`（不跟书脊齐高）                     |
| `sessions` | 区 `left` · list              | dock `chat` start · spine（露出 4 张、两字）     |
| `now`      | 区 `right` · inspector        | dock `chat` end · note（176px）            |
| `identity` | 区 `left`                     | 区 `me`（左下，贴底）                            |
| `mind`     | 区 `left` · map               | 区 `mind`（左上）· tile                       |
| `today`    | 区 `left`                     | 区 `today`（右上）                            |
| `need`     | 不在场                           | 区 `need`（mind 下）                         |
| `tasks`    | 不在场                           | 区 `tasks`（today 下）                       |
| `remind`   | 不在场                           | 区 `remind`（tasks 下）                      |
| `plugin:*` | 区 `left`                       | 区 `plug`（need 下）                         |


窄窗（≤960px）desk 只留 `book` + `compose`，board 上的 glance 全藏。

用户改 glance 的显隐/大小/材质/同槽顺序，换预设时这些偏好可留；工作面摊法和 dock 跟预设走，不跟偏好走。

## 格子（预设自带，不是全球枚举）

扩展靠「这份预设多几个区名」，不靠「先改框架的槽位表」。

预设给两样东西：

1. **区怎么排** — 一份 grid / 分栏描述（列宽、行、`areas`）。区名是这份预设的局部词汇。
2. **零件怎么放** — `{ id, region? }` 进某个区，或 `{ id, dock: { to, edge } }` 贴到另一块。

`rails` 的区可以是 `left` / `main` / `right`。`desk` 的区可以是 `mind` / `book` / `compose` / `today`…。第三套不必借用 `work` 这个词。

框架稳定的是这套**布局语言**（区 + 贴边），不是那五个名字。书脊贴纸，用 dock 表达，不必在全球表里占一个 `work-start`。

空区塌掉。没有工作面时，其余区铺满窗口。

## 坏引用，不是「非法装配」

不设产品禁令。你关掉纸、不要输入、只要一圈瓷片，都算数。

加载时只处理**坏引用**：区名在这份格子图里不存在、dock 的对象不在场、零件 id 未登记。那一块跳过（或贴不上就藏），**不要整桌打回出厂**。用户改过的其余部分留下。

## 三份预设（与今天的观感对齐）

**`rails`（缺省，三栏）**

- 区：`left` / `main` / `right`
- chat 在 `main`，thread；composer 在对话下面
- 会话列表、glance 在 `left`；本次 inspector 在 `right`

**`desk`（整桌）**

- 区：`mind` / `need` / `me` / `book` / `compose` / `today` / `tasks` / `remind`
- chat 在 `book`，paper；sessions dock 到 chat.start；now dock 到 chat.end
- composer 进 `compose`（纸前面的桌，独占一行）
- glance 进各自的区

**`salon`（会客）**

- 格子是窗正中一席（约 46rem）。四列规整：左列认知/译宝同宽，右三列等分（需要你 / 今日 / 提醒），台词、名片、输入叠在这三列上，左右齐边
- 区：`mind` / `need` / `today` / `remind` / `seat` / `talk` / `cards` / `say`
- chat 在 `talk`，只摊刚说的几句，对话框跟译宝对齐；identity 在 `seat` 坐下；sessions 是桌面上的名片，不扇、不盖台词
- 没有「本次」、没有进行中、没有插件格。输入进 `say`，跟对话框同宽。空是房间四周的气，不是家具之间的洞

没有 chat 时，dock 到它的书脊/便条跟着不画；其余区照常。用户改 glance 的显隐/大小/材质，换预设时这些偏好可留。

## 怎么加东西


| 要加     | 做法                                      |
| ------ | --------------------------------------- |
| 新瓷片    | 目录加一条 `glance` + 一个视图，丢进某份预设的某个区       |
| 工作面新摊法 | `chat` 上加一种 presentation                |
| 新的主屏形态 | 新预设：自己的格子图 + 放置表。不写新 shell，也不改全球槽位表   |
| 插件一瞥   | 已有 widget 协议对上 `glance`                 |


## 和今天代码的关系

落点：

| 职责 | 文件 |
|---|---|
| 目录 / 预设 / 合成 | `app/src/lib/home-assembly.ts` |
| chrome 适配（旧 API） | `app/src/lib/home-chrome.ts` |
| 框架 | `HomeFrame.vue` |
| 视图登记 | `home-assembly-ui.ts`（`PART_VIEWS`） |
| 会话宿主 | `HomeChat.vue`（只提供 `#chat` / `#sessions` / `#now` / `#composer`） |
| 对话摊法 | `HomeChatThread.vue` / `HomeChatPaper.vue` |
| 插件 glance | `HomePluginGlance.vue` |
| 瓷片偏好 | `home-widgets.ts` |

`HomeRails` / `HomeDesk` / `CHROME_SHELL` 已删。设置页换的是预设，不是骨架。`home-chrome.ts` 只保留 `yibao-chrome` 持久化和旧测试用的推导字段。

小窗宠物对话不跟这根轴。不复制一套 desk 色板。不要像素坐标、不要重叠、不要 3D。

## 不做

- 零件 `position: absolute` 或无限画布。
- 为第三种桌面再写一个 `HomeXxx.vue`。
- 在业务组件里保留 `if (preset === 'desk')` 复制布局树。
- 把「场上必须有纸 / 输入不能进某区」写成加载器硬约束。
- 把纸和输入做成和认知同一套瓷片皮肤（种类不同，样子就不同）。

