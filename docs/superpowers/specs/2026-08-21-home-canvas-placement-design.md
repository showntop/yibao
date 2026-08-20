# 主屏装配：摊法 + 画布

日期：2026-08-21  
状态：被取代  
被 `2026-08-21-home-place-engines-design.md` 取代：三栏 / 整桌 / 会客改回 CSS grid，只有第四预设 `canvas` 仍用画布框。

## 一句话

见面方式是摊法。摆放是画布。一切皆零件，皆可拖，皆可改大小。会客底层用画布实现，不靠 Frame 里的预设名 CSS。

## 公理

- 对话、输入、会话、本次、译宝、认知都是零件。没有结构件例外。
- 零件都能拖、都能改宽高。
- 位置是舞台上的框，不是区名，不是无限乱放的像素游戏关卡：原点在舞台，可用边钉应对窗变。
- 允许重叠。点到的那一块 z 在上。
- 新预设 = 摊法表 + 一桌出厂框。不写新 `HomeXxx.vue`，Frame 不出现 `if (preset === 'salon')` 复制布局。

## 两层正交


| 层 | 管什么 | 谁改 |
| --- | --- | --- |
| 摊法 | 这块零件长什么样、这份预设怎么见面 | 出厂预设 |
| 画布 | 框：`left` / `top` / `width` / `height` / `z`，可选 `right` / `bottom` 钉边 | 出厂快照；用户拖、拖边之后写进这份预设的偏好 |

会客仍然是会客：`identity` 用 `seat`，`chat` 用 `talk`，`sessions` 用 `cards`。它们出现在哪，是快照里的框。

## 三个词

**舞台。** `HomeFrame` 里那一块相对定位的面。坐标原点左上，单位 CSS 像素。拖动时铺 8px 格子作参照；靠近零件边才吸住并画对齐线。这不是 `grid-template-areas`，零件仍是绝对框。

**框。** 一个零件在舞台上的矩形。与 CSS `position: absolute` 同一套边：`left`+`width` 或 `right`+`width` 或 `left`+`right`；竖直同理。出厂三栏右栏钉 `right`，窗变宽它仍贴右边。用户拖走过之后记下 `left/top/width/height`，钉边解开。

**贴边。** 可选约束：`attach: { to, edge, gap }`。被贴的框一移，贴上去的跟着算。用户把贴着的那块拖开，卸贴，自己成独立框。书脊、便条用这个，不必再占一个区名。

## 数据

### 1. 目录

仍是代码常量。`HOME_PARTS` 不变。插件 glance 启动后并进目录。

### 2. 出厂预设

```ts
{
  id: "salon",
  label: "会客",
  presentations: {
    chat: "talk",
    identity: "seat",
    sessions: "cards",
    composer: "bar",
    mind: "tile",
    today: "tile",
    need: "tile",
    remind: "tile",
  },
  frames: {
    mind: { left: 24, top: 24, width: 148, height: 168, z: 1 },
    identity: { left: 24, top: 204, width: 148, height: 148, z: 2 },
    chat: { left: 184, top: 204, width: 520, height: 160, z: 2 },
    // …
  },
  attach: {
    // desk：sessions 贴 chat.start；now 贴 chat.end
  },
  absent: ["now", "tasks"],
  compact: { presentations: { chat: "talk", composer: "bar" }, frames: { /* 只留说话 */ } },
}
```

谁在场：`frames` 里有的，减去 `absent`，减去用户 `hidden`。不在这份 `presentations` 里的零件用目录缺省摊法。

插件：出厂给一个 `pluginFrame`（左上宽高）。多块插件在这份框里往下叠，间距 12。用户拖走过某一块之后那一块独立成框。

### 3. 用户偏好

旧键 `yibao-chrome` 仍是预设名。`yibao-home-widgets` 仍管 `hidden` / `material`。`size`（小中大）和全局 `order` 不再驱动落点。

新键（或同一 JSON 下的 `layouts`）：

```ts
{
  hidden: ["remind"],
  material: { today: "glass" },
  layouts: {
    salon: {
      frames: { mind: { left: 40, top: 80, width: 180, height: 200, z: 4 } },
      attach: { sessions: null }, // 显式卸贴
    }
  }
}
```

只记动过的框。没写过的走快照。`layouts` 按预设分，区名本来就是这份房间的词，坐标也是。材质、显隐跨预设可留。

## 合成

```
catalog × PRESETS[preset] × prefs.layouts[preset] × hidden
  → 零件 id 未登记 / hidden / 在 absent 且用户没单独放回来    → 跳过
  → attach.to 不在场                                         → 当独立框；贴不上不打回出厂
  → Assembly { preset, items: [{ id, kind, presentation, frame, attach }] }
```

`frame` 是已经把钉边解析成舞台可用的 left/top/width/height（解析时需要舞台宽高）。贴边的项可以没有自己的 left：合成时从宿主框推。

