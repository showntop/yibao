# 译宝 · 桌面形象重塑 设计文档

- 日期：2026-07-23
- 状态：待复核（draft → pending review）
- 作者：denny × Claude（brainstorming）
- 溯源：可交互 mockup 在 `.superpowers/brainstorm/<session>/content/`（gitignored，本地）：`character-directions` / `-resculpted` / `-fresh` / `-topper` / `-states` / `-bubble`。

---

## 1. 背景

译宝是 Tauri v2 + Vue 3 桌面 AI agent，差异化定位是「**操作电脑 / 盯桌面工作流**」（区别于纯陪伴型桌宠）。当前形象是纯内联 SVG 团子（`app/src/components/Avatar.vue`），配治愈系暖奶油 + 杏子橙（`app/src/assets/tokens.css`）。

设计 review 结论：形象「可爱，但匿名」。三宗罪：

1. **粉嫩** —— 奶白 + 蜜桃 + 腮红 = 婴儿色 / 糖果色，与「能干」天然打架，配色在拆台。
2. **圆呼呼** —— 纯椭圆、无手脚、无轮廓。圆软读「婴儿」不读「能动手的搭档」，也正是「匿名」的成因。
3. **没立体感** —— 只有一个径向渐变 + 一道柔投影，无体积 / 重量 / 光感，像贴纸贴在桌面。

外加三项具体问题：`work` 状态色 `#ff8a5c` = 品牌主色（操作中与按钮同色，语义糊）；无成功 / 失败情绪；透明窗未做鼠标穿透（`setIgnoreMouseEvents` 零实现，132×140 透明区挡桌面点击）。

用户定位确认：**工作干活为主，兼顾娱乐生活为辅，最好有人格。**

**与既有设计的关系**：本设计细化并更新 `docs/superpowers/specs/2026-07-16-desktop-agent-design.md` §7「形象」。与之一致：沿用 v1 五状态机（待机/听/思考/说话/工作）、置顶透明窗 + 鼠标穿透（§7 原计划「动态切换 `setIgnoreMouseEvents`」，代码尚未落地，本设计实现它）、对话气泡（§3 架构已列）、Live2D 仍属 v2。本设计新增：品牌身份（鹅蛋 + 天线）、天青配色、天线状态灯、成功/失败情绪、收起态 peek 气泡。两份不冲突，形象细节以本文为准。

## 2. 目标 / 范围

**In scope（本设计）：**

- 重塑角色造型：鹅蛋体重 + 立体光影 + 小手 + 天线。
- 配色迁移到「天青 Sky」主题（token 化，暖 → 清爽）。
- 状态系统：5 活动态 + 2 情绪态，由天线灯驱动。
- 收起态「对话气泡 peek」：展示 AI 回复。
- 透明窗鼠标穿透。

**Out of scope：**

- Live2D 迁移（远期；SVG 长期保留为低功耗 / 老机器 fallback）。
- 三主题切换器（v1.5；tokens 保持变量化、可换肤）。
- 交互侧重构（hover 快捷钮 / 右键菜单 / 热键升级 —— 属另一设计，见 review 第二节）。

**成功标准：** 团子有唯一可识别的身份与人格；状态一眼可读且 `work` 与主色脱钩；收起态可见回复；透明区不挡桌面点击；仍保留温度（暖 → 清爽，不滑向冷冰冰）。

## 3. 设计决策

### 3.1 造型：鹅蛋 + 立体 + 小手

- **身体**：鹅蛋 / weeble 体重（上窄下宽、重心低）→ 不再「圆呼呼」，有重量感。
- **立体**：落地实阴影 + 底部接触阴影（重量）+ 顶部高光 + 右下核心阴影 + 左侧边缘反光（光感）→ 从贴纸变「小手办」。
- **小手**：两侧短手 nub → agency，读「会动手」，`work` 态可做手势。
- **身体路径**（viewBox `0 0 120 128`）：
  `M60 18 C76 18 84 34 84 57 C84 84 74 102 60 102 C46 102 36 84 36 57 C36 34 44 18 60 18 Z`
- **技术**：SVG 线性渐变身体 + 径向高光 / 核心阴影（裁剪在身体 `clipPath` 内）+ `feGaussianBlur` 软阴影 + 边缘反光描边。眼 / 嘴墨色 `#3f372e`；腮红压到 ~22–30% 透明（只剩气色，不婴儿色）。

### 3.2 配色：天青 Sky（品牌默认，token 迁移）

治愈系暖奶油 → 天青清爽，token 驱动（替换 `tokens.css` 暖色组）。

- 身体渐变：`#ffffff → #f2f6fb → #d5e0ee`
- 核心阴影 `#8498b0` @0.42 / 接触阴影 `#5e7088` @0.26 / 边缘反光 `#ffffff`
- 主色 accent：`#4d90c4`（天青蓝）→ 替换原 `--yb-accent #ff8a5c`
- 文字 `#3f372e`（保留）/ 次文字 `#8a9aac`
- 卡片 / 气泡底 `#ffffff` / 边 `#e6edf5`
- 保留温暖气色：腮红 `#ffb89a` @低透明（仅气色）

