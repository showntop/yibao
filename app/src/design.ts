// 设计预览入口（仅开发用，不进产品窗口）：按 ?theme=healing|sleek 渲染主题对比。
import { createApp } from "vue";
import "./assets/tokens.css";
import DesignPreview from "./DesignPreview.vue";

createApp(DesignPreview).mount("#app");
