// 小窗气泡流域：消息列表 + 流式下标 + 过程行/表面锚点索引 + 滚动跟随 + 常驻轻提示。
// 由 App.vue 的 onEvent 等驱动（本 composable 返回可变引用，App 直接操作）。
import { nextTick, ref, watch, type Ref } from "vue";
import type { SurfaceAttr } from "../lib/surface/pet-surface";

/** 气泡消息（pstate：过程行状态，图标随态渲染；halted：被打断；icon：行首语义图标） */
export type BubbleMsg = {
  role: "user" | "ai" | "sys";
  text: string;
  pstate?: "run" | "ok" | "fail";
  halted?: boolean;
  icon?: "clock" | "alert" | "doc";
  /** 晨间反刍 deep-link：morning_recap 提醒气泡携带 day 字符串时，点击切到 home 回顾视图 */
  recap?: string;
  /** 表面属性（Phase 1.5）：与 pstate 正交。有则该行渲染为面板入口 */
  surface?: SurfaceAttr;
};

export interface PetBubblesDeps {
  expanded: Ref<boolean>;
  expand: () => Promise<void>;
}

export function usePetBubbles(deps: PetBubblesDeps) {
  const { expanded, expand } = deps;

  const bubbles = ref<BubbleMsg[]>([]);
  /** 正在接收 chunk 的 bubble 下标 */
  const streamingIdx = ref<number | null>(null);
  /** 过程展示：action.id → 过程行（sys 淡色小字）在 bubbles 里的下标，结果回来原地更新 */
  const procIdx = new Map<string, number>();
  /** action id → 过程行下标：panel 事件按 origin 找回该行补表面属性。
   *  必须与 procIdx 分开：procIdx 在 action_result 就删了，
   *  而 panel 事件在 action_result 之后才到（loop.py:331 → :337）。 */
  const surfaceAnchor = new Map<string, number>();

  /** 告警气泡：⚠️ 前缀改行首 alert 图标渲染（文案纯净，图标走 YbIcon） */
  function pushWarn(text: string) {
    bubbles.value.push({ role: "ai", text, icon: "alert" });
  }

  /** 常驻轻提示（reminder 等「有事找你」）：展开对话窗 + 落一条提醒气泡。 */
  function openBubbleSticky(text: string) {
    if (expanded.value) return;
    bubbles.value.push({ role: "ai", text, icon: "alert" });
    void expand();
  }

  // ---- 气泡流滚动：新气泡平滑到底、流式 chunk 即时跟手 ----
  const bubblesRef = ref<HTMLElement | null>(null);
  function scrollBubbles(smooth: boolean) {
    void nextTick(() => {
      const el = bubblesRef.value;
      if (!el) return;
      if (smooth) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
      else el.scrollTop = el.scrollHeight;
    });
  }
  watch(() => bubbles.value.length, () => scrollBubbles(true));
  watch(() => bubbles.value[bubbles.value.length - 1]?.text, () => scrollBubbles(false));

  // 对话区挂在 v-if="expanded" 上：收起即拆 DOM，滚动位置归零。
  // 再展开时 bubbles 已在内存、length 不变，上面的 watch 不触发，会停在最顶。
  // 收起前记下「是否贴底 / 滚动偏移」，展开后恢复；贴底或没有记录则滚到最新。
  // 恢复时序：nextTick + rAF 尝试，若容器（bubblesRef）尚未同步到新挂载的 .bubbles
  // 则保留请求，由 watch(bubblesRef) 在容器就绪时兜底执行——避免展开/切视图后停在开头。
  const STICK_BOTTOM_PX = 80;
  let stickBottom = true;
  let savedScrollTop = 0;
  let restoreFn: (() => void) | null = null;

  function applyRestore() {
    const el = bubblesRef.value;
    if (!el) return;
    el.scrollTop = stickBottom ? el.scrollHeight : savedScrollTop;
  }

  function captureBubbleScroll() {
    const el = bubblesRef.value;
    if (!el) {
      // 容器不在（收起态/视图切换）：展开后按贴底处理，保证看到最新
      stickBottom = true;
      savedScrollTop = 0;
      return;
    }
    stickBottom = el.scrollHeight - el.scrollTop - el.clientHeight < STICK_BOTTOM_PX;
    savedScrollTop = el.scrollTop;
  }

  function restoreBubbleScroll() {
    restoreFn = applyRestore;
    void nextTick(() => {
      requestAnimationFrame(() => {
        if (!bubblesRef.value) return; // 容器未就绪：留给 watch(bubblesRef) 兜底
        const fn = restoreFn;
        restoreFn = null;
        fn?.();
      });
    });
  }

  // 容器重新挂载（展开对话 / 插件视图切回）且仍有未消费的恢复请求：立即恢复
  watch(bubblesRef, (el) => {
    if (restoreFn && el) {
      const fn = restoreFn;
      restoreFn = null;
      requestAnimationFrame(fn);
    }
  });

  /** 列表整表重建后复位滚动记忆（reloadMessages 用）。 */
  function resetScroll() {
    stickBottom = true;
    savedScrollTop = 0;
    restoreFn = null;
  }

  return {
    bubbles,
    streamingIdx,
    procIdx,
    surfaceAnchor,
    pushWarn,
    openBubbleSticky,
    bubblesRef,
    scrollBubbles,
    captureBubbleScroll,
    restoreBubbleScroll,
    resetScroll,
  };
}
