// 对话浮层域（HomePlugins 工作台条上方）：输入/回复留痕成时间线；一轮结束几秒后
// 自动收起，角标可重开。proc = 过程展示行（工具调用，样式同 hint 淡色小字）。
// pstate 驱动图标与颜色，不把状态符号拼进 text——文案与呈现分离，图标统一走 YbIcon。
import { nextTick, ref } from "vue";

export type ThreadMsg = {
  role: "user" | "ai" | "hint" | "proc";
  text: string;
  pstate?: "run" | "ok" | "fail";
  halted?: boolean; // 被打断：行尾显示中止图标
};

export function usePluginOverlay() {
  const msgs = ref<ThreadMsg[]>([]);
  /** 过程展示：action.id → 过程行下标，结果回来原地更新。 */
  const procIdx = new Map<string, number>();
  const streamingIdx = ref<number | null>(null); // 正在接收 chunk 的 ai 气泡下标
  const layerVisible = ref(false);
  const listeningHint = ref(false); // 聆听占位行（识别完替换为用户气泡）
  const layerRef = ref<HTMLElement | null>(null);
  let collapseTimer: ReturnType<typeof setTimeout> | null = null;

  function openLayer() {
    layerVisible.value = true;
    if (collapseTimer !== null) {
      clearTimeout(collapseTimer);
      collapseTimer = null;
    }
  }

  /** 一轮结束后自动收起：浮层是干活时的环境反馈，不是常驻聊天窗。 */
  function scheduleCollapse(ms: number) {
    if (collapseTimer !== null) clearTimeout(collapseTimer);
    collapseTimer = setTimeout(() => {
      layerVisible.value = false;
      collapseTimer = null;
    }, ms);
  }

  function pushMsg(role: ThreadMsg["role"], text: string) {
    msgs.value.push({ role, text });
    openLayer();
    scrollSoon();
  }

  function scrollSoon() {
    void nextTick(() => {
      const el = layerRef.value;
      if (el) el.scrollTop = el.scrollHeight;
    });
  }

  /** 组件卸载时清定时器（宿主 onUnmounted 调用）。 */
  function dispose() {
    if (collapseTimer !== null) clearTimeout(collapseTimer);
  }

  return {
    msgs,
    procIdx,
    streamingIdx,
    layerVisible,
    listeningHint,
    layerRef,
    openLayer,
    scheduleCollapse,
    pushMsg,
    scrollSoon,
    dispose,
  };
}
