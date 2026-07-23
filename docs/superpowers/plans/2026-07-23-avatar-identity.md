# 译宝 桌面形象重塑 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把译宝的桌面形象从「扁平暖色团子」重塑为「天青鹅蛋小手办 + 天线状态灯」，并补齐收起态对话气泡 peek 与透明窗鼠标穿透。

**Architecture:** 全部在 Tauri 壳的 Vue 前端（`app/src`）+ 一处 Rust（`app/src-tauri`）。token 驱动：先迁移 `tokens.css` 配色（暖→天青），再重写 `Avatar.vue`（立体 SVG + 七态），新增 `PeekBubble.vue` 并接入 `App.vue`，`window.ts` 加鼠标穿透，`lib.rs` 修托盘图标。不改大脑事件协议、插件架构、sidecar。

**Tech Stack:** Vue 3 (`<script setup>` + TS)、SVG + CSS 动画、Tauri v2 window API、Rust。

**Spec:** `docs/superpowers/specs/2026-07-23-yibao-avatar-identity-design.md`（细化和更新 `2026-07-16-desktop-agent-design.md` §7）。

## Global Constraints

- **配色锁天青 Sky**：主色 `--yb-accent: #4d90c4`；身体渐变 `#ffffff → #f2f6fb → #d5e0ee`；详见 Task 1。所有组件只消费 `var(--yb-*)`，不写死 hex（Avatar SVG 渐变 stop 例外，走身体 token）。
- **七态状态灯色板**（天线灯）：idle `#aab8c8` / listen `#2fb0b5` / think `#8e7cf0` / work `#f2a03c` / say `#58b368` / success `#3e8e5a` / error `#e5484d`。**work 必须与主色脱钩**（琥珀 ≠ 天青）。
- **保留接口**：`Avatar.vue` props `{ state: AvatarState; size?: number }`、emits `click`/`longpress`，以及现有 click/longpress/drag 手势状态机（`Avatar.vue:32-63` 的指针逻辑原样保留）。
- **Live2D 不做**（属 v2）；本次只在 SVG 内升级。
- **无前端单测框架**：仓库无 vitest/jest。每个任务的验证 = (1) `cd app && npx vue-tsc --noEmit` 通过；(2) `npm run dev` 后在 `http://localhost:1420/design.html` 目测；(3) 涉及行为的任务（穿透/气泡）在真机 `npm run tauri dev` 手动验证。不要为本次引入 vitest。
- **commit 规范**：中文 conventional，每任务结束 commit；末尾加 `Co-Authored-By: Claude <noreply@anthropic.com>`。

---

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `app/src/assets/tokens.css` | 全局设计令牌（配色/圆角/动效/状态灯） | 改：暖→天青 + 新增状态灯令牌 + 过渡别名 |
| `app/src/components/Avatar.vue` | 角色渲染 + 手势 | 改：整体重写（立体鹅蛋+小手+天线+七态） |
| `app/src/DesignPreview.vue` | 设计走查页 | 改：状态数组加 success/error |
| `app/src/components/PeekBubble.vue` | 收起态回复气泡 | 新建 |
| `app/src/App.vue` | 宠物窗编排 | 改：接入 PeekBubble + success/error 态 + busy 修正 |
| `app/src/lib/window.ts` | 窗口控制 | 改：新增鼠标穿透切换 |
| `app/src-tauri/src/lib.rs` | Rust 壳 | 改：托盘 `icon_as_template(true)` |
| `app/src-tauri/icons/icon-tray.png` | 托盘单色图标 | 新建（单色 template 用） |

---

## Task 1: 配色令牌迁移到天青 Sky

**Files:**
- Modify: `app/src/assets/tokens.css`（整文件替换 `:root` 内容）

**Interfaces:**
- Produces: 所有 `--yb-*` 令牌的天青取值；新增 `--yb-state-*`（七态灯）、`--yb-body-*`（身体）；保留别名 `--yb-idle/listen/think/work/say`、`--yb-dumpling-*` 指向新令牌，避免 Avatar 重写前（Task 2）其它组件引用断裂。

- [ ] **Step 1: 用天青版整体替换 `app/src/assets/tokens.css`**

