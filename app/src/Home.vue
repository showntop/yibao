<script setup lang="ts">
// 大窗根组件（完整 APP 主界面）：左侧边栏（团子 + 对话/插件/设置导航 + 收起）+ 主区三页。
// 三页常驻挂载（v-show 切显隐）：事件订阅不断、气泡/面板状态切页不丢。
// 与小窗互斥由 Rust 管（open/close_home_window），本组件 × 走 close_home_window。
import { computed, ref } from "vue";
import { invoke } from "@tauri-apps/api/core";
import Avatar from "./components/Avatar.vue";
import HomeChat from "./components/HomeChat.vue";
import HomePlugins from "./components/HomePlugins.vue";
import SettingsView from "./components/SettingsView.vue";

type Tab = "chat" | "plugins" | "settings";
type AvatarState = "idle" | "listen" | "think" | "work" | "say" | "success" | "error";

const tab = ref<Tab>("chat");
// 两页各自的会话状态：侧边栏团子跟随「当前页」的状态（设置页沿用对话页）
const chatState = ref<AvatarState>("idle");
const panelState = ref<AvatarState>("idle");
const railState = computed<AvatarState>(() =>
  tab.value === "plugins" ? panelState.value : chatState.value,
);

const NAV: { id: Tab; label: string }[] = [
  { id: "chat", label: "对话" },
  { id: "plugins", label: "插件" },
  { id: "settings", label: "设置" },
];

function close() {
  // 收起大窗 = 隐藏 + 回小窗模式（Rust 侧还原宠物窗/面板浮窗）
  void invoke("close_home_window").catch(() => {});
}
</script>

<template>
  <div class="home-shell">
    <!-- 侧边栏：团子（状态跟随当前页）+ 导航 + 收起大窗 -->
    <aside class="rail">
      <div class="rail-head" data-tauri-drag-region>
        <Avatar :state="railState" :size="36" />
        <span class="rail-name" data-tauri-drag-region>译宝</span>
      </div>
      <nav class="rail-nav">
        <button
          v-for="n in NAV"
          :key="n.id"
          class="nav-item"
          :class="{ on: tab === n.id }"
          @click="tab = n.id"
        >
          <!-- 对话 -->
          <svg v-if="n.id === 'chat'" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
          </svg>
          <!-- 插件 -->
          <svg v-else-if="n.id === 'plugins'" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="3" width="7" height="7" rx="1.5" />
            <rect x="14" y="3" width="7" height="7" rx="1.5" />
            <rect x="3" y="14" width="7" height="7" rx="1.5" />
            <rect x="14" y="14" width="7" height="7" rx="1.5" />
          </svg>
          <!-- 设置 -->
          <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor"
            stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
          </svg>
          {{ n.label }}
        </button>
      </nav>
      <div class="rail-foot">
        <button class="rail-x" title="收起大窗（回到译宝小窗）" @click="close">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
            <line x1="6" y1="12" x2="18" y2="12" />
          </svg>
        </button>
      </div>
    </aside>

    <!-- 主区：三页常驻挂载，切页只切显隐 -->
    <main class="main">
      <HomeChat v-show="tab === 'chat'" @state="chatState = $event" @open-panel="tab = 'plugins'" />
      <HomePlugins v-show="tab === 'plugins'" @state="panelState = $event" />
      <SettingsView v-show="tab === 'settings'" />
    </main>
  </div>
</template>

<style scoped>
/* 壳：与面板窗同款玻璃大卡（圆角 + 天青渐变头 + blur） */
.home-shell {
  height: 100vh;
  box-sizing: border-box;
  display: flex;
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
/* 侧边栏：浅天青底与主区分开，右侧一根 hairline */
.rail {
  width: 152px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  padding: var(--yb-space-3) var(--yb-space-2);
  background: linear-gradient(180deg, rgba(77, 144, 196, 0.14), rgba(77, 144, 196, 0.06));
  border-right: 1px solid var(--yb-surface-border);
}
.rail-head {
  display: flex;
  align-items: center;
  gap: var(--yb-space-2);
  padding: 2px var(--yb-space-2) var(--yb-space-3);
  user-select: none;
}
.rail-name {
  font-size: var(--yb-fs-xl);
  font-weight: 650;
  letter-spacing: 0.01em;
}
.rail-nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.nav-item {
  display: flex;
  align-items: center;
  gap: var(--yb-space-2);
  padding: 7px var(--yb-space-3);
  border: none;
  border-radius: var(--yb-radius-md);
  background: transparent;
  color: var(--yb-text-dim);
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.15s ease;
}
.nav-item svg {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
}
.nav-item:hover {
  background: var(--yb-surface-solid);
  color: var(--yb-text);
}
/* 选中态：实底白卡 + accent 字色，一眼定位 */
.nav-item.on {
  background: var(--yb-surface-solid);
  color: var(--yb-accent-deep);
  font-weight: 600;
  box-shadow: var(--yb-shadow-soft);
}
.rail-foot {
  margin-top: auto;
  display: flex;
  padding: var(--yb-space-2);
}
/* 收起大窗：幽灵圆钮 + minus（与宠物窗收起钮同语义） */
.rail-x {
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--yb-text-dim);
  cursor: pointer;
  transition: all 0.15s ease;
}
.rail-x svg {
  width: 14px;
  height: 14px;
}
.rail-x:hover {
  background: var(--yb-well);
  color: var(--yb-text);
}
.main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.main > * {
  flex: 1;
  min-height: 0;
}
</style>
