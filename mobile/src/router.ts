import { createRouter, createWebHashHistory } from "vue-router";
// hash 路由：Capacitor webview 加载本地文件，history 模式在 file:// 下路由会断
import { loadConn } from "./api/connection";

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", redirect: "/chat" },
    { path: "/pairing", component: () => import("./views/Pairing.vue") },
    { path: "/chat", component: () => import("./views/Chat.vue") },
    // M2 底部导航四页：feed 与 approvals 平级；settings 收尾（T3 充实记忆库）
    { path: "/feed", component: () => import("./views/Feed.vue") },
    { path: "/approvals", component: () => import("./views/Approvals.vue") },
    { path: "/settings", component: () => import("./views/Settings.vue") },
  ],
});

router.beforeEach(async (to) => {
  if (to.path !== "/pairing" && !(await loadConn())) return "/pairing";
});
