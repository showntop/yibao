<script setup lang="ts">
// 大窗根组件（完整 APP 主界面）：AI 原生 OS 语言——极简顶栏 + 内容区 + ⌘K 全局命令面板。
// 壳由系统负责（TitleBarStyle::Overlay，见 lib.rs）：原生红绿灯、系统阴影、缩放边框。
// 顶栏留 --yb-titlebar-h 安全区给浮在内容上的红绿灯；导航从「侧栏强 tab」改为「顶栏 tabs +
// ⌘K 命令面板」（Raycast/Linear 风格：找页面用搜/说，不是点）。
// 各页常驻挂载（v-show 切显隐）：事件订阅不断、气泡/面板状态切页不丢。
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { invoke } from "@tauri-apps/api/core";
import YbIcon from "./components/YbIcon.vue";
import CommandPalette, { type PaletteTab } from "./components/CommandPalette.vue";
import HomeChat from "./components/HomeChat.vue";
import HomePlugins from "./components/HomePlugins.vue";
import CapabilityConversationRail, { type CapabilityRailSurface } from "./components/CapabilityConversationRail.vue";
import { sessionStore, clearLegacySessionKeys } from "./state/store";
import DataView from "./components/DataView.vue";
import SettingsView from "./components/SettingsView.vue";
import appLogo from "./assets/logo.png";
import { onPendingConfirms } from "./lib/brain";
import { decideSurface, type Attention, type Presentation } from "./lib/surface-policy";

// 主屏（home）= 对话 + 信息面板的融合体（AI 原生：对话是主入口，动态/回顾/插件一瞥都在右侧）
type Tab = "home" | "plugins" | "data" | "settings";
type AvatarState = "idle" | "listen" | "think" | "work" | "say" | "success" | "error";

const tab = ref<Tab>("home");
const qaMode = import.meta.env.DEV && new URLSearchParams(window.location.search).get("qa") === "capability";
const chatState = ref<AvatarState>("idle");
const panelState = ref<AvatarState>("idle");
const leftRailOpen = ref(true);
const rightRailOpen = ref(true);
// 非全屏（窗口窄）默认收起左栏：展开/折叠按钮始终可点，用户手动切换后不再被覆盖
const isNarrowWindow = () => window.innerWidth <= 1180;
const clockNow = ref(new Date());
const productNote = "让信息自然归位。";

// ---- 主题（顶栏切换按钮；三态：light / dark / system，与系统偏好对齐） ----
type ThemeMode = "light" | "dark" | "system";
const theme = ref<ThemeMode>(((localStorage.getItem("yibao-theme") as ThemeMode) || "system"));
function applyTheme(v: ThemeMode) {
  if (v === "system") {
    delete document.documentElement.dataset.theme;
    localStorage.removeItem("yibao-theme");
  } else {
    document.documentElement.dataset.theme = v;
    localStorage.setItem("yibao-theme", v);
  }
}
watch(theme, applyTheme);
// 实际生效：system 跟随系统，light/dark 显式
const themeEffective = computed<"light" | "dark">(() => {
  if (theme.value === "system") return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  return theme.value;
});
// 图标 = 点击后要去到的主题（macOS 约定）：深色显示太阳（点击变亮）、浅色显示月亮（点击变暗）。
// 不随状态显示 sliders——那会和设置按钮图标混淆。
const themeIcon = computed(() => themeEffective.value === "dark" ? "sun" : "moon");
const themeTitle = computed(() => {
  const eff = themeEffective.value === "dark" ? "深色" : "浅色";
  if (theme.value === "system") return `主题：跟随系统（当前${eff}）· 点击切换`;
  return `主题：${theme.value === "dark" ? "深色" : "浅色"} · 点击切换`;
});
function cycleTheme() {
  // light → dark → system → light；system 固定到当前反色，不产生无效点击
  if (theme.value === "light") theme.value = "dark";
  else if (theme.value === "dark") theme.value = "light";
  else theme.value = themeEffective.value === "dark" ? "light" : "dark";
}
const weatherNote = "晴 · 26°";
let clockTimer: number | null = null;

const clockText = computed(() => new Intl.DateTimeFormat("zh-CN", {
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
}).format(clockNow.value));

