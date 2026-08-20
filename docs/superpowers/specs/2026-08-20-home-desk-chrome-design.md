# 主屏 chrome

日期：2026-08-20  
状态：已落地  
后续：装配模型见 `2026-08-21-home-parts-assembly-design.md`（已落地第一版）。「加骨架 = 再写一个 shell」已由预设取代。

## 问题

主屏默认是三栏（左零件 / 中对话 / 右本次）。整窗即桌是另一套骨架，不是替换缺省。两套都是一等公民：加骨架靠登记，不要在三栏实现上打补丁，也不要删掉三栏。

## 正交轴

| 轴 | 职责 | 扩展 |
|---|---|---|
| theme | 深浅 | `data-theme` |
| finish | 釉/半径/影/色温 | `FINISHES` + tokens 覆盖块 |
| chrome | 零件落哪个槽、中间摊什么 | `HOME_CHROMES` + `CHROME_SHELL` |
| 零件 | 显隐、大小、瓷/玻璃 | `HOME_WIDGETS` |

组件读 `--yb-*`、`data-widget`、当前 chrome 对象上的字段（`surface` / `sessionVariant` / `widgetSlot`）。禁止散落 `if (chrome === 'rails')` 复制整棵布局树。

## 已发布

缺省 **`rails`（三栏）**。对话流在中栏，左栏叠零件，右栏本次。

**`desk`（整桌）** 是第二套：石面桌，纸是工作面，会话是书脊，「本次」从纸边 Peek 长出。`groupPages()` 把 user+run 收成页。

## 加一套骨架

1. `HOME_CHROMES` 加一项（槽位、surface、sessionVariant、可折叠槽）
2. 写一个 shell 组件（自己管布局，不必复用 desk grid）
3. `CHROME_SHELL` 登记
4. 若中间面不是 thread/paper，再加 surface 分支

## 落点

| 职责 | 文件 |
|---|---|
| 登记表 / 持久化 | `app/src/lib/home-chrome.ts`（`yibao-chrome`，缺省 `rails`） |
| shell 映射 | `app/src/lib/home-chrome-ui.ts` |
| 三栏壳 | `HomeRails.vue` |
| 整桌壳 | `HomeDesk.vue` |
| 纸面 | `HomePaper.vue` |
| 零件 | `home-widgets.ts` |

小窗宠物对话不跟这根轴。不复制一套 desk 色板。无限画布 / 3D 仍非目标。
