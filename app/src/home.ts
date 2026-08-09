// 大窗入口（独立于宠物窗 main.ts；令牌同源，避免复制漂移）。
import { createApp } from "vue";
import Home from "./Home.vue";
import "./assets/tokens.css";

// 主题：必须在 mount 前应用到 <html>，否则首帧会按系统偏好渲染再切换（闪屏）。
// yibao-theme: "light" | "dark" | "system"（缺省 = 跟随系统，tokens.css 媒体查询生效）
const saved = localStorage.getItem("yibao-theme");
if (saved === "light" || saved === "dark") document.documentElement.dataset.theme = saved;

createApp(Home).mount("#app");
