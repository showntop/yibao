<script setup lang="ts">
/* YbIcon — 内联 SVG 图标集，替换原先当图标用的 UI emoji。
 *
 * 为什么不用 emoji：emoji 字形由系统字体决定，跨平台（甚至跨 macOS 版本）
 * 尺寸、基线、配色都不可控，且是彩色实心的，与本产品线性克制的 UI 割裂。
 *
 * 规范：24×24 网格 / 线性 / stroke-width 1.75 / currentColor / 圆头圆角。
 * 颜色由父级 color 决定，因此可直接跟随语义令牌（含未来深色模式）。
 */

type IconName =
  | "clock" | "chat" | "gear" | "spinner" | "check" | "x" | "stop"
  | "lock" | "pin" | "doc" | "alert" | "inbox" | "sparkle" | "plug"
  | "dumpling" | "mic" | "wave" | "thumb-up" | "thumb-down" | "search"
  | "panel-left" | "panel-right" | "plus" | "expand";

withDefaults(
  defineProps<{
    name: IconName;
    /** 视觉尺寸（px）。UI 内联 16，区块标题 20，空态 24+ */
    size?: number | string;
    /** 覆盖描边粗细；小尺寸想更实一点时用 */
    stroke?: number | string;
    /** 持续旋转，仅用于 spinner */
    spin?: boolean;
  }>(),
  { size: 16, stroke: 1.75, spin: false },
);

