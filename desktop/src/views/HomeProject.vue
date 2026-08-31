<script setup lang="ts">
// 家态项目卡（specimen video-flow.html 场景 2）：当前项目名 + S0–S8 九段进度轨 + 下一步 + 待确认数。
// 推导规则全在 lib/home/project-card.ts（V1a 诚实推导，V1b 接阶段模型后替换），这里只渲染。
import { computed } from "vue";
import HomeWidget from "./HomeWidget.vue";
import { useProject } from "../composables/useProject";
import { PROJECT_STAGES, projectCardFace, projectTouchLabel } from "../lib/home/project-card.ts";

const emit = defineEmits<{ chat: [draft: string] }>();
const { current, projects, switchTo } = useProject();

const face = computed(() => (current.value ? projectCardFace(current.value) : null));
// 其余最近项目（projects 已按 touched_at 倒序），点击=切换到该项目
const others = computed(() =>
  projects.value.filter((p) => p.id !== current.value?.id).slice(0, 2),
);

function onSwitch(id: string) {
  void switchTo(id);
}
</script>

<template>
  <aside class="project-card">
    <HomeWidget id="project" aria-label="项目">
      <header class="head">
        <span class="kicker">项目</span>
        <span v-if="projects.length" class="total">全部 {{ projects.length }}</span>
      </header>
      <p v-if="!face" class="empty">还没有在手项目</p>
      <button v-else class="current" type="button" @click="emit('chat', `查看项目「${face.name}」`)">
        <span class="line">
          <span class="name">{{ face.name }}</span>
          <span v-if="face.pending > 0" class="pending">待确认 {{ face.pending }}</span>
        </span>
        <span class="track" aria-hidden="true">
          <i
            v-for="i in PROJECT_STAGES.length"
            :key="i"
            :class="{ done: i - 1 < face.stage, now: i - 1 === face.stage }"
          />
        </span>
        <span class="foot">
          <span class="stage">{{ face.stageLabel }}</span>
          <span v-if="face.nextStep" class="next">下一步 · {{ face.nextStep }}</span>
        </span>
      </button>
      <button
        v-for="p in others"
        :key="p.id"
        class="row"
        type="button"
        title="切换到此项目"
        @click="onSwitch(p.id)"
      >
        <time>{{ projectTouchLabel(p.touched_at) }}</time>
        <span class="row-name">{{ p.name }}</span>
      </button>
    </HomeWidget>
  </aside>
</template>

<style scoped>
.project-card { display: contents; }
/* 卡内距：.yb-widget 本体无 padding，内容自带（同提醒卡） */
.project-card :deep(.yb-widget) {
  min-width: 0;
  max-width: 100%;
  padding: 10px 16px 12px;
}
.head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 4px;
}
.kicker {
  color: var(--yb-paper-ink-dim);
  font-size: var(--yb-fs-xs);
  font-weight: var(--yb-fw-medium);
  letter-spacing: 0.04em;
}
.total {
  color: var(--yb-text-faint);
  font-size: var(--yb-fs-xs);
}
.empty {
  margin: 0;
  color: var(--yb-text-faint);
  font-size: var(--yb-fs-sm);
}
/* 当前项目卡（specimen：tile 内嵌一张描边圆角小卡） */
.current {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
  margin: 0;
  padding: 10px 12px;
  border: 1px solid var(--yb-widget-border);
  border-radius: calc(var(--yb-widget-radius) - 8px);
  background: var(--yb-note-mute);
  box-shadow: var(--yb-press);
  color: var(--yb-paper-ink);
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.current:hover { filter: brightness(0.98); }
.line {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}
.name {
  overflow: hidden;
  color: var(--yb-text-strong);
  font-size: 13.5px;
  font-weight: var(--yb-fw-bold);
  line-height: 1.35;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 待确认 = 琥珀（design §7：待你定/异常用琥珀）；V1a 恒 0 不显示 */
.pending {
  flex: none;
  margin-left: auto;
  padding: 1px 8px;
  border-radius: var(--yb-radius-pill);
  background: var(--yb-intent-pending-soft);
  color: var(--yb-intent-pending-ink);
  font-size: 10px;
}
/* S0–S8 九段进度轨：过去=accent，当前=半透天青，未来=浅灰（specimen 场景 2 配色） */
.track {
  display: flex;
  gap: 3px;
}
.track i {
  flex: 1;
  height: 4px;
  border-radius: 2px;
  background: var(--yb-c-slate-100);
}
.track i.done { background: var(--yb-accent); }
.track i.now { background: rgba(var(--yb-c-sky-rgb), 0.45); }
.foot {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  color: var(--yb-text-faint);
  font-size: 10.5px;
}
.foot .next {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 其余项目行（点击切换） */
.row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  width: 100%;
  margin: 0;
  padding: 6px 2px 0;
  border: 0;
  background: none;
  color: var(--yb-paper-ink);
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.row time {
  flex: none;
  color: var(--yb-text-faint);
  font-family: var(--yb-mono);
  font-size: 10px;
}
.row-name {
  overflow: hidden;
  font-size: var(--yb-fs-sm);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.row:hover .row-name { color: var(--yb-accent); }
.current:focus-visible,
.row:focus-visible {
  outline: 2px solid var(--yb-accent);
  outline-offset: 1px;
}
</style>