// Phase 1：surface 裁决事件——后端建议（presentation/attention）+ 前端 explicit 推断一起进裁决器
interface CapabilitySurfaceEvent extends CapabilityRailSurface {
  explicit: boolean;
  suggested: Presentation | null;
  attention: Attention;
  supported?: Presentation[];
}
type HomePluginsRef = {
  backToList: () => void;
  suspendSurface: () => void;
  restoreSurface: () => boolean;
  clearObjectScope: () => boolean;
  collapseScene: () => Promise<void>;
};
const pluginHost = ref<HomePluginsRef | null>(null);
const capability = ref<CapabilityRailSurface | null>(null);
const surfaceVisible = ref(false);
const presentation = ref<Presentation>("stage");
const sceneActive = computed(() => tab.value === "home" && surfaceVisible.value && capability.value !== null);
const activityBusy = computed(() => panelState.value !== "idle");
// 场景布局（scene）持久化在 surface 域：hydrate 完成后 onMounted 读入，watch 写回
let savedScene: { panel: string; visible: boolean; presentation: Presentation } | null = null;
let restorePending = false;
// 恢复只允许在启动后的短暂窗口内发生：pullCache 会从 surface 域回退并发出首个 surface；
// 超时未匹配就放弃 pending，防止后续用户手动操作被旧布局"拽回去"。
let restoreTimer: number | null = null;

watch([capability, surfaceVisible, presentation], () => {
  try {
    if (!capability.value) sessionStore.surface.clearScene();
    else {
      sessionStore.surface.setScene({
        panel: capability.value.panel,
        visible: surfaceVisible.value,
        presentation: presentation.value,
        tab: tab.value,
      });
    }
  } catch { /* UI 布局偏好写入失败只降级为本次窗口状态。 */ }
}, { deep: true });

/** 启动恢复初始化：hydrate 后读 scene，设置恢复窗口 */
function initSceneRestore(): void {
  const scene = sessionStore.surface.getScene();
  if (scene) {
    savedScene = { panel: scene.panel, visible: scene.visible, presentation: scene.presentation };
    if (scene.visible) {
      restorePending = true;
      restoreTimer = window.setTimeout(() => { restorePending = false; }, 8000);
    }
  }
}

function onSurface(surface: CapabilityRailSurface) {
  capability.value = surface;
  // 只有「启动恢复待定 && 布局偏好指向的面板」才恢复；不匹配则保留 pending，
  // 等 pullCache 快照回退发出的后续 surface（多段恢复容错）。
  const scene = savedScene;
  const shouldRestore = restorePending && scene !== null && scene.visible && scene.panel === surface.panel;
  if (shouldRestore) {
    if (restoreTimer !== null) { clearTimeout(restoreTimer); restoreTimer = null; }
    restorePending = false;
    presentation.value = scene.presentation;
    surfaceVisible.value = true;
    tab.value = "home";
    void nextTick(() => pluginHost.value?.restoreSurface());
  }
}

// 待 Inline 回执（Task 5）/ 活动轨（Task 6）接入的挂点
const pendingInline = ref<CapabilitySurfaceEvent | null>(null);
const activityFeed = ref<CapabilitySurfaceEvent[]>([]);

function onPanelAvailable(surface: CapabilitySurfaceEvent) {
  capability.value = surface;
  // 表面裁决：模型最多自动展开到 peek；stage/focus 必须有明确意图（裁决器是唯一判据）。
  const { presentation: p, show } = decideSurface({
    suggested: surface.suggested,
    attention: surface.attention,
    explicit: surface.explicit,
    current: surfaceVisible.value ? presentation.value : null,
    supported: surface.supported,
  });
  if (!show) {
    // quiet：只记入活动轨，不展开任何表面（Task 6 消费）
    activityFeed.value.push(surface);
    return;
  }
  if (p === null || p === "inline") {
    // null 理论不可达（quiet 已 return）；inline：过程行原地收束为回执卡（Task 5 消费），不开面板
    if (p === "inline") pendingInline.value = surface;
    return;
  }
  // peek/stage/focus：进入主屏场景（peek 由 Task 5 的浮层承载，stage/focus 走既有场景布局）
  presentation.value = p;
  showSurface();
}

function showSurface() {
  if (!capability.value || sceneClosing.value) return;
  surfaceVisible.value = true;
  tab.value = "home";
  void nextTick(() => pluginHost.value?.restoreSurface());
}

/** 收起工作面：先让面板"缩回"锚点（布局保持场景，避免瞬切突兀），动画播完再收拢场景回主屏。 */
const sceneClosing = ref(false);
async function hideSurface() {
  if (sceneClosing.value || !sceneActive.value) return;
  sceneClosing.value = true;
  try {
    await pluginHost.value?.collapseScene();
  } finally {
    pluginHost.value?.suspendSurface();
    surfaceVisible.value = false;
    presentation.value = "stage";
    sceneClosing.value = false;
  }
}

function closeCapability() {
  sessionStore.surface.clearScene();
  surfaceVisible.value = false;
  presentation.value = "stage";
  capability.value = null;
}

