<script setup lang="ts">
// 器物架统计对（wb-prototype shelf-pair）：素材 + 闪念两张 mini 瓷片并排。
// 素材数 = zimeiti.mat_list 直调；闪念数 = notes widget rows（flashesStatFace）。
import { computed, onMounted, onUnmounted, ref } from "vue";
import { getWidgetsOnce, onWidgets, panelAction, type WidgetPayload } from "../lib/brain";
import { flashesStatFace } from "../lib/home/home-jot-face.ts";
import { runPanelAction } from "../lib/home/home-panel-run.ts";
import { setDeskOrigin } from "../lib/home/home-desk-presence.ts";

const widgets = ref<WidgetPayload[]>([]);
const matCount = ref(0);
let unWidgets: (() => void) | null = null;
let timer: ReturnType<typeof setInterval> | null = null;

const flash = computed(() => flashesStatFace(widgets.value));

async function refreshMat() {
  try {
    // mat_list 直调（HomeBench 调 coding.sessions 同款先例）
    const data = (await runPanelAction("zimeiti.mat_list", {}, "panel:zimeiti")) ?? {};
    matCount.value = Array.isArray((data as { rows?: unknown }).rows) ? (data as { rows: unknown[] }).rows.length : 0;
  } catch {
    matCount.value = 0;
  }
}

function openBoard(event: MouseEvent) {
  setDeskOrigin(event.currentTarget as Element); // 看板 peek 从卡片位置长出
  // list 直调自带 panel=zimeiti:board 引用（api.toml），宿主据此弹看板
  void panelAction("zimeiti.list", {}, undefined, "panel:zimeiti").catch(() => {});
}

function openNotes(event: MouseEvent) {
  setDeskOrigin(event.currentTarget as Element);
  void panelAction(flash.value.open, {}, undefined, "panel:notes").catch(() => {});
}

onMounted(async () => {
  const result = await getWidgetsOnce().catch(() => ({ widgets: [] as WidgetPayload[] }));
  widgets.value = result.widgets ?? [];
  try {
    unWidgets = await onWidgets((payload) => { widgets.value = payload?.widgets ?? []; });
  } catch { /* 闪念盘不在线显示 0 */ }
  // 启动期首刷延迟：panel_action 走 brain 往返（秒级），页面还在加载时响应会撞上
  // WebKit 已取消的 scheme task → ObjC 异常穿过 Rust 直接 abort（真机 4/4 复现）
  timer = setTimeout(() => {
    refreshMat();
    timer = setInterval(refreshMat, 60_000);
  }, 5_000);
});

onUnmounted(() => {
  unWidgets?.();
  if (timer) clearInterval(timer);
});
</script>

<template>
  <div class="shelf-stats">
    <button class="mini" type="button" title="打开素材看板" @click="openBoard">
      <span class="num">{{ matCount }}</span>
      <span class="lbl">素材<br>张卡片</span>
      <span class="go">去看看 →</span>
    </button>
    <button class="mini" type="button" title="打开闪念盘" @click="openNotes">
      <span class="num">{{ flash.count }}</span>
      <span class="lbl">闪念<br>条未处理</span>
      <span class="go">去处理 →</span>
    </button>
  </div>
</template>

<style scoped>
.shelf-stats {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  min-width: 0;
  max-width: 100%;
}
.mini {
  min-width: 0;
  max-width: 100%;
}
/* mini 瓷片：自带瓷皮（HomeWidget 的 placed 判定对组合零件不适用） */
.mini {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 1px;
  min-width: 0;
  padding: 10px 12px;
  border: 1px solid var(--yb-widget-border);
  border-radius: var(--yb-widget-radius);
  background: var(--yb-widget-glaze), var(--yb-widget-bg);
  box-shadow: var(--yb-shadow-2);
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.num {
  color: var(--yb-text-strong);
  font-family: var(--yb-mono);
  font-size: 26px;
  line-height: 1.15;
  font-variant-numeric: tabular-nums;
}
.lbl {
  color: var(--yb-text-faint);
  font-size: 10px;
  line-height: 1.35;
}
.go {
  margin-top: 4px;
  color: var(--yb-accent);
  font-size: var(--yb-fs-xs);
}
.mini:hover .go {
  text-decoration: underline;
}
</style>
