import { createApp } from "vue";
import App from "./App.vue";
import "./style.css";

// 宿主主题通道（WebviewPanel 推 {type:"theme", theme:"light"|"dark"}）：
// 面板是 yibao-plugin:// 独立文档，吃不到宿主 tokens.css 的 data-theme 显式通道；
// 收到显式值写自己的 data-theme（命中 style.css 显式深色块），未收到则跟随系统（媒体查询块）。
if (window.yibao?.onMessage) {
  window.yibao.onMessage((m) => {
    if (m?.type !== "theme") return;
    if (m.theme === "light" || m.theme === "dark") document.documentElement.dataset.theme = m.theme;
    else delete document.documentElement.dataset.theme;
  });
}

createApp(App).mount("#app");
