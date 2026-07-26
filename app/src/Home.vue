<script setup lang="ts">
// 设置大窗根组件：标题条（可拖动 + 标题 + 关闭）+ 内容区（设置是唯一内容，结构预留以后扩展）。
import { getCurrentWindow } from "@tauri-apps/api/window";
import SettingsView from "./components/SettingsView.vue";

function close() {
  // 关窗=隐藏不销毁（Rust 侧 CloseRequested 同样拦截），状态保留、二次打开快
  void getCurrentWindow().hide();
}
</script>

<template>
  <div class="home-shell">
    <header class="titlebar" data-tauri-drag-region>
      <span class="title" data-tauri-drag-region>设置</span>
      <button class="x" title="关闭" @click="close">×</button>
    </header>
    <main class="content">
      <SettingsView />
    </main>
  </div>
</template>

<style scoped>
/* 壳：与面板窗同款玻璃大卡（圆角 + 天青渐变头 + blur） */
.home-shell {
  height: 100vh;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  font-family: var(--yb-font);
  font-size: 13px;
  line-height: 1.6;
  color: var(--yb-text);
  background:
    linear-gradient(180deg, rgba(77, 144, 196, 0.09), rgba(77, 144, 196, 0) 128px),
    var(--yb-shell-bg);
  -webkit-backdrop-filter: var(--yb-blur);
  backdrop-filter: var(--yb-blur);
  border: 1px solid var(--yb-glass-border);
  border-radius: var(--yb-radius-xl);
  box-shadow: var(--yb-shadow);
}
/* 标题条：与面板窗同款（可拖动 + 右侧 ×） */
.titlebar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--yb-space-3) var(--yb-space-4);
  border-bottom: 1px solid var(--yb-surface-border);
  user-select: none;
}
.title {
  font-size: var(--yb-fs-lg);
  font-weight: 600;
}
.x {
  border: none;
  background: transparent;
  font-size: 18px;
  line-height: 1;
  cursor: pointer;
  color: var(--yb-text-dim);
  padding: 2px 8px;
  border-radius: var(--yb-radius-sm);
}
.x:hover {
  background: var(--yb-btn-neutral);
}
.content {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
</style>
