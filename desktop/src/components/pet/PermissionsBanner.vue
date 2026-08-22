<script setup lang="ts">
import { onMounted, onUnmounted } from "vue";
import { openUrl } from "@tauri-apps/plugin-opener";
import {
  checkPermissions,
  promptPermission,
  revealAppInFinder,
  type BrainPermissions,
} from "../../lib/brain";
import YbIcon from "../common/YbIcon.vue";

defineProps<{ perms: BrainPermissions }>();

// macOS 系统设置对应面板的 URL scheme
const SETTINGS_URLS: Record<"ax" | "screen" | "input", string> = {
  ax: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
  screen: "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
  input: "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent",
};

// 权限向导条目（顺序即引导顺序）
const PERM_DEFS: { key: "ax" | "screen" | "input"; label: string; why: string }[] = [
  { key: "ax", label: "辅助功能", why: "读取控件、模拟键鼠" },
  { key: "input", label: "输入监控", why: "用户键鼠优先，AI 自动让出控制" },
  { key: "screen", label: "屏幕录制", why: "截图感知屏幕内容" },
];

function grant(which: "ax" | "screen" | "input") {
  // 双管齐下：系统授权弹窗（仅首次有效）+ 打开对应设置面板
  void promptPermission(which).catch(() => {});
  void openUrl(SETTINGS_URLS[which]).catch(() => {});
}

function revealInFinder() {
  // 兜底：在 Finder 亮出授权目标文件，用户可直接拖进系统设置授权列表
  void revealAppInFinder().catch(() => {});
}

// 自动轮询推进：授权成功后系统面板关闭、状态经 brain-permissions 事件回传，
// 向导自动打勾并推进下一步，无需手动点「重新检测」；全部授权后组件被卸载、轮询停止。
let pollTimer: ReturnType<typeof setInterval> | null = null;
onMounted(() => {
  pollTimer = setInterval(() => void checkPermissions().catch(() => {}), 1500);
});
onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer);
});
</script>

<template>
  <div class="banner">
    <div class="title"><YbIcon class="icon" name="lock" :size="16" /> 译宝需要以下权限才能操作电脑</div>
    <div v-for="def in PERM_DEFS" :key="def.key" class="row">
      <span class="label">
        <i class="dot" :class="perms[def.key] ? 'on' : 'off'" />
        {{ def.label }}<span class="why">（{{ def.why }}）</span>
      </span>
      <span v-if="perms[def.key]" class="done">已授权</span>
      <button v-else class="ok" @click="grant(def.key)">去授权</button>
    </div>
    <div class="foot">
      <button class="dim" @click="revealInFinder">在 Finder 中显示授权文件</button>
      <p class="hint">点「去授权」后，在打开的系统设置列表里找到「译宝」并打开开关；若列表中没有，点「+」添加，或点上方按钮在 Finder 中显示文件后直接拖进列表。授权后自动检测推进；屏幕录制需重启译宝生效。</p>
    </div>
  </div>
</template>

<style scoped>
.banner {
  padding: var(--yb-space-3) 14px;
  border-radius: var(--yb-radius-lg);
  background: var(--yb-surface-solid);
  border: 1px solid var(--yb-glass-border);
  box-shadow: var(--yb-shadow);
  font-size: var(--yb-fs-lg);
  color: var(--yb-text);
}
.title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: var(--yb-fw-bold);
  font-size: var(--yb-fs-lg);
  margin-bottom: var(--yb-space-2);
}
.icon {
  color: var(--yb-accent);
}
.row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--yb-space-2);
  padding: 6px 0;
  border-top: 1px solid var(--yb-surface-border);
}
.label {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.why {
  color: var(--yb-text-dim);
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex: none;
}
.dot.on {
  background: var(--yb-success, #34c759);
}
.dot.off {
  background: var(--yb-danger);
}
.done {
  flex: none;
  font-size: var(--yb-fs-sm);
  color: var(--yb-success, #34c759);
  font-weight: var(--yb-fw-medium);
}
button {
  padding: 6px 14px;
  border-radius: var(--yb-radius-sm);
  border: none;
  cursor: pointer;
  font-size: var(--yb-fs-md);
  font-weight: var(--yb-fw-medium);
  white-space: nowrap;
  transition: filter var(--yb-dur-fast);
}
.ok {
  background: var(--yb-accent);
  color: var(--yb-text-on-accent);
}
.dim {
  align-self: flex-start;
  background: var(--yb-btn-neutral);
  color: var(--yb-text-dim);
}
button:hover {
  filter: brightness(0.96);
}
.foot {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--yb-space-2);
  margin-top: var(--yb-space-3);
}
.hint {
  margin: 0;
  max-width: 100%;
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-dim);
  line-height: var(--yb-lh-base);
}
</style>
