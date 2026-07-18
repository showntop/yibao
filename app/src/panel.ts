// 面板窗入口（独立于宠物窗 main.ts；令牌同源，避免复制漂移）。
import { createApp } from "vue";
import PanelApp from "./components/PanelApp.vue";
import "./assets/tokens.css";

createApp(PanelApp).mount("#app");
