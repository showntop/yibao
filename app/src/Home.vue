<script setup lang="ts">
// 大窗根组件（完整 APP 主界面）：macOS 原生窗口语言——左侧 sidebar + 右侧内容区。
// 壳由系统负责（TitleBarStyle::Overlay，见 lib.rs）：原生红绿灯、系统阴影、缩放边框，
// 前端不再自绘圆角玻璃卡。侧栏顶部留 --yb-titlebar-h 安全区给浮在内容上的红绿灯。
// 四页常驻挂载（v-show 切显隐）：事件订阅不断、气泡/面板状态切页不丢。
import { computed, nextTick, onMounted, onUnmounted, ref } from "vue";
import { invoke } from "@tauri-apps/api/core";
import Avatar from "./components/Avatar.vue";
import YbIcon from "./components/YbIcon.vue";
import HomeFeed from "./components/HomeFeed.vue";
import HomeChat from "./components/HomeChat.vue";
import HomePlugins from "./components/HomePlugins.vue";
import SettingsView from "./components/SettingsView.vue";
import { onPendingConfirms, onSettings, onHomeTab, getSettingsOnce, type SettingsValues } from "./lib/brain";

type Tab = "home" | "chat" | "plugins" | "settings";
type AvatarState = "idle" | "listen" | "think" | "work" | "say" | "success" | "error";

const tab = ref<Tab>("home");
// 两页各自的会话状态：侧边栏团子跟随「当前页」的状态（主屏/设置沿用对话页）
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
// 主屏 → 对话页的草稿传递（Feed 点击带上下文追问）
const chatDraft = ref("");
// 待批准数：sidebar 徽标（收件箱有待处理的事，一眼可见）
const approvalCount = ref(0);
// 未读动态数：sidebar 徽标（HomeFeed 经 emit 同步，stats.unread）
const feedUnread = ref(0);
// 感知观察中叠加点（Avatar observing prop）：总开关 + 任一采集源开启即视为观察中
const observing = ref(false);
function syncObserving(s: SettingsValues | null) {
  observing.value = !!(
    s?.["perception.master"] &&
    (s?.["perception.app"] || s?.["perception.activity"] || s?.["perception.screen"])
  );
}
let unApprovals: (() => void) | null = null;
let unSettings: (() => void) | null = null;
let unHomeTab: (() => void) | null = null;
onMounted(async () => {
  unApprovals = onPendingConfirms((l) => (approvalCount.value = l.length));
  void getSettingsOnce().then(syncObserving);
  unSettings = await onSettings(syncObserving);
  unHomeTab = await onHomeTab((t) => {
    if (t === "home" || t === "chat" || t === "plugins") tab.value = t;
  });
});
onUnmounted(() => {
  unApprovals?.();
  unSettings?.();
  unHomeTab?.();
});

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

// sidebar 分区（macOS 惯例：用小标题把导航项按语义分组，而非一列平铺）
const NAV: { id: Tab; label: string; icon: "inbox" | "chat" | "plug" | "gear" }[] = [
  { id: "home", label: "主屏", icon: "inbox" },
  { id: "chat", label: "对话", icon: "chat" },
  { id: "plugins", label: "插件", icon: "plug" },
];
// 徽标：主屏显未读动态数，其次待批准数（都为 0 则不显）
const homeBadge = computed(() => (feedUnread.value > 0 ? feedUnread.value : approvalCount.value));

function close() {
  // 收起大窗 = 隐藏 + 回小窗模式（Rust 侧还原宠物窗/面板浮窗）
  void invoke("close_home_window").catch(() => {});
}
</script>

<template>
  <div class="home-shell">
    <!-- 侧栏：macOS sidebar——顶部让位红绿灯，身份区 + 分区导航 + 底部设置 -->
    <aside class="sidebar">
      <div class="titlebar-safe" data-tauri-drag-region></div>

      <div class="identity" data-tauri-drag-region>
        <Avatar :state="railState" :size="30" :observing="observing" />
        <div class="id-meta" data-tauri-drag-region>
          <span class="id-name" data-tauri-drag-region>译宝</span>
          <span class="id-state" :class="railState" data-tauri-drag-region>
            <i class="id-dot" />{{ stateText }}
          </span>
        </div>
      </div>

      <nav class="nav">
        <div class="nav-head">工作区</div>
        <button
          v-for="n in NAV"
          :key="n.id"
          class="nav-item"
          :class="{ on: tab === n.id }"
          @click="tab = n.id"
        >
          <YbIcon class="nav-ic" :name="n.icon" :size="15" />
          <span class="nav-label">{{ n.label }}</span>
          <span v-if="n.id === 'home' && homeBadge > 0" class="nav-badge yb-num">{{ homeBadge }}</span>
        </button>
      </nav>

      <div class="sidebar-foot">
        <button class="nav-item" :class="{ on: tab === 'settings' }" @click="tab = 'settings'">
          <YbIcon class="nav-ic" name="gear" :size="15" />
          <span class="nav-label">设置</span>
        </button>
        <button class="nav-item collapse" title="收起大窗（回到译宝小窗）" @click="close">
          <YbIcon class="nav-ic" name="dumpling" :size="15" />
          <span class="nav-label">收起为小窗</span>
        </button>
      </div>
    </aside>

    <!-- 内容区：四页常驻挂载，切页只切显隐。
         reminder / 新面板打开 → 自动切到对应页；主屏提交/点动态 → 切对话页 -->
    <main class="content">
      <HomeFeed v-show="tab === 'home'" @chat="onFeedChat" @unread="feedUnread = $event" />
      <HomeChat v-show="tab === 'chat'" :draft="chatDraft" @state="chatState = $event" @open-panel="tab = 'plugins'" @reminder="tab = 'chat'" />
      <HomePlugins v-show="tab === 'plugins'" @state="panelState = $event" @panel="tab = 'plugins'" />
      <SettingsView v-show="tab === 'settings'" />
    </main>
  </div>