三主题切换延后：tokens 保持变量化；薄荷 / 净白作为未来可加主题，v1 只交付 Sky。

### 3.3 头顶：天线（兼状态灯）

细杆（`#6a7c92`）+ 顶端发光点（径向 glow + 实心点）。三理由：

1. 最强剪影记忆点（解决最后一块「匿名死角」——光 dome）；
2. 顶端点 = 状态灯，颜色 + 动效随状态变，省掉独立状态可视化；
3. 天线天然读「AI / 在接收 / 能干」，贴合定位，且足够干净不破坏清爽。

否决项：小芽（温柔但能干感弱）、呆毛（人格强但剪影记忆弱）。

### 3.4 状态系统（天线灯 + 微表情）

天线灯颜色 + 动效，叠加轻微面部变化。状态切换仍由现有大脑事件驱动（`App.vue` `onEvent`），**不改事件协议，只改 Avatar 渲染**。

| 状态 | 灯色 | 动效 | 微表情 |
|---|---|---|---|
| 待机 idle | `#aab8c8` | 暗淡呼吸 | 微笑 |
| 聆听 listen | `#2fb0b5` | 脉动 + 声波 | 张小嘴 |
| 思考 think | `#8e7cf0` | 旋转虚线环 | 眼上抬 |
| 操作 work | `#f2a03c` | 脉动 | 专注眉、一字嘴 |
| 说话 say | `#58b368` | 发光 + 声波 | 张嘴 + 舌 |
| 成功 ✓ | `#3e8e5a` | 星星 pop | 笑眼（0.4s） |
| 出错 ⚠ | `#e5484d` | 抖动 | 垂眼 + 汗（0.4s） |

- **`work = #f2a03c` 琥珀，与天青主色 `#4d90c4` 彻底脱钩** —— 解决「操作中与按钮同色」。
- 七色跨色相，色盲下可分。
- 成功 / 失败为叠加在 idle 上的短暂情绪（≤0.4s），非独立常态。失败由现有 `error` 事件触发；成功由 action 完成触发——当前 `action_result` 事件不区分成败，实现期二选一：① 由「`action_result` 且短窗口内无后续 `error`」推断成功；② 给 `action_result` 加 `success` 标志（更准，需大脑侧小改）。

### 3.5 收起态「对话气泡 peek」

- **触发**：AI 回复到达（`final_reply` / `reminder`）→ 团子旁升气泡（0.3s rise）。
- **内容规则**：≤2 行纯文本 → 直接显示；含 markdown / 表格 / 代码 / 超长 → 摘要（复用 `final_reply` 首段）+ 一行预览 + 「点开看 →」chip。
- **生命周期**：~6s 或失焦自动收起；hover 保持；点气泡 = 展开完整聊天窗（复用现有 `expand`）。
- **定位**：团子默认 dock 右上 → 气泡在团子左侧（带指向团子的 tail）；随展开方向 `dir` 自适应（与 `window.ts` 现有 nw/ne/sw/se 一致）。
- **体量**：临时 / 小 / 自动消失，非常驻面板；收起态团子体量不变，不破坏桌宠感。

### 3.6 透明窗鼠标穿透（行为）

- 现状：132×140 透明窗整块吃事件，团子周围透明区挡桌面点击。
- 方案：默认 `setIgnoreMouseEvents(true, { forward: true })`（穿透且仍转发 mousemove）；团子本体 hot-region 用 JS 监听 `mouseenter` → 临时 `setIgnoreMouseEvents(false)` 切回可交互，`mouseleave` → 恢复穿透（BongoCat 同款做法）。
- 待验证：macOS 正常；Linux Wayland 无标准 always-on-top / 穿透 API → 降级为普通窗 + 提示。

## 4. 文件影响

- `app/src/assets/tokens.css` —— 暖色组 → 天青组；新增状态灯色板（`--yb-state-idle/listen/think/work/say/success/error`）。
- `app/src/components/Avatar.vue` —— 重写：鹅蛋身体 + 立体光影 + 小手 + 天线 + 状态灯 + 七态表情 / 动效；**保留**对 click / longpress / drag 的 pointer 状态机（`Avatar.vue:32-63`）。
- `app/src/components/PeekBubble.vue` —— 新增（收起态气泡）；现有 `Bubble.vue` 继续用于展开态聊天气泡。
- `app/src/App.vue` —— 收起态接入 PeekBubble（`onEvent` 的 `final_reply` / `reminder` 触发；自动收起计时；点击 `expand`）。
- `app/src/lib/window.ts` —— 新增 mouseenter / leave 切 `setIgnoreMouseEvents`；气泡定位随 `dir`。
- `app/src-tauri/src/lib.rs` —— 托盘 `icon_as_template(true)` + 单色图标（顺带修 macOS 暗色菜单栏）。
- 不改：大脑事件协议、插件架构、sidecar。

