import { createRouter, createWebHashHistory } from "vue-router";
// hash 路由：Capacitor webview 加载本地文件，history 模式在 file:// 下路由会断
import { loadConn } from "./api/connection";

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", redirect: "/chat" },
    { path: "/pairing", component: () => import("./views/Pairing.vue") },
    { path: "/chat", component: () => import("./views/Chat.vue") },
  ],
});

router.beforeEach(async (to) => {
  if (to.path !== "/pairing" && !(await loadConn())) return "/pairing";
});
