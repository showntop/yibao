<script setup lang="ts">
// 大窗根组件（完整 APP 主界面）：左侧边栏（团子 + 主屏/对话/插件/设置导航 + 收起）+ 主区四页。
// 四页常驻挂载（v-show 切显隐）：事件订阅不断、气泡/面板状态切页不丢。
// 主屏是默认落地页（OS 感 §4.2：解锁第一眼 = 问候 + 动态 + 常用）。
// 与小窗互斥由 Rust 管（open/close_home_window），本组件 × 走 close_home_window。
import { computed, nextTick, onMounted, onUnmounted, ref } from "vue";
import { invoke } from "@tauri-apps/api/core";
import Avatar from "./components/Avatar.vue";
import HomeFeed from "./components/HomeFeed.vue";
import HomeChat from "./components/HomeChat.vue";
import HomePlugins from "./components/HomePlugins.vue";
import SettingsView from "./components/SettingsView.vue";
import { onPendingConfirms } from "./lib/brain";

type Tab = "home" | "chat" | "plugins" | "settings";
type AvatarState = "idle" | "listen" | "think" | "work" | "say" | "success" | "error";

const tab = ref<Tab>("home");
// 两页各自的会话状态：侧边栏团子跟随「当前页」的状态（主屏/设置沿用对话页）
const chatState = ref<AvatarState>("idle");
const panelState = ref<AvatarState>("idle");
const railState = computed<AvatarState>(() =>
  tab.value === "plugins" ? panelState.value : chatState.value,
);
// 主屏 → 对话页的草稿传递（Feed 点击带上下文追问）
const chatDraft = ref("");
// 待批准数：主屏 nav 徽标（收件箱有待处理的事，一眼可见）
const approvalCount = ref(0);
let unApprovals: (() => void) | null = null;
onMounted(() => {
  unApprovals = onPendingConfirms((l) => (approvalCount.value = l.length));
});
onUnmounted(() => unApprovals?.());

function onFeedChat(draft?: string) {
  tab.value = "chat";
  if (!draft) {
    chatDraft.value = "";
    return;
  }
  // 同一条动态重复点击也要重新填入：先清空、下一拍再设回，强制触发 InputBar 的 watch
  chatDraft.value = "";
  void nextTick(() => (chatDraft.value = draft));
}

const NAV: { id: Tab; label: string }[] = [
  { id: "home", label: "主屏" },
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
          <!-- 主屏 -->
          <svg v-if="n.id === 'home'" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
            <polyline points="9 22 9 12 15 12 15 22" />
          </svg>
          <!-- 对话 -->
          <svg v-else-if="n.id === 'chat'" viewBox="0 0 24 24" fill="none" stroke="currentColor"
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
          <span v-if="n.id === 'home' && approvalCount" class="nav-badge">{{ approvalCount }}</span>
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

    <!-- 主区：四页常驻挂载，切页只切显隐。
         reminder / 新面板打开 → 自动切到对应页（提醒要看见；新面板 ≈ 小窗模式浮窗弹出）；
         主屏提交/点动态 → 切对话页（draft 非空时预填输入框） -->
    <main class="main">
      <HomeFeed v-show="tab === 'home'" @chat="onFeedChat" />
      <HomeChat v-show="tab === 'chat'" :draft="chatDraft" @state="chatState = $event" @open-panel="tab = 'plugins'" @reminder="tab = 'chat'" />
      <HomePlugins v-show="tab === 'plugins'" @state="panelState = $event" @panel="tab = 'plugins'" />
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
/* 待批徽标：主屏 nav 上的小红点计数（收件箱有等你处理的事） */
.nav-badge {
  margin-left: auto;
  min-width: 17px;
  height: 17px;
  padding: 0 4px;
  display: grid;
  place-items: center;
  border-radius: 9px;
  background: var(--yb-danger);
  color: #fff;
  font-size: 10px;
  font-weight: 700;
  line-height: 1;
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