function toggleFocus() {
  if (!surfaceVisible.value) showSurface();
  presentation.value = presentation.value === "focus" ? "stage" : "focus";
}

// 待批准数：顶栏「主屏」徽标（收件箱有待处理的事，一眼可见）
const approvalCount = ref(0);

// ---- ⌘K 全局命令面板 ----
const paletteOpen = ref(false);
function togglePalette() {
  paletteOpen.value = !paletteOpen.value;
}
function onPaletteNavigate(t: PaletteTab) {
  navigate(t as Tab);
  paletteOpen.value = false;
}

function navigate(target: Tab) {
  if (target === "plugins") {
    surfaceVisible.value = false;
    presentation.value = "stage";
    void nextTick(() => pluginHost.value?.backToList());
  } else if (target !== "home" && surfaceVisible.value) {
    pluginHost.value?.suspendSurface();
  } else if (target === "home" && surfaceVisible.value) {
    void nextTick(() => pluginHost.value?.restoreSurface());
  }
  tab.value = target;
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
  if (e.key === "Escape" && sceneActive.value) {
    e.preventDefault();
    if (pluginHost.value?.clearObjectScope()) return;
    if (presentation.value === "focus") presentation.value = "stage";
    else hideSurface();
    return;
  }
  if (!e.metaKey && !e.ctrlKey) return;
  const k = e.key.toLowerCase();
  if (k === "k") {
    e.preventDefault();
    togglePalette();
    return;
  }
  if (k === ",") {
    e.preventDefault();
    navigate("settings");
    return;
  }
  const t = TAB_SHORTCUTS[k];
  if (t) {
    e.preventDefault();
    navigate(t);
  }
}

// 徽标：主屏显待批准数（收件箱有待你动手的事）
const homeBadge = computed(() => approvalCount.value);

let unApprovals: (() => void) | null = null;
onMounted(async () => {
  if (!qaMode) unApprovals = onPendingConfirms((l) => (approvalCount.value = l.length));
  window.addEventListener("keydown", onGlobalKeydown);
  if (isNarrowWindow()) leftRailOpen.value = false; // 非全屏默认收起左栏（按钮仍可展开）
  clockTimer = window.setInterval(() => (clockNow.value = new Date()), 30_000);
  // 启动恢复：hydrate 后按 surface 域 scene 设置布局恢复窗口
  try {
    clearLegacySessionKeys();
    await sessionStore.restore();
    initSceneRestore();
    // window 域：注册主窗 + 当前聚焦会话（多窗协调的最小接入）
    sessionStore.window.updateState("main", {
      visible: true,
      focusedConversationId: sessionStore.conversation.getActiveConversationId(),
      focusedPanelId: capability.value?.panel ?? sessionStore.surface.getPanel()?.panel ?? null,
    });
  } catch { /* 恢复失败不阻塞主屏 */ }
});
onUnmounted(() => {
  unApprovals?.();
  window.removeEventListener("keydown", onGlobalKeydown);
  if (clockTimer !== null) window.clearInterval(clockTimer);
});

function toggleLeftRail() {
  leftRailOpen.value = !leftRailOpen.value;
}

