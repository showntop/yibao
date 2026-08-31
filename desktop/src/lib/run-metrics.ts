/** 运行指标（tokens / 耗时 / 模型名）显示开关：开发者调试信息，默认关。
 *  纯界面偏好走 localStorage（同 finish/chrome），不写大脑 settings.json、无需重启。 */
import { ref } from "vue";

const KEY = "yibao-run-metrics";

export function readShowRunMetrics(): boolean {
  try {
    return localStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

/** 全局共享 ref：设置页开关与对话页脚同一份状态，切换即时生效。 */
export const showRunMetrics = ref(readShowRunMetrics());

export function setShowRunMetrics(on: boolean): void {
  showRunMetrics.value = on;
  try {
    localStorage.setItem(KEY, on ? "1" : "0");
  } catch { /* 写不进去（如存储被禁）就保持内存态 */ }
}
