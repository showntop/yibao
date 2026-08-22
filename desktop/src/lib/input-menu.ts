import { ref } from "vue";

/** 输入框的命令/文件菜单是否打开。
 *  桌宠收起态 QuickPanel 由 hover 热区驱动显示（pet-cursor-leave 即收起）：
 *  鼠标移向菜单项（离开团子热区）时若立即收起，面板连同菜单一起消失、点不中；
 *  菜单打开期间暂停 hover 收起，菜单关闭后再按热区逻辑正常收起。 */
export const inputMenuOpen = ref(false);

/** 加号菜单（附件/项目文件）是否打开。
 *  桌宠下 Rust 只放行前端上报的热区矩形，超出 .wb-input 几何的加号菜单需单独上报
 *  让 macOS 不忽略该区域的鼠标事件。 */
export const addMenuOpen = ref(false);