```css
/* 设计令牌：宠物窗与面板窗共用（main.ts / panel.ts 各 import 一次）。
 * 主题「天青 Sky」：清爽白底 + 天青蓝 accent + 大圆角 + 弹簧动效。
 * 2026-07-23 由治愈系暖奶油迁移而来（token 驱动，全局一次性切换）。
 * 组件只消费变量；结构令牌（间距/字号/圆角/动效）不变。 */
:root {
  /* 间距梯度 */
  --yb-space-1: 4px;
  --yb-space-2: 8px;
  --yb-space-3: 12px;
  --yb-space-4: 16px;

  /* 字号梯度 */
  --yb-fs-sm: 11.5px;
  --yb-fs-md: 12.5px;
  --yb-fs-lg: 13.5px;
  --yb-fs-xl: 15px;
}

/* ============ 天青 Sky：清爽白底 + 天青蓝 + 大圆角 + 弹簧动效 ============ */
:root {
  /* 表面 */
  --yb-bg: #eef2f7;
  --yb-shell-bg: rgba(255, 255, 255, 0.86);
  --yb-glass-border: rgba(255, 255, 255, 0.7);
  --yb-blur: saturate(140%) blur(20px);
  --yb-shadow: 0 10px 32px rgba(40, 60, 90, 0.16);

  /* 文字 */
  --yb-text: #3f372e;
  --yb-text-dim: #8a9aac;

  /* 主色（天青） */
  --yb-accent: #4d90c4;
  --yb-accent-deep: #3d7aa8;
  --yb-accent-soft: #e7f0f8;
  --yb-bubble-ai: rgba(255, 255, 255, 0.94);
  --yb-bubble-user: rgba(231, 240, 248, 0.9);
  --yb-danger: #e5484d;
  --yb-danger-soft: rgba(229, 72, 77, 0.24);

  /* 形象身体（天青瓷白胎） */
  --yb-body-hi: #ffffff;
  --yb-body-mid: #f2f6fb;
  --yb-body-lo: #d5e0ee;
  --yb-body-ink: #3f372e;
  --yb-body-core-shadow: #8498b0;
  --yb-body-contact: #5e7088;
  --yb-body-blush: #ffb89a;
  --yb-body-stem: #6a7c92;

  /* 天线状态灯（七态） */
  --yb-state-idle: #aab8c8;
  --yb-state-listen: #2fb0b5;
  --yb-state-think: #8e7cf0;
  --yb-state-work: #f2a03c;
  --yb-state-say: #58b368;
  --yb-state-success: #3e8e5a;
  --yb-state-error: #e5484d;

  /* 圆角梯度 */
  --yb-radius-sm: 10px;
  --yb-radius-md: 14px;
  --yb-radius-lg: 18px;
  --yb-radius-xl: 24px;
  --yb-radius: var(--yb-radius-md);

  /* 动效：弹簧曲线 */
  --yb-ease: cubic-bezier(0.34, 1.56, 0.64, 1);
  --yb-dur: 0.22s;

  /* 组件表面（输入条/卡片/次级按钮） */
  --yb-surface: rgba(255, 255, 255, 0.78);
  --yb-surface-solid: #fbfdff;
  --yb-surface-border: rgba(40, 60, 90, 0.1);
  --yb-btn-neutral: rgba(40, 60, 90, 0.07);
  --yb-well: rgba(40, 60, 90, 0.05);
  --yb-shadow-soft: 0 2px 8px rgba(40, 60, 90, 0.08);
}

/* ============ 过渡别名：Task 2 重写 Avatar 后可删 ============ */
:root {
  --yb-idle: var(--yb-state-idle);
  --yb-listen: var(--yb-state-listen);
  --yb-think: var(--yb-state-think);
  --yb-work: var(--yb-state-work);
  --yb-say: var(--yb-state-say);
  --yb-dumpling-hi: var(--yb-body-hi);
  --yb-dumpling-lo: var(--yb-body-lo);
  --yb-dumpling-ink: var(--yb-body-ink);
  --yb-dumpling-blush: var(--yb-body-blush);
}
```

- [ ] **Step 2: 类型检查 + 构建**

Run: `cd app && npx vue-tsc --noEmit`
Expected: 无错误（CSS 改动不影响 TS，但确认未误改其它文件）。

- [ ] **Step 3: 目测全局配色切换**

Run: `cd app && npm run dev`，浏览器开 `http://localhost:1420/design.html`。
Expected: 整页底色变清爽冷白偏蓝，accent 变天青蓝；聊天气泡/看板/输入条都跟着切到天青，无残留杏色。再开 `http://localhost:1420/`（宠物窗）确认收起态底色一致。

- [ ] **Step 4: Commit**

```bash
git add app/src/assets/tokens.css
git commit -m "feat(avatar): tokens 暖奶油→天青 Sky，新增身体/状态灯令牌" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 2: 重写 Avatar.vue（立体鹅蛋 + 小手 + 天线 + 七态）

**Files:**
- Modify: `app/src/components/Avatar.vue`（整体替换）
- Modify: `app/src/DesignPreview.vue`（状态数组 + 标签加 success/error）

**Interfaces:**
- Consumes: Task 1 的 `--yb-body-*`、`--yb-state-*`、`--yb-accent`。
- Produces: `Avatar` props `{ state: "idle"|"listen"|"think"|"work"|"say"|"success"|"error"; size?: number }`、emits `click`/`longpress`（与旧版兼容，App.vue 无需改调用）。`state` 联合类型扩展了 `success`/`error`（Task 3 接入触发）。

- [ ] **Step 1: 整体替换 `app/src/components/Avatar.vue` 为下述内容**

> 保留原有指针状态机（click/longpress/drag 消歧）；重写 `<template>` 为立体 SVG；`<style>` 重写。`state` 类型扩到七态。

```vue
<script setup lang="ts">
import { ref } from "vue";
import { startDrag } from "../lib/window";

// 译宝 · 天青鹅蛋角色：立体光影 + 小手 + 天线（兼状态灯）。
// 七态：idle/listen/think/work/say + 短暂 valence（success/error）。
// size：常态球 64 / 聊天头部 44。保留 click/longpress/drag 手势状态机。
const props = withDefaults(
  defineProps<{
    state: "idle" | "listen" | "think" | "work" | "say" | "success" | "error";
    size?: number;
  }>(),
  { size: 64 },
);
const emit = defineEmits<{ (e: "click"): void; (e: "longpress"): void }>();

// 拖动 vs 点击 vs 长按：pointerdown 记坐标并起 450ms 计时；
// 移动 >4px 触发 startDragging（取消计时）；到点未动未抬 = 长按（voice）；提前抬起且未拖 = click。
const THRESHOLD = 4;
const LONGPRESS_MS = 450;
let down: { x: number; y: number } | null = null;
let dragging = false;
let longFired = false;
let timer: ReturnType<typeof setTimeout> | null = null;
const holding = ref(false);

