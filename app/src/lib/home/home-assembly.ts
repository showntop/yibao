/** 主屏装配：零件目录 + 摊法 + 两套落点（grid / canvas）。
 * 已按职责拆分至 assembly/（parts / snap / presets / layout），本文件为兼容 re-export 入口。
 * 原 livePluginIds（Vue 响应式）已迁至 composables/useAssembly。 */
export * from "../assembly/parts";
export * from "../assembly/snap";
export * from "../assembly/presets";
export * from "../assembly/layout";