function toggleRightRail() {
  rightRailOpen.value = !rightRailOpen.value;
}

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
      <div class="topbar-row" data-tauri-drag-region>
        <div class="topbar-brand">
          <!-- 顶栏品牌 = 项目 logo（阴阳鱼，与 src-tauri/icons/icon.png 一致）；
               宠物形象 Avatar 不放顶栏，归左栏身份头部（角色与产品品牌分离） -->
          <img class="tb-logo" :src="appLogo" alt="译宝" data-tauri-drag-region />
          <span class="tb-product-note" data-tauri-drag-region>{{ productNote }}</span>
          <span class="tb-status" data-tauri-drag-region>
            <time>{{ clockText }}</time>
            <i aria-hidden="true">·</i>
            <span>{{ weatherNote }}</span>
          </span>
        </div>

        <nav class="tb-nav" data-tauri-drag-region>
          <button
            v-for="n in NAV"
            :key="n.id"
            class="tb-nav-item"
            :class="{ on: tab === n.id && !(sceneActive && n.id === 'plugins') }"
            :title="`⌘${n.shortcut}`"
            @click="navigate(n.id)"
          >
            <YbIcon class="tb-nav-ic" :name="n.icon" :size="15" />
            <span class="tb-nav-label">{{ n.label }}</span>
            <span v-if="n.id === 'home' && homeBadge > 0" class="tb-badge yb-num">{{ homeBadge }}</span>
          </button>
        </nav>

        <div class="tb-right">
          <button
            v-if="capability"
            class="activity-pill"
            :class="{ active: sceneActive, busy: activityBusy }"
            :title="sceneActive ? '收起工作面' : '恢复工作面'"
            @click="sceneActive ? hideSurface() : showSurface()"
          >
            <span class="activity-icon"><YbIcon name="plug" :size="12" /></span>
            <span class="activity-label">{{ capability.title }}</span>
            <i />
          </button>
          <button class="tb-btn" title="命令面板 (⌘K)" @click="togglePalette">
            <YbIcon name="search" :size="15" />
            <kbd class="tb-kbd">⌘K</kbd>
          </button>
          <button class="tb-btn" :class="{ 'tb-theme-auto': theme === 'system' }" :title="themeTitle" :aria-label="themeTitle" @click="cycleTheme">
            <YbIcon :name="themeIcon" :size="15" />
            <i v-if="theme === 'system'" class="tb-theme-dot" aria-hidden="true"></i>
          </button>
          <button class="tb-btn" :class="{ on: tab === 'settings' }" title="设置 (⌘,)" @click="navigate('settings')">
            <YbIcon name="sliders" :size="15" />
          </button>
          <button class="tb-btn" title="收起为小窗" @click="close">
            <YbIcon name="dumpling" :size="15" />
          </button>
        </div>
      </div>
    </header>

    <!-- 内容区：各页常驻挂载，切页只切显隐。
         主屏 = 对话 + 信息面板融合体（AI 交互主入口）；插件面板打开 → 自动切插件页 -->
    <main class="content" :class="{ 'capability-scene': sceneActive, 'capability-focus': sceneActive && presentation === 'focus' }">
      <Transition name="chat-fade">
        <div v-show="tab === 'home' && !sceneActive" class="view-host chat-host">
          <HomeChat v-if="!qaMode" :left-rail-open="leftRailOpen" :right-rail-open="rightRailOpen" @toggle-left="toggleLeftRail" @toggle-right="toggleRightRail" @state="chatState = $event" @open-panel="showSurface" @reminder="navigate('home')" />
        </div>
      </Transition>
      <Transition name="scene-rail">
        <div v-show="sceneActive && presentation === 'stage'" class="capability-rail-host">
          <CapabilityConversationRail :surface="capability" :active="sceneActive" @close="hideSurface" @focus="toggleFocus" />
        </div>
      </Transition>
      <Transition name="scene-panel">
        <div v-show="tab === 'plugins' || sceneActive" class="view-host plugin-host">
        <HomePlugins
          ref="pluginHost"
          :scene="sceneActive"
          :presentation="presentation"
          @state="panelState = $event"
          @panel="onPanelAvailable"
          @surface="onSurface"
          @close="sceneActive ? hideSurface() : closeCapability()"
          @focus="toggleFocus"
        />
        </div>
      </Transition>
      <div v-show="tab === 'data'" class="view-host"><DataView v-if="!qaMode" /></div>
      <div v-show="tab === 'settings'" class="view-host"><SettingsView v-if="!qaMode" /></div>
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
  position: relative;
  height: calc(var(--yb-titlebar-h) + 3px);
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
  position: absolute;
  inset: 0;
  height: auto;
  flex-shrink: 0;
  z-index: 0;
}
.topbar-row {
  position: relative;
  z-index: 1;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  gap: var(--yb-space-3);
  height: calc(var(--yb-titlebar-h) + 3px);
  padding: 0 var(--yb-space-3) 0 74px;
  user-select: none;
}
/* 品牌：项目 logo（与 src-tauri 应用图标同源——阴阳鱼；宠物形象 Avatar 归左栏） */
.topbar-brand {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.tb-logo {
  width: 18px;
  height: 18px;
  display: block;
  user-select: none;
  cursor: default;
  -webkit-user-drag: none;
}
.tb-product-note {
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-xs);
  letter-spacing: 0.01em;
  white-space: nowrap;
}

