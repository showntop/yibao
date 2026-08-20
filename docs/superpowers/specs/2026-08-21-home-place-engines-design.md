# 主屏落点：grid + canvas

日期：2026-08-21  
状态：已落地  
取代：`2026-08-21-home-canvas-placement-design.md`（把三种见面都做成画布框）。目录、kind、摊法、theme / finish、小窗宠物对话仍有效。

## 一句话

零件和摊法是一份。落点是两套引擎：结构用 CSS grid 模板，自由摆放用画布框。Frame 只按 `place` 分一次，不按预设名分叉。

## 产品

结构是我们排好的房间。画布是你自己排的房间。

| 预设 | place | 用途 |
| --- | --- | --- |
| 三栏 `rails` | `grid` | 办事。左零件、中对话、右本次。可折叠左右栏。 |
| 整桌 `desk` | `grid` | 摊开。纸是工作面，书脊/便条贴纸，四周余光。 |
| 会客 `salon` | `grid` | 见面。格子居中、轨道按内容，不拉长零件。 |
| 画布 `canvas` | `canvas` | 自己摆。每块都是窗。出厂不要长得像三栏。 |

结构模式不拖。画布才拖、磁吸、改大小。显隐/材质全局；摊法与落点按预设。画布坐标只写 `layouts.canvas`。

折叠是 grid 轨道上的区名（`left`/`right`），不是零件组。收起后中间 `1fr` 变宽。

## 数据

预设：`place` + `presentations` + `absent` + 二选一：

- `grid`：`tracks`（一行栏，可 fold）或 `columns`/`rows`/`areas`（二维）；`stacks` 每区零件栈（允许多块）；`grow`；`pluginArea`
- `canvas`：`frames` + `attach` + `pluginFrame`（沿用现框引擎）

## Frame

`place === "canvas"`：宿主 `frameStyle` + 拖。  
否则：舞台 `display:grid` 用模板；区内 flex 栈。  

禁止：`[data-preset="salon"]` 布局；业务组件读 `preset` / `place`（只读摊法）。

## 验收

- 第五份结构预设只加模板 + 摊法，Frame 零改。
- 结构模式拖不出框；画布没有栏折叠。
- 切画布再切回三栏，仍是栏，不带画布坐标。
