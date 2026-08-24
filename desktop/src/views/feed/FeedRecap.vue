<script setup lang="ts">
// 主屏「回顾」视图（自包含）：晨间反刍 + 每日回顾（按天卡片：app 时长 + 深度专注段 + 洞察/事件 + 状态徽章）。
// 共享样式在 assets/home-feed.css（.recap-*/.rd-*）。
import { onMounted, onUnmounted, ref } from "vue";
import { getDistillTimelineOnce, onRecapOpen, type DistillDay } from "../../lib/brain";
import { fmtHHMMFromSec, fmtHours } from "../../lib/time";
import YbIcon from "../../components/common/YbIcon.vue";

const emit = defineEmits<{ "open-recap": [day: string] }>();

const recapDays = ref<DistillDay[]>([]);
const recapLoaded = ref(false);
const recapFocusDay = ref<string | null>(null);

async function loadRecap() {
  recapDays.value = await getDistillTimelineOnce(14);
  recapLoaded.value = true;
}

/** 深度专注段："09:30–11:00 · 14:00–15:30"（最多 3 段）。 */
function activeRangesLabel(stats: DistillDay["stats"]): string {
  const rs = stats.active_ranges ?? [];
  if (!rs.length) return "";
  const f = (t: number) => fmtHHMMFromSec(t);
  return rs.slice(0, 3).map((r) => `${f(r[0])}–${f(r[1])}`).join(" · ");
}

const STATUS_LABELS: Record<string, string> = {
  ok: "已提炼",
  failed: "提炼失败",
  no_data: "当日无数据",
  pending: "未提炼",
};
function statusLabel(s: string): string {
  return STATUS_LABELS[s] ?? s;
}
function recapInsights(d: DistillDay) {
  return d.items.filter((i) => i.kind === "insight");
}
function recapEvents(d: DistillDay) {
  return d.items.filter((i) => i.kind === "event");
}

let unRecapOpen: (() => void) | null = null;
onMounted(async () => {
  // deep-link：pet 窗气泡点击 → 切回顾 mode + 跳指定天
  unRecapOpen = await onRecapOpen((day) => {
    emit("open-recap", day);
    recapFocusDay.value = day;
    if (!recapLoaded.value) void loadRecap();
  });
});
onUnmounted(() => {
  unRecapOpen?.();
});
</script>

<template>
  <!-- 回顾视图：按天卡片（app 时长 + 深度专注段 + 洞察/事件 + 状态徽章） -->
  <section class="recap-list">
    <div
      v-for="d in recapDays"
      :key="d.day"
      class="recap-day"
      :class="{ focus: d.day === recapFocusDay }"
    >
      <div class="rd-head">
        <strong class="rd-date yb-num">{{ d.day }}</strong>
        <span class="rd-status" :class="`st-${d.status}`">{{ statusLabel(d.status) }}</span>
      </div>
      <div v-if="d.status === 'ok'" class="rd-body">
        <p
          v-if="Object.keys(d.stats.app_seconds ?? {}).length"
          class="rd-stats yb-num"
        >
          {{
            Object.entries(d.stats.app_seconds ?? {})
              .sort((a, b) => b[1] - a[1])
              .slice(0, 4)
              .map(([k, v]) => `${k} ${fmtHours(v)}`)
              .join(" · ")
          }}
        </p>
        <p v-if="activeRangesLabel(d.stats)" class="rd-blocks">深度专注 {{ activeRangesLabel(d.stats) }}</p>
        <ul class="rd-items">
          <li v-for="i in recapInsights(d)" :key="i.id" class="rd-item insight"><YbIcon name="sparkle" :size="13" /> {{ i.text }}</li>
          <li v-for="i in recapEvents(d)" :key="i.id" class="rd-item event"><YbIcon name="pin" :size="13" /> {{ i.text }}</li>
        </ul>
        <p
          v-if="!recapInsights(d).length && !recapEvents(d).length"
          class="rd-empty"
        >这天没有洞察</p>
      </div>
      <p v-else class="rd-empty">{{ statusLabel(d.status) }}</p>
    </div>
    <p v-if="recapLoaded && !recapDays.length" class="rd-empty">暂时没有回顾</p>
  </section>
</template>
