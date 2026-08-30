<script setup lang="ts">
// 素材统计卡（wb-prototype）：大数字 + 去看看。计数来自 zimeiti.mat_list 直调。
import { onMounted, onUnmounted, ref } from "vue";
import HomeWidget from "./HomeWidget.vue";
import { panelAction } from "../lib/brain";
import { runPanelAction } from "../lib/home/home-panel-run.ts";

const rows = ref<unknown[]>([]);
let timer: ReturnType<typeof setInterval> | null = null;

async function refresh() {
  try {
    const result = await runPanelAction("zimeiti.mat_list", {}, "panel:zimeiti");
    const data = (result ?? {}) as { rows?: unknown };
    rows.value = Array.isArray(data.rows) ? data.rows : [];
  } catch {
    rows.value = [];
  }
}

function openBoard() {
  // list 直调的 result 自带 panel=zimeiti:board 引用（api.toml），宿主据此弹看板
  void panelAction("zimeiti.list", {}, undefined, "panel:zimeiti").catch(() => {});
}

onMounted(async () => {
  await refresh();
  timer = setInterval(refresh, 60_000);
});

onUnmounted(() => {
  if (timer) clearInterval(timer);
});
</script>

<template>
  <aside class="mat-stat">
    <HomeWidget id="materials" aria-label="素材">
      <button class="card" type="button" title="打开素材看板" @click="openBoard">
        <header class="head">
          <span class="kicker">素材</span>
        </header>
        <p class="big">{{ rows.length }}</p>
        <p class="unit">张卡片</p>
        <span class="go">去看看 →</span>
      </button>
    </HomeWidget>
  </aside>
</template>

<style scoped>
.mat-stat { display: contents; }
.card {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  width: 100%;
  border: 0;
  background: none;
  padding: 0;
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.kicker {
  color: var(--yb-paper-ink-dim);
  font-size: var(--yb-fs-xs);
  font-weight: var(--yb-fw-medium);
  letter-spacing: 0.04em;
}
.big {
  margin: 2px 0 0;
  color: var(--yb-text-strong);
  font-family: var(--yb-mono);
  font-size: 30px;
  line-height: 1.1;
  font-variant-numeric: tabular-nums;
}
.unit {
  margin: 0;
  color: var(--yb-text-faint);
  font-size: var(--yb-fs-xs);
}
.go {
  margin-top: 4px;
  color: var(--yb-accent);
  font-size: var(--yb-fs-xs);
}
.card:hover .go {
  text-decoration: underline;
}
</style>