## 5. 风险 / 待验证

- SVG `feGaussianBlur` 多层在低端机 / 大尺寸可能卡 → 状态灯与身体分层，必要时降 filter层数。
- 暖 → 冷 token 迁移会同时影响**面板窗**（工具箱等共享 tokens）→ 全局视觉一次性切换，需回归走查（`DesignPreview.vue` 辅助）。
- 气泡与 `expand` 方向 / 多显示器边界 → 复用 `window.ts` 屏幕剩余空间判定。
- Linux Wayland 穿透 / 置顶受限 → 降级提示。

## 6. 明确不做

- **Live2D**：远期。当前 SVG 方案作为低功耗 fallback 长期保留；仅当需要口型同步 / 细腻情绪时再引入 Live2D，届时 SVG 降为 fallback。
- **三主题切换器**：v1.5。
- **交互侧重构**（review 第二节）：另开设计。

---

## 7. 实现现状（as-built，2026-07-24）

实现过程对原计划有几处关键调整（真机反馈 + Tauri v2 实测驱动）。**实际建成以本节为准**，上方 §3 为初版设计、保留作背景。

### 7.1 固定窗口（替代 resize）——根治 resize 闪烁
原计划收起/展开/气泡走窗口 resize 补间；真机实测透明无边框窗 resize 会闪（macOS 合成器绘中间帧，且 setSize/setPosition 非原子）。**改为固定窗口**：主窗恒为 360×520、透明、置顶、永不缩放。收起态只渲染团子（右上角），其余透明 + 点击穿透放行桌面；展开态渲染聊天。**无 resize → 无闪烁**。代价：收起时窗口占位变大，靠点击穿透消化。

### 7.2 打字机气泡（替代 peek）——说话时逐字
原 §3.5 的 peek（短/长摘要 + 点开看）改为**说话态打字机气泡**：AI 回复流式 chunk 进气泡、天然逐字 + 闪烁光标；说话时显示，说完/展开即收。固定窗口下气泡出现在团子左侧，无需 resize。

### 7.3 点击穿透（Rust 全局光标追踪）——替代 JS setIgnoreMouseEvents
原 §3.6 借鉴 Electron 的 `setIgnoreMouseEvents(on,{forward})`；**实测 Tauri v2 JS API 只有 `setIgnoreCursorEvents(boolean)`、无 forward**，忽略后收不到 mousemove 切不回。改由 Rust 侧 `device_query` 每 40ms 读全局光标，落在团子热区 / 展开窗内 = 可交互、否则 `set_ignore_cursor_events` 穿透。前端用 `set_interactive_full` 标志位告知 Rust 当前整窗可交互（展开 / 气泡）还是仅团子热区。坐标单位按 scale 换算，首次真机已核对。

### 7.4 角色最终造型
天青鹅蛋（身形 `scaleY 0.78` 压扁、脸反向缩放保持圆）+ 立体光影 + 小手 + 小短腿 + 天青领巾 + 天线（兼七态状态灯）+ idle 氛围光晕。**无帽子、无头发**——试过赤陶斜帽、深棕短发均被否（与极简天青风冲突），方向收敛起"极简、同色、贴身"（见偏好记忆）。落地投影等少量字面色留待 token 化清理。

### 7.5 idle 生气
随机眨眼（JS 随机间隔 2.2–5.8s）+ 东张西望（眼区缓慢左右瞥）+ 身体呼吸 + 天线灯慢脉冲 + 氛围光晕呼吸。哈欠试两版不像、已移除。

### 7.6 文件（最终）
- `tokens.css`：暖 → 天青 + 身体 / 七态灯令牌。
- `Avatar.vue`：鹅蛋角色（立体 + 小手 + 腿 + 领巾 + 天线状态灯 + 七态 / idle 生气）；scaleY 压扁身形、脸反向缩放。
- `SpeechBubble.vue`：说话态打字机气泡（新建；PeekBubble 已删）。
- `App.vue`：固定窗口下说话气泡接入（流式 → 气泡、说完 / 展开即收）；expand/collapse 不再 resize。
- `window.ts`：固定窗口（resetWindowSize）；setInteractiveFull 通知 Rust 穿透模式（expand/collapse/tween/speakOpen/speakClose 已移除）。
- `lib.rs`：托盘 `icon_as_template(true)` + 单色图标；`device_query` 光标轮询线程 + `set_interactive_full` 命令。
- `tauri.conf.json`：主窗 360×520 固定。

### 7.7 已知待办
- 落地投影等字面色 → 收进 token（Minor）。
- 交互侧重构（hover 快捷钮 / 右键菜单 / 热键升级）→ 另开。
- Live2D → 远期。
