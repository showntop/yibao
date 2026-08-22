// 装配响应式状态：在线插件零件 id 列表。
// lib/assembly/* 保持纯函数；这里的 ref 是唯一持有"当前在线插件零件"的响应式真源，
// 供装配解析（resolveAssembly 的 pluginIds）与 HomeChat 插件槽共享。
import { ref } from "vue";
import { resetPluginParts, syncPluginParts, type PartId } from "../lib/assembly/parts";

/** 当前在线插件零件 id（跨组件共享的模块级单例）。 */
export const livePluginIds = ref<PartId[]>([]);

export function useLivePluginIds() {
  return {
    ids: livePluginIds,
    /** 同步插件零件：注册进 PARTS 注册表并更新在线列表（返回列表）。 */
    sync(widgets: readonly { panel: string }[]): PartId[] {
      livePluginIds.value = syncPluginParts(widgets);
      return livePluginIds.value;
    },
    reset(): void {
      resetPluginParts();
      livePluginIds.value = [];
    },
  };
}
