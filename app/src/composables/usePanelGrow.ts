// 面板"从来源长出"动效域（HomePlugins 用）：记录触发插件卡的位置，面板用 clip-path
// 从卡片矩形生长/缩回（同源缩回）。独立成 composable：纯动效逻辑，不含业务状态。
import { ref } from "vue";
import { takeDeskOrigin } from "../lib/home-desk-presence";

export function usePanelGrow() {
  const originRect = ref<DOMRect | null>(null);
  /** 收起时实际使用的目标锚点（用户卡片 / fallbackOrigin），供恢复时对称长回。 */
  const collapseAnchor = ref<DOMRect | null>(null);
  const panelViewEl = ref<HTMLElement | null>(null);
  let animLock = false;

  function captureOrigin(event?: Event): void {
    const card = (event?.currentTarget as HTMLElement | null)?.closest?.(".pcard");
    originRect.value = card?.getBoundingClientRect() ?? null;
  }

  function rectToInset(from: DOMRect, to: DOMRect): string {
    return `inset(${Math.max(0, from.top - to.top)}px ${Math.max(0, to.right - from.right)}px ${Math.max(0, to.bottom - to.bottom)}px ${Math.max(0, from.left - to.left)}px)`;
  }

  function prefersReducedMotion(): boolean {
    return window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches ?? false;
  }

  /** 无来源（模型自动展开/挂载补拉）时的默认生长起点：面板区域右中偏上的小块，不抢中心。 */
  function fallbackOrigin(to: DOMRect): DOMRect {
    const w = Math.min(200, to.width * 0.45);
    const h = Math.min(120, to.height * 0.3);
    return new DOMRect(to.left + to.width * 0.72 - w / 2, to.top + to.height * 0.4 - h / 2, w, h);
  }

  /** 面板就位后：从来源矩形"长"满自身区域（matched-geometry，240ms 弹性）。
   *  锚点优先级：用户点击的插件卡 > 上次收起用的锚点 > 右中偏上 fallback（模型自动展开/QA 模式）
   *  fill:forwards 关键：collapseOut 用了 fill:forwards 保持缩回末态，若 growIn 不带 fill，
   *  跑完会"弹回"到旧 fill-forwards 的缩回态——这里 forwards 让全屏末态持续压过。 */
  function growIn() {
    if (animLock) return;
    const el = panelViewEl.value;
    if (!el || prefersReducedMotion()) return;
    const to = el.getBoundingClientRect();
    if (to.width < 2 || to.height < 2) return; // 宿主还不可见（主屏挂载收到 panel）时跳过，进入场景由 scene-panel 承接
    const glance = originRect.value ?? takeDeskOrigin();
    const from = glance ?? collapseAnchor.value ?? fallbackOrigin(to);
    if (glance) originRect.value = glance;
    el.animate(
      [
        { clipPath: rectToInset(from, to), opacity: 0.55, transform: "scale(0.985)" },
        { clipPath: "inset(0px)", opacity: 1, transform: "scale(1)" },
      ],
      { duration: 240, easing: "cubic-bezier(0.22, 0.61, 0.36, 1)", fill: "forwards" },
    );
    collapseAnchor.value = null; // 临时锚点用完即弃，避免下次误用
  }

  /** 收起时反向：缩回来源锚点（同源缩回，卡片或默认锚点）。
   *  keepOrigin=true 时保留来源矩形（用户意图），但无论 keepOrigin 都记录实际目标锚点供恢复对称。 */
  async function collapseOut(keepOrigin = false): Promise<void> {
    if (animLock) return;
    if (prefersReducedMotion()) return; // 减少动效时直接收起
    animLock = true;
    try {
      const el = panelViewEl.value;
      if (el) {
        const from = el.getBoundingClientRect();
        if (from.width < 2 || from.height < 2) return;
        const to = originRect.value ?? fallbackOrigin(from);
        collapseAnchor.value = to; // 记住收起到的锚点，恢复时从同处长回
        const inset = rectToInset(to, from);
        const anim = el.animate(
          [
            { clipPath: "inset(0px)", opacity: 1, transform: "scale(1)" },
            { clipPath: inset, opacity: 0.45, transform: "scale(0.985)" },
          ],
          { duration: 200, easing: "cubic-bezier(0.4, 0, 0.2, 1)", fill: "forwards" },
        );
        await anim.finished.catch(() => {});
      }
    } finally {
      if (!keepOrigin) originRect.value = null;
      animLock = false;
    }
  }

  return {
    originRect,
    collapseAnchor,
    panelViewEl,
    captureOrigin,
    growIn,
    collapseOut,
  };
}
