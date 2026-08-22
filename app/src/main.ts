import { createApp } from "vue";
import App from "./App.vue";
import "./assets/tokens.css";
import "./assets/scrollbar.css";
import "./assets/settings.css";
import "./assets/home-feed.css";
import { bootFinish } from "./lib/finish";

// 主题：必须在 mount 前应用到 <html>，否则首屏会先按系统偏好渲染再切换，闪一下。
// yibao-theme: "light" | "dark" | "system"（缺省 = 跟随系统，tokens.css 媒体查询生效）
const saved = localStorage.getItem("yibao-theme");
if (saved === "light" || saved === "dark") document.documentElement.dataset.theme = saved;
bootFinish();

createApp(App).mount("#app");