function cancelTimer() {
  if (timer !== null) {
    clearTimeout(timer);
    timer = null;
  }
  holding.value = false;
}
function onPointerDown(e: PointerEvent) {
  if (e.button !== 0) return;
  down = { x: e.clientX, y: e.clientY };
  dragging = false;
  longFired = false;
  holding.value = true;
  (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
  timer = setTimeout(() => {
    if (down && !dragging) {
      longFired = true;
      emit("longpress");
    }
    cancelTimer();
  }, LONGPRESS_MS);
}
async function onPointerMove(e: PointerEvent) {
  if (!down || dragging) return;
  if (Math.hypot(e.clientX - down.x, e.clientY - down.y) > THRESHOLD) {
    dragging = true;
    cancelTimer();
    await startDrag(); // 必须在用户手势链内调用
  }
}
function onPointerUp() {
  cancelTimer();
  if (down && !dragging && !longFired) emit("click");
  down = null;
  dragging = false;
  longFired = false;
}

const INK = "var(--yb-body-ink)";
const BLUSH = "var(--yb-body-blush)";
</script>

<template>
  <div
    class="av"
    :class="[state, { holding }]"
    :style="{ width: props.size + 'px', height: props.size + 'px' }"
    @pointerdown.prevent="onPointerDown"
    @pointermove="onPointerMove"
    @pointerup="onPointerUp"
    @pointercancel="onPointerUp"
  >
    <svg viewBox="0 0 120 128" class="yb" aria-hidden="true">
      <defs>
        <linearGradient id="yb-body" x1="34%" y1="6%" x2="66%" y2="100%">
          <stop offset="0%" stop-color="var(--yb-body-hi)" />
          <stop offset="46%" stop-color="var(--yb-body-mid)" />
          <stop offset="100%" stop-color="var(--yb-body-lo)" />
        </linearGradient>
        <radialGradient id="yb-hi" cx="36%" cy="22%" r="32%">
          <stop offset="0%" stop-color="#ffffff" stop-opacity="0.95" />
          <stop offset="100%" stop-color="#ffffff" stop-opacity="0" />
        </radialGradient>
        <radialGradient id="yb-sh" cx="74%" cy="80%" r="46%">
          <stop offset="0%" stop-color="var(--yb-body-core-shadow)" stop-opacity="0.42" />
          <stop offset="100%" stop-color="var(--yb-body-core-shadow)" stop-opacity="0" />
        </radialGradient>
        <radialGradient id="yb-dot-glow" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stop-color="var(--dot)" stop-opacity="0.55" />
          <stop offset="100%" stop-color="var(--dot)" stop-opacity="0" />
        </radialGradient>
        <clipPath id="yb-clip">
          <path d="M60 18 C76 18 84 34 84 57 C84 84 74 102 60 102 C46 102 36 84 36 57 C36 34 44 18 60 18 Z" />
        </clipPath>
        <filter id="yb-b1"><feGaussianBlur stdDeviation="1.4" /></filter>
        <filter id="yb-b2"><feGaussianBlur stdDeviation="3.2" /></filter>
        <filter id="yb-b3"><feGaussianBlur stdDeviation="5.5" /></filter>
      </defs>

      <!-- 落地投影 -->
      <ellipse cx="63" cy="112" rx="33" ry="6.5" fill="#3f372e" opacity="0.16" filter="url(#yb-b3)" />

      <!-- 身体（呼吸在这层） -->
      <g class="body-grp">
        <path d="M60 18 C76 18 84 34 84 57 C84 84 74 102 60 102 C46 102 36 84 36 57 C36 34 44 18 60 18 Z" fill="url(#yb-body)" />
        <g clip-path="url(#yb-clip)">
          <rect x="28" y="10" width="86" height="100" fill="url(#yb-sh)" />
          <ellipse cx="44" cy="34" rx="28" ry="24" fill="url(#yb-hi)" />
          <ellipse cx="60" cy="106" rx="28" ry="10" fill="var(--yb-body-contact)" opacity="0.26" filter="url(#yb-b2)" />
        </g>
        <!-- 左侧边缘反光 -->
        <path d="M40 32 C37.6 47 37.8 73 44 94" fill="none" stroke="#ffffff" stroke-opacity="0.9" stroke-width="2.2" filter="url(#yb-b1)" />
        <!-- 小手 -->
        <ellipse cx="33" cy="64" rx="7.5" ry="11.5" fill="url(#yb-body)" transform="rotate(-14 33 64)" />
        <ellipse cx="34" cy="60" rx="3" ry="5" fill="#ffffff" opacity="0.5" transform="rotate(-14 34 60)" />
        <ellipse cx="87" cy="64" rx="7.5" ry="11.5" fill="url(#yb-body)" transform="rotate(14 87 64)" />
        <ellipse cx="88" cy="60" rx="3" ry="5" fill="var(--yb-body-core-shadow)" opacity="0.32" transform="rotate(14 88 60)" />
        <!-- 领巾（天青） -->
        <path d="M46 42 Q60 50 74 42" fill="none" stroke="var(--yb-accent)" stroke-width="3.2" stroke-linecap="round" />
        <path d="M46 41 Q60 49 74 41" fill="none" stroke="#ffffff" stroke-opacity="0.5" stroke-width="1" stroke-linecap="round" />

        <!-- 腮红（轻） -->
        <ellipse cx="47" cy="66" rx="4.2" ry="2.5" :fill="BLUSH" opacity="0.24" />
        <ellipse cx="73" cy="66" rx="4.2" ry="2.5" :fill="BLUSH" opacity="0.24" />

        <!-- 眼睛 / 嘴：按状态 -->
        <!-- idle -->
        <g v-if="state === 'idle'">
          <ellipse cx="51" cy="60" rx="3.2" ry="4.5" :fill="INK" />
          <ellipse cx="69" cy="60" rx="3.2" ry="4.5" :fill="INK" />
          <circle cx="52.2" cy="58.4" r="1.05" fill="#fff" />
          <circle cx="70.2" cy="58.4" r="1.05" fill="#fff" />
          <path d="M55 69 Q60 72 65 69" fill="none" :stroke="INK" stroke-width="2.4" stroke-linecap="round" />
        </g>
        <!-- listen -->
        <g v-else-if="state === 'listen'">
          <ellipse cx="51" cy="60" rx="3.2" ry="4.5" :fill="INK" />
          <ellipse cx="69" cy="60" rx="3.2" ry="4.5" :fill="INK" />
          <circle cx="52.2" cy="58.4" r="1.05" fill="#fff" />
          <circle cx="70.2" cy="58.4" r="1.05" fill="#fff" />
          <ellipse cx="60" cy="70" rx="2.8" ry="3.4" :fill="INK" />
        </g>
        <!-- think -->
        <g v-else-if="state === 'think'">
          <ellipse cx="51" cy="58.4" rx="3.2" ry="4.5" :fill="INK" />
          <ellipse cx="69" cy="58.4" rx="3.2" ry="4.5" :fill="INK" />
          <circle cx="52.2" cy="56.8" r="1.05" fill="#fff" />
          <circle cx="70.2" cy="56.8" r="1.05" fill="#fff" />
          <path d="M56 70 Q60 68.6 64 70" fill="none" :stroke="INK" stroke-width="2.4" stroke-linecap="round" />
        </g>
        <!-- work -->
        <g v-else-if="state === 'work'">
          <line x1="46.5" y1="54.5" x2="54" y2="56" :stroke="INK" stroke-width="1.8" stroke-linecap="round" />
          <line x1="73.5" y1="54.5" x2="66" y2="56" :stroke="INK" stroke-width="1.8" stroke-linecap="round" />
          <ellipse cx="51" cy="60.5" rx="3.2" ry="3.8" :fill="INK" />
          <ellipse cx="69" cy="60.5" rx="3.2" ry="3.8" :fill="INK" />
          <path d="M55 70 L65 70" fill="none" :stroke="INK" stroke-width="2.4" stroke-linecap="round" />
        </g>
        <!-- say -->
        <g v-else-if="state === 'say'">
          <ellipse cx="51" cy="60" rx="3.2" ry="4.5" :fill="INK" />
          <ellipse cx="69" cy="60" rx="3.2" ry="4.5" :fill="INK" />
          <circle cx="52.2" cy="58.4" r="1.05" fill="#fff" />
          <circle cx="70.2" cy="58.4" r="1.05" fill="#fff" />
          <ellipse cx="60" cy="70" rx="4" ry="4.6" :fill="INK" />
          <ellipse cx="60" cy="72.2" rx="2.2" ry="1.5" fill="#f0b8b8" opacity="0.85" />
        </g>
        <!-- success -->
        <g v-else-if="state === 'success'">
          <path d="M47 60.5 Q51 57.5 55 60.5" fill="none" :stroke="INK" stroke-width="2.4" stroke-linecap="round" />
          <path d="M65 60.5 Q69 57.5 73 60.5" fill="none" :stroke="INK" stroke-width="2.4" stroke-linecap="round" />
          <path d="M52 67 Q60 75 68 67" fill="none" :stroke="INK" stroke-width="2.6" stroke-linecap="round" />
        </g>
        <!-- error -->
        <g v-else>
          <path d="M47 61.5 Q51 64 55 61.5" fill="none" :stroke="INK" stroke-width="2.4" stroke-linecap="round" />
          <path d="M65 61.5 Q69 64 73 61.5" fill="none" :stroke="INK" stroke-width="2.4" stroke-linecap="round" />
          <path d="M55 71.5 Q60 69.2 65 71.5" fill="none" :stroke="INK" stroke-width="2.4" stroke-linecap="round" />
        </g>
      </g>

      <!-- 天线 -->
      <line x1="60" y1="20" x2="60" y2="11" stroke="var(--yb-body-stem)" stroke-width="2" stroke-linecap="round" />
      <circle v-if="state === 'think'" class="ring" cx="60" cy="8" r="6.5" fill="none" stroke="var(--dot)" stroke-width="1.6" stroke-dasharray="3 3" />
      <g class="dot-grp">
        <circle cx="60" cy="8" r="6" fill="url(#yb-dot-glow)" />
        <circle cx="60" cy="8" r="3.4" fill="var(--dot)" />
      </g>

      <!-- success 星星 -->
      <path v-if="state === 'success'" class="spark" d="M86 26 l1.6 4.4 l4.4 1.6 l-4.4 1.6 l-1.6 4.4 l-1.6 -4.4 l-4.4 -1.6 l4.4 -1.6 Z" fill="#f2a03c" />
      <!-- error 汗滴 -->
      <path v-if="state === 'error'" d="M80 34 q2.6 3.4 0 5 q-2.6 -1.6 0 -5" fill="#6a9cc4" />

      <!-- 声波弧（listen 左 / say 右） -->
      <g v-if="state === 'listen'" class="waves">
        <path class="wave" d="M28 58 q-4 6 0 12" fill="none" stroke="var(--dot)" stroke-width="2.2" stroke-linecap="round" />
        <path class="wave" d="M23 53 q-7 11 0 22" fill="none" stroke="var(--dot)" stroke-width="2.2" stroke-linecap="round" />
      </g>
      <g v-else-if="state === 'say'" class="waves">
        <path class="wave" d="M92 58 q4 6 0 12" fill="none" stroke="var(--dot)" stroke-width="2.2" stroke-linecap="round" />
        <path class="wave" d="M97 53 q7 11 0 22" fill="none" stroke="var(--dot)" stroke-width="2.2" stroke-linecap="round" />
      </g>
    </svg>
  </div>
</template>

<style scoped>
.av {
  position: relative;
  width: 64px;
  height: 64px;
  cursor: grab;
  user-select: none;
  touch-action: none;
}
.av:active {
  cursor: grabbing;
}
.yb {
  width: 100%;
  height: 100%;
  display: block;
  overflow: visible;
}

/* ---- 状态灯色（天线 dot）---- */
.av.idle { --dot: var(--yb-state-idle); }
.av.listen { --dot: var(--yb-state-listen); }
.av.think { --dot: var(--yb-state-think); }
.av.work { --dot: var(--yb-state-work); }
.av.say { --dot: var(--yb-state-say); }
.av.success { --dot: var(--yb-state-success); }
.av.error { --dot: var(--yb-state-error); }

/* ---- 动画基础 ---- */
.body-grp {
  transform-box: fill-box;
  transform-origin: 50% 92%;
  animation: breathe 3.4s infinite ease-in-out;
}
.dot-grp {
  transform-box: fill-box;
  transform-origin: center;
}
.ring {
  transform-box: fill-box;
  transform-origin: center;
}
.spark {
  transform-box: fill-box;
  transform-origin: center;
}
.wave {
  transform-box: fill-box;
  transform-origin: center;
}

/* 按住反馈：整体微放大，提示继续按住 = 语音 */
.av.holding .yb {
  transform: scale(1.06);
  transition: transform 0.45s ease;
}
.yb {
  transition: transform 0.15s ease;
}

@keyframes breathe {
  0%, 100% { transform: scale(1); }
  50% { transform: scale(1.02, 0.985); }
}

/* ---- 各态灯动效 ---- */
.av.idle .dot-grp { animation: dim 3s infinite ease-in-out; }
.av.listen .dot-grp { animation: pulse 1.2s infinite ease-in-out; }
.av.think .ring { animation: spin 2.4s linear infinite; }
.av.work .dot-grp { animation: pulse 1.7s infinite ease-in-out; }
.av.say .dot-grp { animation: glow 1s infinite alternate ease-in-out; }
.av.success .spark { animation: pop 1.2s ease-out infinite; }
.av.error .dot-grp { animation: shake 0.5s infinite ease-in-out; }

/* listen / say 声波渐次闪烁 */
.av.listen .wave,
.av.say .wave { animation: wv 1.1s infinite ease-in-out; }
.av.listen .wave:nth-child(2),
.av.say .wave:nth-child(2) { animation-delay: 0.2s; }

@keyframes dim { 0%, 100% { opacity: 0.5; } 50% { opacity: 0.85; } }
@keyframes pulse {
  0%, 100% { transform: scale(0.8); opacity: 0.6; }
  50% { transform: scale(1.2); opacity: 1; }
}
@keyframes glow { from { opacity: 0.65; } to { opacity: 1; } }
@keyframes spin { to { transform: rotate(360deg); } }
@keyframes wv { 0%, 100% { opacity: 0.25; } 50% { opacity: 0.9; } }
@keyframes pop {
  0% { transform: scale(0); opacity: 0; }
  55% { transform: scale(1.25); opacity: 1; }
  100% { transform: scale(1); opacity: 1; }
}
@keyframes shake {
  0%, 100% { transform: translateX(0); }
  25% { transform: translateX(-1.2px); }
  75% { transform: translateX(1.2px); }
}

@media (prefers-reduced-motion: reduce) {
  .body-grp, .dot-grp, .ring, .spark, .wave { animation: none !important; }
}
</style>
```

- [ ] **Step 2: DesignPreview 加 success/error 两态**

Modify `app/src/DesignPreview.vue`：把 `states` 数组与 `stateLabel` 改为：

```ts
const states = ["idle", "listen", "think", "work", "say", "success", "error"] as const;
const stateLabel: Record<string, string> = {
  idle: "待机", listen: "聆听", think: "思考", work: "干活", say: "说话",
  success: "成功", error: "出错",
};
```

- [ ] **Step 3: 类型检查**

Run: `cd app && npx vue-tsc --noEmit`
Expected: 无错误。注意 `App.vue` 仍把 `state` 传成五态字符串字面量——TS 会因 `AvatarState` 在 App.vue 里仍是旧五态定义而**可能不报错**（App.vue 自有 `type AvatarState`），但 Task 3 会同步 App.vue 的类型。若报错涉及 App.vue 传 `state`，先在 Task 3 修。

- [ ] **Step 4: 目测七态**

Run: `cd app && npm run dev`，开 `http://localhost:1420/design.html`。
Expected: 七个团子并排，全部天青瓷白 + 立体光影 + 小手 + 天线；天线灯颜色依次为 灰蓝/青/紫/琥珀/绿/深绿/红；think 有旋转虚线环、listen/say 有声波、success 有星星、error 有汗滴；work 是琥珀（≠ 天青主色）。

- [ ] **Step 5: Commit**

```bash
git add app/src/components/Avatar.vue app/src/DesignPreview.vue
git commit -m "feat(avatar): 重写角色——天青鹅蛋+立体光影+小手+天线状态灯+七态" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: 收起态对话气泡 PeekBubble + App 接入（含 success/error 触发）

**Files:**
- Create: `app/src/components/PeekBubble.vue`
- Modify: `app/src/App.vue`（类型扩七态、busy 修正、peek 状态 + 计时、onEvent 触发、模板渲染 PeekBubble）

**Interfaces:**
- Consumes: Task 2 的 `Avatar`（七态）。复用现有 `expand()`（`App.vue:56`）与 `window.ts` 的展开方向（`dir`）。
- Produces: `PeekBubble` props `{ text: string; preview?: string; long?: boolean }`、emit `expand`。

- [ ] **Step 1: 新建 `app/src/components/PeekBubble.vue`**

```vue
<script setup lang="ts">
// 收起态回复气泡：短回复直接显示；长内容给摘要 + 预览 +「点开看」。
// 仅展示；显隐与自动收起计时由父组件 App.vue 拥有。点整体 = 展开。
defineProps<{ text: string; preview?: string; long?: boolean }>();
defineEmits<{ (e: "expand"): void }>();
</script>

<template>
  <div class="peek" @click="$emit('expand')">
    <div class="who">译宝</div>
    <div v-if="long" class="digest">{{ text }}</div>
    <div v-else class="short">{{ text }}</div>
    <div v-if="long && preview" class="preview">{{ preview }}</div>
    <span v-if="long" class="chip">点开看 →</span>
    <i class="tail" aria-hidden="true" />
  </div>
</template>

<style scoped>
.peek {
  position: relative;
  max-width: 230px;
  background: var(--yb-surface-solid);
  border: 1px solid var(--yb-surface-border);
  border-radius: var(--yb-radius-md);
  padding: 10px 12px;
  box-shadow: var(--yb-shadow);
  font-size: var(--yb-fs-md);
  line-height: 1.6;
  color: var(--yb-text);
  cursor: pointer;
  animation: rise 0.3s var(--yb-ease) both;
}
.who { font-size: var(--yb-fs-sm); color: var(--yb-text-dim); font-weight: 600; margin-bottom: 3px; }
.short { white-space: pre-wrap; word-break: break-word; }
.digest { font-weight: 600; color: var(--yb-accent-deep); }
.preview { color: var(--yb-text-dim); font-size: var(--yb-fs-sm); margin-top: 4px; line-height: 1.5; }
.chip {
  display: inline-flex; align-items: center; gap: 4px; margin-top: 8px;
  background: var(--yb-accent); color: #fff; font-size: var(--yb-fs-sm); font-weight: 600;
  padding: 4px 10px; border-radius: 999px;
}
/* tail 指向右侧团子（团子默认 dock 右上 → 气泡在左） */
.tail {
  position: absolute; right: -5px; top: 18px; width: 10px; height: 10px;
  background: var(--yb-surface-solid);
  border-right: 1px solid var(--yb-surface-border);
  border-bottom: 1px solid var(--yb-surface-border);
  transform: rotate(-45deg);
}
@keyframes rise {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: none; }
}
</style>
```

- [ ] **Step 2: 改 `App.vue` —— 类型扩七态 + busy 修正**

Modify `app/src/App.vue`。

把 `type AvatarState`（约 `App.vue:32`）改为：
```ts
type AvatarState = "idle" | "listen" | "think" | "work" | "say" | "success" | "error";
```
把 `busy`（约 `App.vue:52`）改为（success/error 不算 busy，不可打断）：
```ts
const busy = computed(() =>
  state.value === "listen" || state.value === "think" ||
  state.value === "work" || state.value === "say",
);
```

- [ ] **Step 3: 改 `App.vue` —— peek 状态 + 长短判定 + 计时**

在 `<script setup>` 顶部 import 与状态区加入（放在 `const panelOpen = ref(false);` 附近）：

```ts
import PeekBubble from "./components/PeekBubble.vue";

