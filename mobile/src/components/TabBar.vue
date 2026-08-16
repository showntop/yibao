<script setup lang="ts">
import { pendingCount } from "../state/pending-badge";

// 底部导航四项（M2）：对话/动态/审批[角标]/设置；当前高亮交给 router-link-active。
// 审批角标读模块级单例 ref（跨兄弟组件共享，见 state/pending-badge.ts 说明）；
// 模板里顶层 import 的 ref 自动解包，直接 pendingCount 即数值。
const tabs = [
  { to: "/chat", icon: "💬", label: "对话" },
  { to: "/feed", icon: "📰", label: "动态" },
  { to: "/approvals", icon: "⏳", label: "审批" },
  { to: "/settings", icon: "⚙️", label: "设置" },
];
</script>

<template>
  <nav class="tabbar">
    <router-link v-for="t in tabs" :key="t.to" :to="t.to" class="tab">
      <span class="ico">
        {{ t.icon }}
        <span v-if="t.to === '/approvals' && pendingCount > 0" class="badge">
          {{ pendingCount > 9 ? "9+" : pendingCount }}
        </span>
      </span>
      <span class="label">{{ t.label }}</span>
    </router-link>
  </nav>
</template>

<style scoped>
/* fixed 底部常驻：各页容器用 padding-bottom: calc(52px + env(safe-area-inset-bottom)) 让位 */
.tabbar {
  position: fixed; bottom: 0; left: 0; right: 0; z-index: 10;
  display: flex; height: 52px; padding-bottom: env(safe-area-inset-bottom);
  background: rgba(245, 245, 247, 0.92);
  backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
  border-top: 1px solid rgba(128, 128, 128, 0.18);
}
@media (prefers-color-scheme: dark) {
  .tabbar { background: rgba(17, 17, 17, 0.92); }
}
.tab { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 2px; text-decoration: none; color: #8e8e93; }
.tab.router-link-active { color: #2f6fed; }
.ico { position: relative; font-size: 20px; line-height: 1; }
.label { font-size: 10px; }
/* 审批角标：iOS 风大红点，盖在图标右上 */
.badge { position: absolute; top: -6px; right: -14px; min-width: 16px; height: 16px;
  padding: 0 4px; border-radius: 8px; background: #ff453a; color: #fff;
  font-size: 11px; line-height: 16px; text-align: center; font-weight: 600; }
</style>