不打回出厂。坏的那一条没有，其余照画。

## 渲染

1. Frame 铺舞台，不铺 `grid-template-areas`。
2. 每个在场零件一块绝对定位的宿主，样式来自合成后的 `frame`。
3. 视图只读摊法：`talk` / `paper` / `thread`、`seat`、`cards`。不读预设名。
4. 宿主提供拖（移动）和边（改宽高）。对话面从顶条拖，避免和选字抢手势。点一下把 z 提到全场最高。

Frame **禁止**：`[data-preset="salon"]` 写布局、点名 `mind`/`today` 拉伸、按预设改 host flex。舞台背景可以按 finish/theme，不按预设名分三套布局。思考态的雾可以挂在舞台上，不挂在 salon 选择器上。

三栏折叠：`groups.left` / `groups.right` 是出厂组，不是区。折叠按钮属于组缝，不属于译宝、也不属于 Frame 角上的装饰。点把手 = 把这组移出舞台，并让贴着缝的 work / input 框吃掉那条带宽（改 `left`/`width`，不恢复 grid 列）。译宝头像只打招呼。整桌、会客没有 `groups`，没有栏折叠。

## 手势

- **拖。** 指针跟着走。靠近别的零件边或舞台边（约 24px）才吸住，吸住时画对齐线；拉远才松开。零件贴着零件时留 8px 空隙；齐边（同一条 left/top）不留缝。松手时若没吸在边上，再落到 8px 格子。设置里「布局 → 恢复默认」清掉这份预设里拖过的框。
- **改大小。** 边和角。最小宽高按 kind：glance 96×72，input 高度不小于 44、宽度不小于 240，work 不小于 280×120，nav 不小于 72×72。写入 width/height。拖边同样走磁吸与松手落格。
- **贴边。** 出厂 `attach` 跟宿主走；用户拖走过贴着的零件 → `attach[id] = null`。格子磁吸是坐标约束，不是再引入区名。
- **重叠。** 允许。z 点击提升。

## 窄窗

预设可带 `compact` 快照（摊法 + 框）。≤960px 用它。用户在窄窗里拖过的记在 `layouts[preset].compact`，和宽窗 layouts 分开，避免把桌面框压进手机宽。

## 出厂三份（观感对齐今天，落点改成框）

数字是大约值，实现时按现窗量一次，以「看起来仍是那三间房」为准，不追求像素级复刻补丁时代的 salon 拉伸。

**rails**  
左：身份、认知、今日、会话，宽 280，从上往下叠。中：对话填剩余，输入贴底。右：本次宽 280 钉 right。会话 list，对话 thread，本次 inspector。

**desk**  
四周 glance 各自一块框（约 156 宽）。中间书：chat paper 钉 left/right 留出书脊和便条。sessions attach chat start，now attach chat end。输入在书前一条。

**salon**  
一席居中：出厂框按大约 46rem 宽的簇，算成相对舞台中心的 left/top（合成时用舞台宽高把簇放中间）。认知等 glance 在簇上沿，译宝坐下，台词在右，名片在下，输入贴台词。无本次、无进行中；插件不出场（`pluginFrame` 缺省则跳过）。

## 和今天代码

| 现在 | 之后 |
| --- | --- |
| `PresetGrid` + `place[].region` | `frames` + `presentations` |
| `collapsible` 区名 | 可选 `groups`；第一版可先不做折叠 |
| `pluginRegion` | `pluginFrame` |
| `HomeFrame` 按 areas 铺格 | 舞台 + 绝对框 |
| `[data-preset="salon"]` 布局 CSS | 删除 |
| widget `order` / `size` s/m/l | 不再驱动落点；菜单里小中大可删或变成写入一组默认宽高 |
| `defaultPeek` | `now` 在场且未 attach → 打开；attach 了 → 先收起 |

`home-chrome.ts` 继续用预设名当 `yibao-chrome`。`widgetSlot` 这类区名 API 删掉或改成返回空，测试跟着改。

## 不做

- 零件存全局 x/y 当桌面图标，跨预设共用一份坐标。
- 为第三种见面方式再写 `HomeXxx.vue`。
- 业务组件 `if (preset === 'salon')` 复制布局树。
- Frame 点名零件 id 做 salon 拉伸。
- 用 `grid-template-areas` 当落点（格子只负责磁吸，不是命名区）。
- 3D。
- 小窗宠物对话跟这根轴。

## 验收

- 加第四份预设：只加摊法表 + 框快照，Frame 零改动。
- 会客、整桌、三栏都能把任意在场零件拖走、拖边；刷新后偏好还在。
- 会客不出现「一格一瓷片所以拖了没动」；也不再靠 Frame 里 salon 选择器排版。
- 窄窗会客仍能说话（compact 快照）。
- 现有 hidden / material 不丢。