type Peek = { text: string; preview?: string; long: boolean };
const peek = ref<Peek | null>(null);
let peekTimer: ReturnType<typeof setTimeout> | null = null;

/** 长内容判定：含表格/标题/代码块，或多段、超长。摘要取首段。 */
function buildPeek(raw: string): Peek {
  const long = /(^|\n)\s*(\||#|```|>)|\n\s*\n/.test(raw) || raw.length > 120;
  if (!long) return { text: raw, long: false };
  const firstPara = raw.split(/\n\s*\n/)[0].replace(/^[#>\-\*\d.\s]+/, "").trim();
  const text = firstPara.slice(0, 24) || "已整理好";
  const rest = raw.split("\n").filter((l) => l.trim() && !/^[#>\-\*]/.test(l)).slice(0, 3).join("　");
  return { text, preview: rest ? rest.slice(0, 60) : undefined, long: true };
}
function showPeek(raw: string) {
  if (peekTimer) clearTimeout(peekTimer);
  peek.value = buildPeek(raw);
  peekTimer = setTimeout(() => { peek.value = null; }, 6000);
}
function clearPeek() {
  if (peekTimer) clearTimeout(peekTimer);
  peek.value = null;
}
```

在 `onUnmounted`（约 `App.vue:307`）里追加清理：`if (peekTimer !== null) clearTimeout(peekTimer);`

- [ ] **Step 4: 改 `App.vue` —— onEvent 触发 peek + success/error 短闪**

在 `onEvent` 内：

`final_reply` 分支（约 `App.vue:150`）末尾（确定 text 后）加：
```ts
if (e.text) showPeek(e.text);
```
`reminder` 分支（约 `App.vue:176`）push 气泡后加：
```ts
showPeek("⏰ " + (e.text ?? "到点了"));
```
`action_result` 分支（约 `App.vue:136`）改为（成功短闪 400ms，spec 选项 ①）：
```ts
case "action_result":
  pending.value = null;
  flashValence("success");
  break;
```
`error` 分支（约 `App.vue:188`）push 气泡后加 `flashValence("error");`

新增辅助（紧挨 `onInterrupt` 后）：
```ts
let valenceTimer: ReturnType<typeof setTimeout> | null = null;
function flashValence(v: "success" | "error") {
  if (valenceTimer) clearTimeout(valenceTimer);
  state.value = v;
  valenceTimer = setTimeout(() => {
    if (state.value === v) state.value = "idle";
  }, 400);
}
```
并在 `expand()`（约 `App.vue:56`）开头加 `clearPeek();`（展开即清气泡）。

- [ ] **Step 5: 改 `App.vue` —— 模板渲染 PeekBubble**

在收起态模板（约 `App.vue:320-323`，`v-if="!expanded"` 块）把团子与状态文字包进一个定位容器，并加气泡：

```html
<template v-if="!expanded">
  <div class="pet-wrap">
    <PeekBubble
      v-if="peek"
      :text="peek.text"
      :preview="peek.preview"
      :long="peek.long"
      @expand="expand"
    />
    <Avatar class="pet" :state="state" @click="onPetClick" @longpress="onMic" />
  </div>
  <div class="status-collapsed" :class="state">{{ statusText }}</div>
</template>
```

并在 `<style scoped>` 加定位（团子原本 `position:absolute; left:34px; top:12px`，现在由 `.pet-wrap` 承担，气泡在团子左侧）：

```css
.pet-wrap {
  position: absolute;
  left: 34px;
  top: 12px;
  z-index: 3;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  /* 团子默认 dock 右上：气泡在左、团子在右 */
  flex-direction: row-reverse;
  animation: fade-in 0.18s var(--yb-ease) both;
}
.pet-wrap .pet {
  position: static;
  animation: none;
}
```

（移除原 `.pet { position:absolute; left:34px; top:12px; z-index:3; ... }` 里的绝对定位与 fade-in，交给 `.pet-wrap`。）

- [ ] **Step 6: 类型检查**

Run: `cd app && npx vue-tsc --noEmit`
Expected: 无错误。

- [ ] **Step 7: 手动验证（真机）**

Run: `cd app && npm run tauri dev`。
验证：
1. 对译宝说句话，回复到达 → 团子左侧升起气泡，显示回复文本（短）或摘要+「点开看」（长，如让它列看板）。
2. 约 6 秒气泡自动消失；鼠标悬停期间不消失（注：hover 暂停需额外加 `@mouseenter` 清计时 / `@mouseleave` 重启——若 Step 3 未做，作为可接受 v1 行为，或补：PeekBubble 加 `@mouseenter="$emit('hover')" / @mouseleave="$emit('leave')"`，App 维护计时暂停。本步先验证自动消失即可）。
3. 点气泡 → 展开完整聊天窗。
4. 触发一个成功操作（如记闪念）→ 团子 success 笑脸+星星闪 0.4s 回 idle；触发报错 → error 垂脸闪 0.4s。

- [ ] **Step 8: Commit**

```bash
git add app/src/components/PeekBubble.vue app/src/App.vue
git commit -m "feat(avatar): 收起态对话气泡 peek + success/error 情绪 + busy 修正" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 4: 透明窗鼠标穿透（setIgnoreMouseEvents）

**Files:**
- Modify: `app/src/lib/window.ts`（新增 `setClickThrough`）
- Modify: `app/src/App.vue`（收起态 mousemove 监听切换穿透）

**Interfaces:**
- Consumes: Tauri `getCurrentWindow().setIgnoreMouseEvents(ignore, {forward:true})`。
- Produces: `window.ts` 导出 `setClickThrough(on: boolean): Promise<void>`。

> 原理（BongoCat 同款）：默认 `setIgnoreMouseEvents(true, {forward:true})`——透明区穿透到下层，但仍转发 mousemove 给本窗；监听 mousemove，当光标进入团子屏幕区域时切 `false`（团子可交互），离开切 `true`。mousemove 先于 click，故进入团子后随后的 click 能落上。

- [ ] **Step 1: `window.ts` 新增 setClickThrough**

在 `app/src/lib/window.ts` 末尾追加：

```ts
/** 鼠标穿透开关：on=true 透明区穿透（仍转发 mousemove），on=false 团子区可交互。 */
export async function setClickThrough(on: boolean): Promise<void> {
  try {
    await getCurrentWindow().setIgnoreMouseEvents(on, { forward: on });
  } catch {
    /* Linux Wayland 等不支持则忽略，退化普通窗 */
  }
}
```

- [ ] **Step 2: `App.vue` 收起态默认穿透 + 进入团子区切回**

在 `onMounted`（约 `App.vue:295`）末尾加默认穿透 + mousemove 监听：

```ts
import { setClickThrough } from "./lib/window"; // 顶部 import 区加

// 收起态默认让透明区穿透；光标进入团子区才切回可交互
await setClickThrough(true);
window.addEventListener("mousemove", onPetHover);
```

在 `onUnmounted` 移除：`window.removeEventListener("mousemove", onPetHover);`

新增处理函数（紧挨 `onKeydown` 后）：

```ts
function onPetHover(e: MouseEvent) {
  if (expanded.value) {
    setClickThrough(false); // 展开态整窗可交互
    return;
  }
  const el = document.querySelector(".pet-wrap");
  if (!el) return;
  const r = el.getBoundingClientRect();
  const inside = e.clientX >= r.left && e.clientX <= r.right &&
                 e.clientY >= r.top && e.clientY <= r.bottom;
  setClickThrough(!inside);
}
```

- [ ] **Step 3: 类型检查**

Run: `cd app && npx vue-tsc --noEmit`
Expected: 无错误。

- [ ] **Step 4: 手动验证（真机，关键）**

Run: `cd app && npm run tauri dev`。
验证：
1. 收起态：把团子拖到一个桌面图标/另一窗口上方，点团子**旁边透明区** → 应点中下面的图标/窗口（穿透生效），不再被 132×140 透明窗吞掉。
2. 光标移到团子上 → 团子可点击/长按/拖（hover 切回可交互）。
3. 展开态：整个对话窗正常交互（输入/按钮）。
4. macOS 重点测；Linux Wayland 若无效属已知降级（spec §5），不影响 macOS/Windows。

- [ ] **Step 5: Commit**

```bash
git add app/src/lib/window.ts app/src/App.vue
git commit -m "feat(avatar): 透明窗鼠标穿透——透明区点击直达桌面，团子区可交互" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 5: 托盘图标 template 化（macOS 暗色菜单栏适配）

**Files:**
- Modify: `app/src-tauri/src/lib.rs`（`icon_as_template(false)` → `true`）
- Create: `app/src-tauri/icons/icon-tray.png`（单色剪影，透明背景）

**Interfaces:**
- 无前端接口；仅 Rust 托盘构建参数。

> `icon_as_template(true)` 让 macOS 把托盘图标当 template（自适应菜单栏明暗）。需配单色（黑/透明）图标；现有 `icons/icon.png` 是彩色的，不适配。故新建单色剪影 `icon-tray.png`。

- [ ] **Step 1: 准备单色托盘图标**

用任意工具把译宝天线鹅蛋剪影导出为 **单色（纯黑主体 + 透明背景）PNG，32×32 与 128×128**，存为 `app/src-tauri/icons/icon-tray.png`（128 优先，macOS 会缩放）。
> 若无现成剪影，可临时用现有 `icons/icon.png` 做灰度+阈值处理得到单色版；或先跳过本任务的视觉，仅在 Step 2 改参数并验证不崩，图标视觉后补。本步产物：`app/src-tauri/icons/icon-tray.png` 存在。

- [ ] **Step 2: 改 `lib.rs` 托盘构建**

Modify `app/src-tauri/src/lib.rs`（约 `lib.rs:512-516`）：

把
```rust
let tray_img = tauri::image::Image::from_bytes(include_bytes!("../icons/icon.png"))
    .expect("加载托盘图标失败");
TrayIconBuilder::with_id("main-tray")
    .icon(tray_img)
    .icon_as_template(false)
```
改为
```rust
let tray_img = tauri::image::Image::from_bytes(include_bytes!("../icons/icon-tray.png"))
    .expect("加载托盘图标失败");
TrayIconBuilder::with_id("main-tray")
    .icon(tray_img)
    .icon_as_template(true)
```

- [ ] **Step 3: 构建验证**

Run: `cd app/src-tauri && cargo build`
Expected: 编译通过（确认 `icon-tray.png` 被 `include_bytes!` 找到、无路径错误）。

- [ ] **Step 4: 手动验证（macOS）**

Run: `cd app && npm run tauri dev`。
切系统到深色菜单栏 → 托盘图标应自适应变白可见（template 生效）；浅色下变深。不再出现「彩色图标在深色菜单栏上糊成一团」。

- [ ] **Step 5: Commit**

```bash
git add app/src-tauri/src/lib.rs app/src-tauri/icons/icon-tray.png
git commit -m "feat(avatar): 托盘图标 template 化，适配 macOS 暗色菜单栏" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage（对照 spec §3）：**
- 3.1 造型（鹅蛋+立体+小手）→ Task 2 ✓
- 3.2 天青配色 token 迁移 → Task 1 ✓
- 3.3 天线 → Task 2 ✓
- 3.4 七态状态灯 + work 脱钩 + success/error → Task 2（渲染）+ Task 3（触发）✓
- 3.5 peek 气泡（短/长、自动收起、点击展开）→ Task 3 ✓（hover 暂停列为可选 v1 行为，spec 写"hover 保持"，Step 7 注明可补）
- 3.6 鼠标穿透 → Task 4 ✓
- §4 文件影响逐项覆盖：tokens.css / Avatar.vue / PeekBubble.vue(新) / App.vue / window.ts / lib.rs ✓
- §6 明确不做（Live2D / 三主题 / 交互侧重构）→ 均不在计划内 ✓

**Placeholder scan：** 无 TBD/TODO；每步含完整代码或精确改动 + 命令 + 期望。Task 5 Step 1 的图标生成是唯一"外部素材"步骤，已给出降级路径（可后补视觉、先验参数不崩），非占位。

**Type 一致性：** `AvatarState` 七态在 Task 2（Avatar 定义）与 Task 3（App 同步）一致；`Peek` 类型、`buildPeek/showPeek/clearPeek/flashValence` 命名在 Step 3/4/5 自洽；`setClickThrough(on)` 定义（Task 4 Step 1）与调用（Step 2）签名一致。

**已知遗留（非阻塞，实现期定）：**
- peek hover 暂停计时：spec 要求"hover 保持"，计划 Step 7 标注为可补；若严格遵循 spec，在 PeekBubble 加 hover 事件、App 暂停/重启计时器。
- success 触发用 spec 选项 ①（action_result 无后续 error 推断），Task 3 用 400ms 短闪实现；若后续要更准，给 `action_result` 加 `success` 标志（大脑侧小改，另议）。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-23-avatar-identity.md`. Two execution options:

1. **Subagent-Driven (recommended)** — 每个任务派一个全新 subagent，任务间我来 review，迭代快、上下文干净。
2. **Inline Execution** — 本会话内按任务批量执行，带 checkpoint 给你过目。

选哪种？