.tb-status {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-left: 2px;
  color: var(--yb-text-faint);
  font-size: var(--yb-fs-xs);
  letter-spacing: 0.01em;
  white-space: nowrap;
}
.tb-status time {
  color: var(--yb-text-dim);
  font-variant-numeric: tabular-nums;
}
.tb-status i {
  color: var(--yb-border-strong);
  font-style: normal;
}

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
  height: 24px;
  padding: 2px 10px;
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
.tb-rail-btn {
  color: var(--yb-text-faint);
}
.tb-rail-btn.on {
  color: var(--yb-accent);
}
.activity-pill {
  max-width: 230px;
  height: 28px;
  margin-right: 4px;
  padding: 0 8px 0 5px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--yb-card-border);
  border-radius: var(--yb-radius-pill);
  background: var(--yb-card-bg);
  color: var(--yb-text-dim);
  font: inherit;
  font-size: var(--yb-fs-xs);
  cursor: pointer;
  box-shadow: var(--yb-shadow-1);
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.activity-pill:hover,
.activity-pill.active { border-color: rgba(var(--yb-c-sky-rgb), 0.3); color: var(--yb-text); box-shadow: var(--yb-shadow-2); }
.activity-pill .activity-icon { width: 20px; height: 20px; display: grid; place-items: center; border-radius: 50%; background: var(--yb-accent-soft); color: var(--yb-accent); }
.activity-label { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.activity-pill > i { width: 6px; height: 6px; flex-shrink: 0; border-radius: 50%; background: var(--yb-success); }
.activity-pill.busy > i { background: var(--yb-accent); box-shadow: 0 0 0 4px rgba(var(--yb-c-sky-rgb), 0.1); }
.tb-btn {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 24px;
  padding: 0 7px;
  border: none;
  border-radius: var(--yb-radius-sm);
  background: transparent;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-md);
  font-family: inherit;
  cursor: pointer;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
/* 主题「跟随系统」角标：右上小圆点提示 auto，避免与固定浅/深混淆 */
.tb-theme-dot {
  position: absolute;
  top: 2px;
  right: 2px;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--yb-accent);
  box-shadow: 0 0 0 1.5px var(--yb-bg);
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

@media (max-width: 1180px) {
  .tb-status {
    display: none;
  }
}

/* ---- 内容区 ---- */
.content {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  /* 顶部 + 左右 + 底部 accent 氛围光（radial 淡出）：四栏工作台在宽屏下
   * 各处空白由微光承接，整体有"AI 空间"层次而非死白 */
  background:
    radial-gradient(120% 90% at 50% -20%, rgba(var(--yb-c-sky-rgb), 0.05), transparent 55%),
    radial-gradient(90% 50% at 50% 110%, rgba(var(--yb-c-sky-rgb), 0.04), transparent 65%),
    radial-gradient(60% 40% at 0% 50%, rgba(var(--yb-c-sky-rgb), 0.03), transparent 70%),
    radial-gradient(60% 40% at 100% 50%, rgba(var(--yb-c-sky-rgb), 0.03), transparent 70%),
    var(--yb-content-bg);
}
.content > * {
  flex: 1;
  min-height: 0;
}
.view-host {
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.view-host > * { flex: 1; min-height: 0; }
.content.capability-scene {
  display: grid;
  grid-template-columns: minmax(286px, 310px) minmax(0, 1fr);
  grid-template-rows: minmax(0, 1fr);
}
/* 场景进入动效：协作轨从左侧滑入；插件宿主像从译宝内部"长出来"（spring 弹性，上浮展开）。
 * leave 不做宿主级动画——收起工作面即回到原对话位置，面板自身的同源缩回由 HomePlugins 负责 */
.capability-rail-host {
  grid-column: 1;
  min-width: 0;
  min-height: 0;
}
.scene-rail-enter-active {
  transition: opacity 0.22s var(--yb-ease-out), transform 0.22s var(--yb-ease-out);
}
.scene-rail-enter-from {
  opacity: 0;
  transform: translateX(-14px);
}
.scene-panel-enter-active {
  transition: opacity 0.3s var(--yb-ease-spring), transform 0.3s var(--yb-ease-spring);
}
.scene-panel-enter-from {
  opacity: 0;
  transform: translateY(12px) scale(0.97);
  transform-origin: 50% 0%;
}
/* 收起工作面后主屏对话淡入承接（面板缩回 → 对话出现，避免场景整体瞬切） */
.chat-fade-enter-active {
  transition: opacity 0.18s var(--yb-ease-out);
}
.chat-fade-enter-from {
  opacity: 0;
}
.capability-scene .plugin-host {
  grid-column: 2;
  min-width: 0;
  min-height: 0;
}
.content.capability-focus { grid-template-columns: minmax(0, 1fr); }
.capability-focus .plugin-host { grid-column: 1; }

@media (max-width: 980px) {
  .content.capability-scene { grid-template-columns: minmax(0, 1fr); }
  .capability-scene .capability-rail-host { display: none !important; }
  .capability-scene .plugin-host { grid-column: 1; }
}
@media (max-width: 760px) {
  .activity-pill { max-width: 40px; padding-right: 5px; }
  .activity-pill .activity-label { display: none; }
  .tb-kbd { display: none; }
}
</style>
