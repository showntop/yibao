// 收起态回复气泡域：speech 内容/流式/显隐 + 自动收起定时器。
// 与快捷面板互斥（区域重叠）；依赖 quick 与窗口热区刷新经 deps 注入。
import { ref, type Ref } from "vue";

export interface PetSpeechDeps {
  quick: Ref<boolean>;
  syncHotRects: () => void;
}

export function usePetSpeech(deps: PetSpeechDeps) {
  const speech = ref<string | null>(null);
  const speechStreaming = ref(false);
  const speechVisible = ref(false);
  let speechTimer: ReturnType<typeof setTimeout> | null = null;

  /** 显示收起态回复气泡（回复类事件用；与快捷面板互斥，区域重叠） */
  function showSpeechBubble() {
    speechVisible.value = true;
    deps.quick.value = false;
    deps.syncHotRects();
  }

  /** 隐藏并清空气泡；timer 到期调用 */
  function hideSpeechBubble() {
    speechVisible.value = false;
    speech.value = null;
    speechStreaming.value = false;
    deps.syncHotRects();
  }

  /** 设置自动收起定时器（多条路径共用，避免重复 setTimeout）。 */
  function scheduleAutoHide(ms: number) {
    if (speechTimer) clearTimeout(speechTimer);
    speechTimer = setTimeout(hideSpeechBubble, ms);
  }

  /** 取消自动收起（内容迁入气泡流/组件卸载时）。 */
  function cancelAutoHide() {
    if (speechTimer) {
      clearTimeout(speechTimer);
      speechTimer = null;
    }
  }

  /** 卸载时清理定时器。 */
  function dispose() {
    cancelAutoHide();
  }

  return {
    speech,
    speechStreaming,
    speechVisible,
    showSpeechBubble,
    hideSpeechBubble,
    scheduleAutoHide,
    cancelAutoHide,
    dispose,
  };
}
