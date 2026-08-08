<script setup lang="ts">
// 大窗根组件（完整 APP 主界面）：AI 原生 OS 语言——极简顶栏 + 内容区 + ⌘K 全局命令面板。
// 壳由系统负责（TitleBarStyle::Overlay，见 lib.rs）：原生红绿灯、系统阴影、缩放边框。
// 顶栏留 --yb-titlebar-h 安全区给浮在内容上的红绿灯；导航从「侧栏强 tab」改为「顶栏 tabs +
// ⌘K 命令面板」（Raycast/Linear 风格：找页面用搜/说，不是点）。
// 各页常驻挂载（v-show 切显隐）：事件订阅不断、气泡/面板状态切页不丢。
import { computed, onMounted, onUnmounted, ref } from "vue";
import { invoke } from "@tauri-apps/api/core";
import YbIcon from "./components/YbIcon.vue";
import Avatar from "./components/Avatar.vue";
import CommandPalette, { type PaletteTab } from "./components/CommandPalette.vue";
import HomeChat from "./components/HomeChat.vue";
import HomePlugins from "./components/HomePlugins.vue";
import DataView from "./components/DataView.vue";
import SettingsView from "./components/SettingsView.vue";
import { onPendingConfirms, onSettings, getSettingsOnce, type SettingsValues } from "./lib/brain";

// 主屏（home）= 对话 + 信息面板的融合体（AI 原生：对话是主入口，动态/回顾/插件一瞥都在右侧）
type Tab = "home" | "plugins" | "data" | "settings";
type AvatarState = "idle" | "listen" | "think" | "work" | "say" | "success" | "error";

const tab = ref<Tab>("home");
// 两页各自的会话状态：顶栏状态点跟随「当前页」的状态（主屏/设置沿用对话页）
const chatState = ref<AvatarState>("idle");
const panelState = ref<AvatarState>("idle");
const railState = computed<AvatarState>(() =>
  tab.value === "plugins" ? panelState.value : chatState.value,
);
const stateText = computed(
  () => ({
    idle: "待命中", listen: "聆听中", think: "思考中", work: "操作中", say: "说话中",
    success: "完成", error: "出错了",
  }[railState.value]),
);
// 待批准数：顶栏「主屏」徽标（收件箱有待处理的事，一眼可见）
const approvalCount = ref(0);
// 感知观察中叠加点（顶栏状态点）
const observing = ref(false);
function syncObserving(s: SettingsValues | null) {
  observing.value = !!(
    s?.["perception.master"] &&
    (s?.["perception.app"] || s?.["perception.activity"] || s?.["perception.screen"])
  );
}

// ---- ⌘K 全局命令面板 ----
const paletteOpen = ref(false);
function togglePalette() {
  paletteOpen.value = !paletteOpen.value;
}
function onPaletteNavigate(t: PaletteTab) {
  tab.value = t as Tab;
  paletteOpen.value = false;
}

// 全局快捷键：⌘K 命令面板；⌘1-3 / ⌘, 直接切页（macOS 标准）
const NAV: { id: Tab; label: string; icon: "inbox" | "plug" | "doc"; shortcut: string }[] = [
  { id: "home", label: "主屏", icon: "inbox", shortcut: "1" },
  { id: "plugins", label: "插件", icon: "plug", shortcut: "2" },
  { id: "data", label: "数据", icon: "doc", shortcut: "3" },
];
const TAB_SHORTCUTS: Record<string, Tab> = {
  "1": "home", "2": "plugins", "3": "data",
};
function onGlobalKeydown(e: KeyboardEvent) {
  if (!e.metaKey && !e.ctrlKey) return;
  const k = e.key.toLowerCase();
  if (k === "k") {
    e.preventDefault();
    togglePalette();
    return;
  }
  if (k === ",") {
    e.preventDefault();
    tab.value = "settings";
    return;
  }
  const t = TAB_SHORTCUTS[k];
  if (t) {
    e.preventDefault();
    tab.value = t;
  }
}

// 徽标：主屏显待批准数（收件箱有待你动手的事）
const homeBadge = computed(() => approvalCount.value);

let unApprovals: (() => void) | null = null;
let unSettings: (() => void) | null = null;
onMounted(async () => {
  unApprovals = onPendingConfirms((l) => (approvalCount.value = l.length));
  void getSettingsOnce().then(syncObserving);
  unSettings = await onSettings(syncObserving);
  window.addEventListener("keydown", onGlobalKeydown);
});
onUnmounted(() => {
  unApprovals?.();
  unSettings?.();
  window.removeEventListener("keydown", onGlobalKeydown);
});

