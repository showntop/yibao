// 面板窗入口（独立于宠物窗 main.ts；令牌同源，避免复制漂移）。
import { createApp } from "vue";
import PanelApp from "./windows/panel/PanelWindow.vue";
import "./assets/tokens.css";

// yibao-theme 引导（与 main.ts/home.ts 同一约定）：浮窗内的 SchemaPanel 与
// WebviewPanel 主题通道（effectiveTheme 读 documentElement.dataset.theme）都依赖它。
const saved = localStorage.getItem("yibao-theme");
if (saved === "light" || saved === "dark") document.documentElement.dataset.theme = saved;

createApp(PanelApp).mount("#app");
