<script setup lang="ts">
import { openUrl } from "@tauri-apps/plugin-opener";
import { checkPermissions, promptPermission, type BrainPermissions } from "../lib/brain";

defineProps<{ perms: BrainPermissions }>();

// macOS 系统设置对应面板的 URL scheme
const SETTINGS_URLS: Record<"ax" | "screen", string> = {
  ax: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
  screen: "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
};

function grant(which: "ax" | "screen") {
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
    <div class="title"><span class="icon">🔐</span> 译宝需要以下权限才能操作电脑</div>
    <div v-if="!perms.ax" class="row">
      <span class="label">辅助功能<span class="why">（读取控件、模拟键鼠）</span></span>
      <button class="ok" @click="grant('ax')">去授权</button>
    </div>
    <div v-if="!perms.screen" class="row">
      <span class="label">屏幕录制<span class="why">（截图感知屏幕内容）</span></span>
      <button class="ok" @click="grant('screen')">去授权</button>
    </div>
    <div class="foot">
      <button class="dim" @click="recheck">重新检测</button>
      <span class="hint">授权后点「重新检测」；屏幕录制需重启译宝生效。开发模式下屏幕录制状态可能误报。</span>
    </div>
  </div>
</template>

<style scoped>
.banner {
  padding: 12px 14px;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.96);
  border: 1px solid var(--yb-glass-border);
  box-shadow: var(--yb-shadow);
  font-size: 13px;
  color: var(--yb-text);
}
.title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  font-size: 13.5px;
  margin-bottom: 8px;
}
.icon {
  font-size: 15px;
}
.row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 6px 0;
  border-top: 1px solid rgba(0, 0, 0, 0.05);
}
.why {
  color: var(--yb-text-dim);
}
button {
  padding: 6px 14px;
  border-radius: 9px;
  border: none;
  cursor: pointer;
  font-size: 12.5px;
  font-weight: 500;
  white-space: nowrap;
  transition: filter 0.15s;
}
.ok {
  background: var(--yb-accent);
  color: #fff;
}
.dim {
  background: rgba(0, 0, 0, 0.06);
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
  font-size: 11.5px;
  color: var(--yb-text-dim);
  line-height: 1.4;
}
</style>
