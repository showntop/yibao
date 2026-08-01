<script setup lang="ts">
import { openUrl } from "@tauri-apps/plugin-opener";
import { checkPermissions, promptPermission, type BrainPermissions } from "../lib/brain";
import YbIcon from "./YbIcon.vue";

defineProps<{ perms: BrainPermissions }>();

// macOS 系统设置对应面板的 URL scheme
const SETTINGS_URLS: Record<"ax" | "screen" | "input", string> = {
  ax: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
  screen: "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
  input: "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent",
};

function grant(which: "ax" | "screen" | "input") {
  // 双管齐下：系统授权弹窗（仅首次有效）+ 打开对应设置面板
  void promptPermission(which).catch(() => {});
  void openUrl(SETTINGS_URLS[which]).catch(() => {});
}

function recheck() {
  void checkPermissions().catch(() => {});
}
</script>

<template>
  <div class="banner">
    <div class="title"><YbIcon class="icon" name="lock" :size="16" /> 译宝需要以下权限才能操作电脑</div>
    <div v-if="!perms.ax" class="row">
      <span class="label">辅助功能<span class="why">（读取控件、模拟键鼠）</span></span>
      <button class="ok" @click="grant('ax')">去授权</button>
    </div>
    <div v-if="!perms.screen" class="row">
      <span class="label">屏幕录制<span class="why">（截图感知屏幕内容）</span></span>
      <button class="ok" @click="grant('screen')">去授权</button>
    </div>
    <div v-if="!perms.input" class="row">
      <span class="label">输入监控<span class="why">（用户键鼠优先，AI 自动让出控制）</span></span>
      <button class="ok" @click="grant('input')">去授权</button>
    </div>
    <div class="foot">
      <button class="dim" @click="recheck">重新检测</button>
      <span class="hint">授权后点「重新检测」；屏幕录制需重启译宝生效。开发模式下屏幕录制状态可能误报。</span>
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
.why {
  color: var(--yb-text-dim);
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
  background: var(--yb-btn-neutral);
  color: var(--yb-text-dim);
}
button:hover {
  filter: brightness(0.96);
}
.foot {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 8px;
}
.hint {
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-dim);
  line-height: var(--yb-lh-ui);
}
</style>
