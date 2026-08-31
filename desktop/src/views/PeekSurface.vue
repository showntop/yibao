<script setup lang="ts">
// Peek 探窗（Phase 1）：从调用锚点"长出"的轻表面——不重排主屏、不占独立导航历史、
// 用户仍能看见背后的对话。Esc / 点空白 / 完成动作后缩回原锚点。
// 复用 Slice 1 matched-geometry 时序（240ms 弹性生长 / 200ms 同源缩回）。
import { computed, onMounted, onBeforeUnmount, ref } from "vue";
import SchemaPanel from "../components/panel/SchemaPanel.vue";
import WebviewPanel from "../components/panel/WebviewPanel.vue";
import YbIcon from "../components/common/YbIcon.vue";
import { onBrainEvent, panelAction, type BrainEvent } from "../lib/brain";
import type { WebviewPayload } from "../lib/webview-source";

const props = withDefaults(
  defineProps<{
    panel: string; // 面板引用（plugin_id:name）
    title: string; // 面板显示名
    provider: string; // 插件 id
    schema: Record<string, any> | null;
    webview: WebviewPayload | null;
    data: Record<string, unknown>;
  }>(),
  { schema: null, webview: null },
);
const emit = defineEmits<{ close: []; expand: [] }>();

const rootEl = ref<HTMLElement | null>(null);
const isWebview = computed(() => !!(props.webview?.html || props.webview?.url));

// 面板数据本地镜像：props.data 是开窗快照；panel_data 流式增量（同面板）浅合并进来，
// WebviewPanel watch(data) 自动 postInit 推给 iframe（对齐 PanelApp 合并范式；纯接收，不发信）
const mergedData = ref<Record<string, unknown>>({ ...props.data });
let unlisten: (() => void) | null = null;

function onEvent(e: BrainEvent) {
  if (e.kind !== "panel_data") return;
  if (props.panel !== (e.payload?.panel ?? "")) return;
  mergedData.value = { ...mergedData.value, ...(e.payload?.data ?? {}) };
}

/** 面板内动作（素材「查看」/看板卡「详情」等）：与 HomePlugins/PanelWindow 同一条 panel_action 直调路径。
 *  结果 panel 事件仍经 decideSurface 裁决回落（探窗内换成新面板内容），不新辟弹出通道。
 *  surface 用 panel:<插件>（面板工作台惯例）：动作回执不并进主对话流。 */
function onAction(a: { method: string; params: Record<string, unknown> }) {
  void panelAction(a.method, a.params, undefined, `panel:${props.provider}`).catch(() => {});
}

function prefersReducedMotion(): boolean {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches ?? false;
}
/** 无来源时的生长起点：右下角小块，不抢中心。 */
function fromRect(to: DOMRect): DOMRect {
  return new DOMRect(to.right - 320, to.bottom - 220, 320, 220);
}
function growIn() {
  const el = rootEl.value;
  if (!el || prefersReducedMotion()) return;
  const to = el.getBoundingClientRect();
  const from = fromRect(to);
  el.animate(
    [
      { clipPath: `inset(${from.top - to.top}px ${to.right - from.right}px ${to.bottom - from.bottom}px ${from.left - to.left}px)`, opacity: 0.45, transform: "scale(0.985)" },
      { clipPath: "inset(0px)", opacity: 1, transform: "scale(1)" },
    ],
    { duration: 240, easing: "cubic-bezier(0.22, 0.61, 0.36, 1)", fill: "forwards" },
  );
}
async function collapseOut(): Promise<void> {
  const el = rootEl.value;
  if (!el || prefersReducedMotion()) return;
  const from = el.getBoundingClientRect();
  const to = fromRect(from);
  const anim = el.animate(
    [
      { clipPath: "inset(0px)", opacity: 1, transform: "scale(1)" },
      { clipPath: `inset(${to.top - from.top}px ${from.right - to.right}px ${from.bottom - to.bottom}px ${to.left - from.left}px)`, opacity: 0.45, transform: "scale(0.985)" },
    ],
    { duration: 200, easing: "cubic-bezier(0.4, 0, 0.2, 1)", fill: "forwards" },
  );
  await anim.finished.catch(() => {});
}
async function close() {
  await collapseOut();
  emit("close");
}
function onKeydown(e: KeyboardEvent) {
  if (e.key === "Escape") void close();
}

onMounted(async () => {
  window.addEventListener("keydown", onKeydown);
  try {
    unlisten = await onBrainEvent(onEvent);
  } catch { /* 非 Tauri 环境（单测/纯网页）：无订阅也只是退回快照态 */ }
  void growIn();
});
onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKeydown);
  unlisten?.();
});
</script>

<template>
  <div ref="rootEl" class="peek-surface" @click.self="close">
    <section class="peek-card">
      <header class="peek-head">
        <span class="peek-tag">{{ provider }}</span>
        <strong class="peek-title">{{ title }}</strong>
        <span class="peek-spacer" />
        <button type="button" class="peek-close" title="展开到工作面" aria-label="展开到工作面" @click="emit('expand')"><YbIcon name="expand" :size="14" /></button>
        <button type="button" class="peek-close" aria-label="收起" @click="close"><YbIcon name="x" :size="14" /></button>
      </header>
      <div class="peek-body">
        <WebviewPanel v-if="isWebview" :panel="panel" :html="webview!.html" :url="webview!.url" :v="webview!.v" :data="mergedData" />
        <SchemaPanel v-else :panel="panel" :schema="schema" :data="mergedData" @action="onAction" />
      </div>
    </section>
  </div>
</template>

<style scoped>
.peek-surface {
  position: fixed;
  right: 22px;
  bottom: 58px; /* 36px 地平线 + 22px 边距：不压仪器条 */
  z-index: var(--yb-z-peek);
  width: min(460px, calc(100vw - 44px));
  height: min(420px, calc(100vh - 44px));
  display: flex;
  align-items: stretch;
  box-sizing: border-box;
  will-change: clip-path, opacity, transform;
}
.peek-card {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--yb-card-border);
  border-radius: var(--yb-card-radius);
  background: var(--yb-card-bg);
  box-shadow: var(--yb-shadow-3);
  overflow: hidden;
}
.peek-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-bottom: 1px solid var(--yb-card-border);
  background: var(--yb-card-bg);
}
.peek-tag {
  padding: 1px 7px;
  border-radius: 999px;
  background: var(--yb-accent-soft);
  color: var(--yb-accent);
  font-size: var(--yb-fs-xs);
  font-weight: var(--yb-fw-medium);
  white-space: nowrap;
}
.peek-title {
  overflow: hidden;
  color: var(--yb-text);
  font-size: var(--yb-fs-sm);
  font-weight: var(--yb-fw-medium);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.peek-spacer {
  flex: 1;
}
.peek-close {
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  border: none;
  border-radius: var(--yb-radius-xs);
  background: transparent;
  color: var(--yb-text-faint);
  cursor: pointer;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.peek-close:hover {
  background: var(--yb-btn-neutral);
  color: var(--yb-text);
}
.peek-body {
  flex: 1;
  min-height: 0;
  overflow: auto;
}
</style>