function close() {
  // 收起大窗 = 隐藏 + 回小窗模式（Rust 侧还原宠物窗/面板浮窗）
  void invoke("close_home_window").catch(() => {});
}
</script>

<template>
  <div class="home-shell">
    <!-- 顶栏：红绿灯安全区 + 品牌 + 居中 tabs + 右侧命令/设置/收起 -->
    <header class="topbar">
      <div class="titlebar-safe" data-tauri-drag-region></div>
      <div class="topbar-row">
        <div class="topbar-brand">
          <!-- 天青鹅蛋角色（译宝本体）：compact 模式带状态灯/呼吸，顶栏品牌即角色 -->
          <span class="tb-brand" data-tauri-drag-region>
            <Avatar :state="railState" :size="20" :observing="observing" />
          </span>
          <span class="tb-name" data-tauri-drag-region>译宝</span>
          <span class="tb-state" :class="railState" data-tauri-drag-region>
            <i class="tb-dot" />{{ stateText }}
          </span>
        </div>

        <nav class="tb-nav" data-tauri-drag-region>
          <button
            v-for="n in NAV"
            :key="n.id"
            class="tb-nav-item"
            :class="{ on: tab === n.id }"
            :title="`⌘${n.shortcut}`"
            @click="tab = n.id"
          >
            <YbIcon class="tb-nav-ic" :name="n.icon" :size="15" />
            <span class="tb-nav-label">{{ n.label }}</span>
            <span v-if="n.id === 'home' && homeBadge > 0" class="tb-badge yb-num">{{ homeBadge }}</span>
          </button>
        </nav>

        <div class="tb-right">
          <button class="tb-btn" title="命令面板 (⌘K)" @click="togglePalette">
            <YbIcon name="search" :size="15" />
            <kbd class="tb-kbd">⌘K</kbd>
          </button>
          <button class="tb-btn" :class="{ on: tab === 'settings' }" title="设置 (⌘,)" @click="tab = 'settings'">
            <YbIcon name="gear" :size="15" />
          </button>
          <button class="tb-btn" title="收起为小窗" @click="close">
            <YbIcon name="dumpling" :size="15" />
          </button>
        </div>
      </div>
    </header>

    <!-- 内容区：各页常驻挂载，切页只切显隐。
         主屏 = 对话 + 信息面板融合体（AI 交互主入口）；插件面板打开 → 自动切插件页 -->
    <main class="content">
      <HomeChat v-show="tab === 'home'" @state="chatState = $event" @open-panel="tab = 'plugins'" @reminder="tab = 'home'" />
      <HomePlugins v-show="tab === 'plugins'" @state="panelState = $event" @panel="tab = 'plugins'" />
      <DataView v-show="tab === 'data'" />
      <SettingsView v-show="tab === 'settings'" />
    </main>

    <!-- ⌘K 命令面板：覆盖在主屏上（AI 原生：找页面用搜/说） -->
    <CommandPalette :open="paletteOpen" @close="paletteOpen = false" @navigate="onPaletteNavigate" @collapse="close" />
  </div>
</template>

<style scoped>
/* 壳：原生窗口——不自绘圆角/阴影/玻璃，系统负责。只管 顶栏 + 内容区 布局。 */
.home-shell {
  height: 100vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  font-family: var(--yb-font);
  font-size: var(--yb-fs-lg);
  line-height: var(--yb-lh-base);
  color: var(--yb-text);
  background: var(--yb-content-bg);
}