// 纯静态字符串字典，无用户输入，v-html 安全
const paths: Record<IconName, string> = {
  clock:
    '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.4V12l3.3 2"/>',
  chat:
    '<path d="M20.5 12.2a7.5 7.5 0 0 1-7.5 7.5H9.3L5 21.6l1-3.5A7.5 7.5 0 1 1 20.5 12.2Z"/>',
  gear:
    // 8 齿齿轮：4 直角齿 + 4 对角色齿（旋转 45°）+ 中心圆——
    // 圆形齿视觉上不像太阳的辐射线
    '<g><circle cx="12" cy="3.2" r="1.4"/><circle cx="12" cy="20.8" r="1.4"/><circle cx="3.2" cy="12" r="1.4"/><circle cx="20.8" cy="12" r="1.4"/><circle cx="5.9" cy="5.9" r="1.4"/><circle cx="18.1" cy="5.9" r="1.4"/><circle cx="5.9" cy="18.1" r="1.4"/><circle cx="18.1" cy="18.1" r="1.4"/><circle cx="12" cy="12" r="3.2"/></g>',
  spinner:
    '<path d="M12 3.5a8.5 8.5 0 1 0 8.5 8.5"/>',
  check:
    '<path d="M4.8 12.6l4.9 4.9L19.2 7"/>',
  x:
    '<path d="M6.2 6.2l11.6 11.6M17.8 6.2L6.2 17.8"/>',
  stop:
    '<rect x="6.5" y="6.5" width="11" height="11" rx="2.2"/>',
  lock:
    '<rect x="4.5" y="10.4" width="15" height="10.1" rx="2.6"/><path d="M8 10.4V7.7a4 4 0 0 1 8 0v2.7"/>',
  pin:
    '<path d="M8.6 3.6h6.8M10.1 3.6v6.1c0 1-2.4 2.3-2.4 4.4h8.6c0-2.1-2.4-3.4-2.4-4.4V3.6M12 14.1v6.3"/>',
  doc:
    '<path d="M13.4 3.5H7.2a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h9.6a2 2 0 0 0 2-2V9z"/><path d="M13.4 3.5V9h5.4M8.6 13.2h6.8M8.6 16.6h4.4"/>',
  alert:
    '<path d="M12 4.3l9 15.2H3z"/><path d="M12 9.6v4.1M12 16.7v.05"/>',
  inbox:
    '<path d="M3.5 13.1h4.2l1.5 2.5h5.6l1.5-2.5h4.2"/><path d="M3.5 13.1 6.7 5.5a2 2 0 0 1 1.8-1.2h7a2 2 0 0 1 1.8 1.2l3.2 7.6V18a2.5 2.5 0 0 1-2.5 2.5H6A2.5 2.5 0 0 1 3.5 18z"/>',
  sparkle:
    '<path d="M10.4 3.4l1.7 4.8 4.8 1.7-4.8 1.7-1.7 4.8-1.7-4.8L3.9 9.9l4.8-1.7z"/><path d="M17.8 15.1l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z"/>',
  plug:
    '<path d="M9.2 3.5v5.1M14.8 3.5v5.1M6.6 8.6h10.8v2.9a5.4 5.4 0 0 1-10.8 0zM12 16.9v3.6"/>',
  // 团子：圆润半月身体 + 顶部盖子折痕（模拟饺子/汤圆褶皱），
  // 不再用"圆+杆+小圆"那种像水滴的画法
  dumpling:
    '<path d="M4.5 12c0 4.1 3.4 7.5 7.5 7.5s7.5-3.4 7.5-7.5c0-3.8-3.4-7-7.5-7-3.5 0-7.5 2.5-7.5 7z"/><path d="M7 5.5c1.5 1.2 3 1.8 5 1.8s3.5-0.6 5-1.8"/>',
  mic:
    '<rect x="9" y="3.5" width="6" height="10.5" rx="3"/><path d="M5.8 11.5a6.2 6.2 0 0 0 12.4 0M12 17.7v3.1M9 20.8h6"/>',
  // 招手（欢迎）：简化线稿手掌
  wave:
    '<path d="M8 11.5V6a1.5 1.5 0 0 1 3 0v4.5m0-6a1.5 1.5 0 0 1 3 0V11m0-4a1.5 1.5 0 0 1 3 0v5.5m0-2.5a1.5 1.5 0 0 1 3 0v3.5c0 4.1-2.7 7-6.8 7-2.7 0-4.4-1-5.9-3.1l-2.7-3.9a1.55 1.55 0 0 1 2.5-1.8l1.9 2.3"/>',
  // 误报反馈（信任仪表写侧）：拇指 up/down
  "thumb-up":
    '<path d="M7 10.4v9.1M7 11.2 11.3 4c.5-.9 1.9-.6 2 .4l.3 3.6h5.2a2 2 0 0 1 2 2.4l-1.3 6.8a2.5 2.5 0 0 1-2.4 2H7"/><path d="M7 11v8.5H4.5a1 1 0 0 1-1-1V12a1 1 0 0 1 1-1z"/>',
  "thumb-down":
    '<path d="M7 13.6V4.5M7 12.8 11.3 20c.5.9 1.9.6 2-.4l.3-3.6h5.2a2 2 0 0 0 2-2.4l-1.3-6.8a2.5 2.5 0 0 0-2.4-2H7"/><path d="M7 13V4.5H4.5a1 1 0 0 0-1 1V12a1 1 0 0 0 1 1z"/>',
  // 放大镜（命令面板搜索等）
  search:
    '<circle cx="11" cy="11" r="6.5"/><path d="M15.8 15.8 20.5 20.5"/>',
  "panel-left":
    '<rect x="3.5" y="4" width="17" height="16" rx="2.5"/><path d="M9 4v16"/>',
  "panel-right":
    '<rect x="3.5" y="4" width="17" height="16" rx="2.5"/><path d="M15 4v16"/>',
  plus:
    '<path d="M12 5v14M5 12h14"/>',
  expand:
    '<path d="M8.5 4.5h-4v4M15.5 4.5h4v4M8.5 19.5h-4v-4M19.5 15.5v4h-4"/><path d="M4.8 8.2 9 4M15 4l4.2 4.2M4.8 15.8 9 20M15 20l4.2-4.2"/>',
};
</script>

<template>
  <svg
    class="yb-icon"
    :class="{ spin: spin }"
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    :stroke-width="stroke"
    stroke="currentColor"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    focusable="false"
    v-html="paths[name]"
  />
</template>

<style scoped>
.yb-icon {
  display: inline-block;
  flex: none;
  vertical-align: -0.14em; /* 与相邻文字基线对齐 */
}

.yb-icon.spin {
  animation: yb-icon-spin 0.9s linear infinite;
  transform-origin: 50% 50%;
}

@keyframes yb-icon-spin {
  to {
    transform: rotate(360deg);
  }
}

/* 减少动效时停转：常驻软件不该为一个转圈持续占用合成器 */
@media (prefers-reduced-motion: reduce) {
  .yb-icon.spin {
    animation: none;
  }
}
</style>