</template>

<style scoped>
/* 壳：原生窗口——不自绘圆角/阴影/玻璃，系统负责。只管内部两栏布局。 */
.home-shell {
  height: 100vh;
  display: flex;
  overflow: hidden;
  font-family: var(--yb-font);
  font-size: var(--yb-fs-lg);
  line-height: var(--yb-lh-base);
  color: var(--yb-text);
  background: var(--yb-content-bg);
}

/* ---- 侧栏 ---- */
.sidebar {
  width: var(--yb-sidebar-w);
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  padding: 0 var(--yb-space-2) var(--yb-space-2);
  background: var(--yb-sidebar-bg);
  border-right: 1px solid var(--yb-border-base);
}
/* 红绿灯安全区：Overlay 标题栏下按钮浮在内容上，这块只作留白 + 拖窗把手 */
.titlebar-safe {
  height: var(--yb-titlebar-h);
  flex-shrink: 0;
}
/* 身份区：白底小卡衬托团子（浅灰侧栏上直接放团子会发飘） */
.identity {
  display: flex;
  align-items: center;
  gap: var(--yb-space-2);
  margin-bottom: var(--yb-space-4);
  padding: var(--yb-space-2);
  border-radius: var(--yb-radius-xs);
  background: var(--yb-card-bg);
  border: 1px solid var(--yb-card-border);
  user-select: none;
}
.id-meta {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
  cursor: default;
}
.id-name {
  font-size: var(--yb-fs-lg);
  font-weight: var(--yb-fw-bold);
  line-height: var(--yb-lh-tight);
}
/* 状态：sidebar 里用裸文字 + 色点（比胶囊更克制，符合 macOS 侧栏调性） */
.id-state {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: var(--yb-fs-xs);
  color: var(--yb-text-dim);
  line-height: var(--yb-lh-tight);
}
.id-dot {
  width: 5px;
  height: 5px;
  flex-shrink: 0;
  border-radius: 50%;
  background: var(--dot, var(--yb-state-idle));
}
.id-state.idle { --dot: var(--yb-state-idle); }
.id-state.listen { --dot: var(--yb-state-listen); }
.id-state.think { --dot: var(--yb-state-think); }
.id-state.work { --dot: var(--yb-state-work); }
.id-state.say { --dot: var(--yb-state-say); }
.id-state.success { --dot: var(--yb-state-success); }
.id-state.error { --dot: var(--yb-state-error); }

.nav {
  display: flex;
  flex-direction: column;
  gap: 1px;
}
/* 分区小标题：macOS sidebar 惯例（小号、灰、字距略开） */
.nav-head {
  padding: 0 var(--yb-space-2) var(--yb-space-1);
  font-size: var(--yb-fs-xs);
  font-weight: var(--yb-fw-bold);
  color: var(--yb-sidebar-head);
  letter-spacing: 0.04em;
  user-select: none;
}
.nav-item {
  display: flex;
  align-items: center;
  gap: var(--yb-space-2);
  width: 100%;
  padding: 6px var(--yb-space-2);
  border: none;
  border-radius: var(--yb-radius-xs);
  background: transparent;
  color: var(--yb-text);
  font-size: var(--yb-fs-lg);
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  transition: background var(--yb-dur-fast) var(--yb-ease-out);
}
.nav-ic {
  flex-shrink: 0;
  color: var(--yb-text-dim);
}
.nav-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.nav-item:hover {
  background: var(--yb-sidebar-sel);
}
/* 选中态：macOS 用 accent 实底 + 白字白图标（不是描边） */
.nav-item.on {
  background: var(--yb-sidebar-sel-active);
  color: var(--yb-text-on-accent);
}
.nav-item.on .nav-ic {
  color: var(--yb-text-on-accent);
}
/* 徽标：选中态下反白 */
.nav-badge {
  flex-shrink: 0;
  min-width: 18px;
  height: 17px;
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
.nav-item.on .nav-badge {
  background: rgba(255, 255, 255, 0.28);
}
.sidebar-foot {
  margin-top: auto;
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.collapse .nav-label,
.collapse .nav-ic {
  color: var(--yb-text-dim);
}
.collapse:hover .nav-label,
.collapse:hover .nav-ic {
  color: var(--yb-text);
}

/* ---- 内容区 ---- */
.content {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: var(--yb-content-bg);
}
.content > * {
  flex: 1;
  min-height: 0;
}
</style>