/* ---- 顶栏 ---- */
.topbar {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  border-bottom: 1px solid var(--yb-border-base);
  /* 极淡 accent 微光（自上而下淡出），与内容区氛围光衔接成一体 */
  background:
    linear-gradient(180deg, rgba(var(--yb-c-sky-rgb), 0.045), rgba(var(--yb-c-sky-rgb), 0) 100%),
    var(--yb-content-bg);
}
/* 红绿灯安全区：Overlay 标题栏下按钮浮在内容上，这块只作留白 + 拖窗把手 */
.titlebar-safe {
  height: var(--yb-titlebar-h);
  flex-shrink: 0;
}
.topbar-row {
  display: flex;
  align-items: center;
  gap: var(--yb-space-3);
  height: 44px;
  padding: 0 var(--yb-space-4);
  user-select: none;
}
/* 品牌：团子 + 译宝 + 状态点（观感从"app 导航"变成"OS 顶栏"） */
.topbar-brand {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
/* 顶栏品牌 = 天青鹅蛋角色本体（Avatar compact 20px，自带状态灯/呼吸），
 * 纯角色无徽章底 */
.tb-brand {
  display: grid;
  place-items: center;
  user-select: none;
  cursor: default;
}
.tb-brand :deep(.av) {
  cursor: default;
}
.tb-brand :deep(.av:active) {
  cursor: default;
}
.tb-name {
  font-size: var(--yb-fs-lg);
  font-weight: var(--yb-fw-bold);
  letter-spacing: -0.01em;
}
.tb-state {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: var(--yb-fs-xs);
  color: var(--yb-text-dim);
  line-height: 1;
}
.tb-dot {
  width: 6px;
  height: 6px;
  flex-shrink: 0;
  border-radius: 50%;
  background: var(--dot, var(--yb-state-idle));
}
.tb-state.idle { --dot: var(--yb-state-idle); }
.tb-state.listen { --dot: var(--yb-state-listen); }
.tb-state.think { --dot: var(--yb-state-think); }
.tb-state.work { --dot: var(--yb-state-work); }
.tb-state.say { --dot: var(--yb-state-say); }
.tb-state.success { --dot: var(--yb-state-success); }
.tb-state.error { --dot: var(--yb-state-error); }

/* 导航：居中 tabs（macOS 分段控件语言），hover/选中有底 */
.tb-nav {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 2px;
}
.tb-nav-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 12px;
  border: none;
  border-radius: var(--yb-radius-sm);
  background: transparent;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-md);
  font-family: inherit;
  cursor: pointer;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.tb-nav-item:hover {
  background: var(--yb-row-hover);
  color: var(--yb-text);
}
.tb-nav-item.on {
  background: var(--yb-segment-thumb);
  color: var(--yb-text);
  font-weight: var(--yb-fw-medium);
  box-shadow: var(--yb-shadow-1);
}
.tb-nav-item.on .tb-nav-ic {
  color: var(--yb-accent);
}
.tb-badge {
  flex-shrink: 0;
  min-width: 16px;
  height: 15px;
  padding: 0 5px;
  display: grid;
  place-items: center;
  border-radius: var(--yb-radius-pill);
  background: var(--yb-danger);
  color: var(--yb-text-on-accent);
  font-size: var(--yb-fs-xs);
  font-weight: var(--yb-fw-bold);
  line-height: 1;
}
.tb-nav-item.on .tb-badge {
  background: var(--yb-accent);
}

/* 右侧：⌘K 命令 / 设置 / 收起 */
.tb-right {
  display: flex;
  align-items: center;
  gap: 2px;
}
.tb-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 28px;
  padding: 0 9px;
  border: none;
  border-radius: var(--yb-radius-sm);
  background: transparent;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-md);
  font-family: inherit;
  cursor: pointer;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.tb-btn:hover {
  background: var(--yb-row-hover);
  color: var(--yb-text);
}
.tb-btn.on {
  background: var(--yb-segment-thumb);
  color: var(--yb-accent);
}
.tb-kbd {
  padding: 1px 5px;
  border: 1px solid var(--yb-border-strong);
  border-radius: var(--yb-radius-xs);
  background: var(--yb-surface-2);
  color: var(--yb-text-faint);
  font-size: 10px;
  font-family: var(--yb-font);
  line-height: 1.3;
}

/* ---- 内容区 ---- */
.content {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  /* 顶部 + 左右两侧 accent 氛围光（radial 淡出）：对话列全宽展开后，
   * 两侧空白由微光承接，不显死白 */
  background:
    radial-gradient(120% 90% at 50% -20%, rgba(var(--yb-c-sky-rgb), 0.05), transparent 55%),
    radial-gradient(60% 40% at 0% 50%, rgba(var(--yb-c-sky-rgb), 0.03), transparent 70%),
    radial-gradient(60% 40% at 100% 50%, rgba(var(--yb-c-sky-rgb), 0.03), transparent 70%),
    var(--yb-content-bg);
}
.content > * {
  flex: 1;
  min-height: 0;
}
</style>
