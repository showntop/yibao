# 主屏画布落点 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 主屏落点从 `grid-template-areas` 换成舞台上的框；摊法仍跟预设走；Frame 不再为会客写布局 CSS。

**Architecture:** `HOME_PRESETS` 改为 `presentations` + `frames` + 可选 `attach` / `compact` / `pluginFrame`。`resolveAssembly` 合成框（用户 `layouts[preset]` 覆盖动过的框）。`HomeFrame` 绝对定位宿主。拖与拖边写回偏好。

**Tech Stack:** Vue 3, Vitest, 现有 `home-assembly.ts` / `HomeFrame.vue` / `home-widgets.ts`

## Global Constraints

- 一切皆零件，皆可拖，皆可改大小；允许重叠。
- Frame 禁止 `[data-preset="salon"]` 布局选择器和按零件 id 拉伸。
- 不写 `HomeXxx.vue` 壳；业务里不 `if (preset === 'salon')` 复制布局树。
- 坐标是舞台 CSS 像素；可用 `right`/`bottom` 钉边。
- 小窗宠物对话不跟这根轴。
- 规格：`docs/superpowers/specs/2026-08-21-home-canvas-placement-design.md`

---

### Task 1: 装配数据改成框

**Files:**
- Modify: `desktop/src/lib/home-assembly.ts`
- Modify: `desktop/src/lib/home-assembly.test.ts`
- Modify: `desktop/src/lib/home-widgets.ts`（`layouts` 偏好）
- Modify: `desktop/src/lib/home-chrome.ts` 及 `home-chrome.test.ts`（去掉区名 slot）
- Modify: `desktop/src/lib/home-frame.test.ts`

**Produces:** `FrameBox`, `resolveAssembly` 返回 `items[].frame`；三份预设有出厂框；无 `grid.areas`。

- [ ] 重写类型与 `HOME_PRESETS` 为 presentations + frames
- [ ] `resolveAssembly` 合并 prefs.layouts、解析钉边、处理 attach
- [ ] 改测试：salon 不再断言 areas；断言 frame 存在、compact 只留说话、Frame 源码无 salon 布局选择器（可先标 skip 到 Task 2）
- [ ] chrome `widgetSlot` 删除或恒空，测试跟着改

### Task 2: Frame 只当舞台

**Files:**
- Modify: `desktop/src/components/HomeFrame.vue`
- Modify: `desktop/src/lib/home-frame.test.ts`

**Produces:** 零件按 `frame` 绝对定位；删除 salon/desk/rails 布局 CSS。

- [ ] 舞台 `position: relative`，宿主 `position: absolute` + left/top/width/height/zIndex
- [ ] 删 `[data-preset="salon"]` 及按 region 的栏宽/垫
- [ ] 折叠按钮第一版：隐藏 `groups.left` 或暂时保留 left hidden 逻辑的最小替代
- [ ] 测试：Frame 不含 salon 布局选择器

### Task 3: 拖与拖边写回偏好

**Files:**
- Modify: `desktop/src/lib/home-widgets.ts`
- Modify: `desktop/src/components/HomeFrame.vue`（或小的 `HomePartHost.vue`）
- Test: `desktop/src/lib/home-widgets.test.ts`

**Produces:** 移动改 left/top；拖边改 width/height；卸贴；刷新仍在。

- [ ] `setFrame(preset, id, box)` / `detach(preset, id)`
- [ ] 宿主拖柄与边；work 从顶条拖
- [ ] 点击提升 z

### Task 4: 出厂三间房对齐

**Files:**
- Modify: `desktop/src/lib/home-assembly.ts` 里三份 `frames`
- 目测 rails / desk / salon

**Produces:** 出厂快照看起来仍是三栏、整桌、会客一席，但已是画布。

- [ ] 按现窗微调出厂数字
- [ ] salon 簇相对舞台居中（合成时用舞台宽高）
- [ ] compact 快照可说话
