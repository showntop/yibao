<script setup lang="ts">
// 家态 Workspace / Mission 卡：当前会话显式绑定工作语境；领域阶段由 Workflow Pack
// 投影提供，组件不再知道视频九段。真正接 WorkflowRun 后保持同一卡面合同。
import { computed } from "vue";
import HomeWidget from "./HomeWidget.vue";
import { useProject } from "../composables/useProject";
import { projectCardFace, projectTouchLabel } from "../lib/home/project-card.ts";
import { durableCancel, durableResume } from "../lib/brain";

const emit = defineEmits<{ chat: [draft: string] }>();
const props = defineProps<{ sessionId?: string }>();
const { current, projects, switchTo, refresh } = useProject(() => props.sessionId);

const face = computed(() => (current.value ? projectCardFace(current.value) : null));
// 其余最近项目（projects 已按 touched_at 倒序），点击=切换到该项目
const others = computed(() =>
  projects.value.filter((p) => p.id !== current.value?.id).slice(0, 2),
);

function onSwitch(id: string) {
  void switchTo(id);
}

async function onDurableAction(kind: "cancel" | "resume", executionId: string) {
  if (!executionId) return;
  if (kind === "cancel") await durableCancel(executionId, props.sessionId);
  else await durableResume(executionId, props.sessionId);
  await refresh().catch(() => { /* background broadcast normally wins */ });
}
</script>

<template>
  <aside class="project-card">
    <HomeWidget id="project" aria-label="项目">
      <header class="head">
        <span class="kicker">工作语境</span>
        <span v-if="projects.length" class="total">{{ face ? "本会话已接入" : `可接入 ${projects.length}` }}</span>
      </header>
      <div v-if="!face" class="empty">
        <strong>这段会话还没有工作语境</strong>
        <span>不会继承其他会话的项目与素材</span>
        <button type="button" @click="emit('chat', '为这个会话创建一个新的工作语境')">新建工作语境</button>
      </div>
      <div v-else class="current">
        <button class="current-main" type="button" @click="emit('chat', `查看项目「${face.name}」`)">
          <span class="scope-line">
          <span class="pack" :data-domain="face.domain">{{ face.packLabel }}</span>
          <span class="artifacts">{{ face.artifactCount }} 个产物</span>
          </span>
          <span class="line">
          <span class="name">{{ face.name }}</span>
          <span v-if="face.pending > 0" class="pending">受阻 {{ face.pending }}</span>
          </span>
          <span v-if="face.missionTitle !== face.name" class="mission">
          目标 · {{ face.missionTitle }}
          </span>
          <span v-if="face.capabilityBlocked" class="cap-gap">
          <span class="cap-gap-line">缺能力 · {{ face.missingStageLabels.join("、") }}</span>
          <span v-if="face.capabilityReason" class="cap-gap-reason">{{ face.capabilityReason }}</span>
          </span>
          <span class="track" aria-hidden="true">
          <i
            v-for="i in face.stages.length"
            :key="i"
            :class="{
              done: face.stageStates[i - 1] === 'completed',
              now: face.stageStates[i - 1] === 'ready' || face.stageStates[i - 1] === 'running',
              blocked: face.stageStates[i - 1] === 'blocked',
              capmiss: face.missingStageLabels.includes(face.stages[i - 1]),
            }"
            :title="`${face.stages[i - 1]} · ${face.stageStates[i - 1]}${face.missingStageLabels.includes(face.stages[i - 1]) ? ' · 缺能力' : ''}`"
          />
          </span>
          <span v-if="face.executionLabel" class="activity" :data-status="face.executionStatus">
          <span class="activity-copy">
            <i aria-hidden="true" />
            {{ face.executionLabel }}
          </span>
          <span v-if="face.executionProgress !== null" class="activity-meter" aria-hidden="true">
            <i :style="{ width: `${face.executionProgress}%` }" />
          </span>
          </span>
          <span class="foot">
          <span class="stage">{{ face.activeLabels.length > 1 ? '并行' : '当前' }} · {{ face.stageLabel }}</span>
          <span v-if="face.nextStep" class="next">{{ face.nextStep }}</span>
          </span>
        </button>
        <span v-if="face.executionCanCancel || face.executionCanResume" class="execution-actions">
          <button
            v-if="face.executionCanResume"
            type="button"
            @click="onDurableAction('resume', face.executionId)"
          >从断点继续</button>
          <button
            v-if="face.executionCanCancel && !face.executionCanResume"
            type="button"
            class="stop"
            @click="onDurableAction('cancel', face.executionId)"
          >安全停止</button>
        </span>
      </div>
      <button
        v-for="p in others"
        :key="p.id"
        class="row"
        type="button"
        :title="face ? '把本会话切换到此工作语境' : '把此工作语境接入本会话'"
        @click="onSwitch(p.id)"
      >
        <time>{{ projectTouchLabel(p.touched_at) }}</time>
        <span class="row-name">{{ p.name }}</span>
        <span class="row-action">{{ face ? "切换" : "接入" }}</span>
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
  display: grid;
  gap: 4px;
  padding: 10px 12px;
  border: 1px dashed var(--yb-widget-border);
  border-radius: calc(var(--yb-widget-radius) - 8px);
  color: var(--yb-text-faint);
}
.empty strong {
  color: var(--yb-text-strong);
  font-size: 12px;
}
.empty span { font-size: 10.5px; line-height: 1.45; }
.empty button {
  justify-self: start;
  min-height: 28px;
  margin-top: 4px;
  padding: 0 10px;
  border: 1px solid rgba(var(--yb-c-sky-rgb), 0.24);
  border-radius: var(--yb-radius-sm);
  background: var(--yb-note-accent);
  color: var(--yb-accent);
  font: inherit;
  font-size: 10.5px;
  cursor: pointer;
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
}
.current:hover { filter: brightness(0.98); }
.current-main {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.scope-line {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.pack {
  padding: 2px 7px;
  border-radius: var(--yb-radius-pill);
  background: var(--yb-note-accent);
  color: var(--yb-accent);
  font-size: 9.5px;
  font-weight: var(--yb-fw-medium);
  letter-spacing: 0.03em;
}
.pack[data-domain="deck"] { background: var(--yb-intent-pending-soft); color: var(--yb-intent-pending-ink); }
.pack[data-domain="code"] { background: var(--yb-c-slate-100); color: var(--yb-paper-ink-dim); }
.pack[data-domain="data"] { background: color-mix(in srgb, var(--yb-intent-ok) 12%, transparent); color: var(--yb-intent-ok); }
.artifacts {
  margin-left: auto;
  color: var(--yb-text-faint);
  font-size: 9.5px;
}
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
.mission {
  overflow: hidden;
  color: var(--yb-paper-ink-dim);
  font-size: 10.5px;
  line-height: 1.4;
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
/* Workflow Pack 可变段数：过去=accent，当前=半透天青，未来=浅灰。 */
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
.track i.blocked {
  background: repeating-linear-gradient(
    90deg,
    var(--yb-intent-pending-ink) 0 3px,
    transparent 3px 5px
  );
  opacity: 0.62;
}
/* 缺能力段：灰色虚纹弱化（与依赖阻塞的琥珀虚纹区分——不是等等就好，是装了 provider 才能跑） */
.track i.capmiss {
  background: repeating-linear-gradient(
    90deg,
    var(--yb-c-slate-300) 0 3px,
    transparent 3px 5px
  );
  opacity: 0.9;
}
/* 能力缺口 banner：琥珀虚边小条，开工前可见 */
.cap-gap {
  display: grid;
  gap: 2px;
  padding: 6px 9px;
  border: 1px dashed rgba(var(--yb-c-amber-rgb), 0.45);
  border-radius: var(--yb-radius-sm);
  background: var(--yb-intent-pending-soft);
}
.cap-gap-line {
  color: var(--yb-intent-pending-ink);
  font-size: 10.5px;
  font-weight: var(--yb-fw-medium);
}
.cap-gap-reason {
  color: var(--yb-paper-ink-dim);
  font-size: 10px;
  line-height: 1.45;
}
.activity {
  display: grid;
  grid-template-columns: minmax(0, auto) minmax(48px, 1fr);
  align-items: center;
  gap: 8px;
  color: var(--yb-accent);
  font-size: 10px;
}
.activity-copy {
  display: flex;
  align-items: center;
  gap: 5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.activity-copy > i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  box-shadow: 0 0 0 3px rgba(var(--yb-c-sky-rgb), 0.10);
}
.activity[data-status="interrupted"],
.activity[data-status="failed"],
.activity[data-status="cancel_requested"] { color: var(--yb-intent-pending-ink); }
.activity-meter {
  height: 3px;
  overflow: hidden;
  border-radius: var(--yb-radius-pill);
  background: rgba(var(--yb-c-sky-rgb), 0.10);
}
.activity-meter > i {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: currentColor;
  transition: width 220ms ease;
}
.execution-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: -2px;
}
.execution-actions button {
  min-height: 26px;
  padding: 0 9px;
  border: 1px solid rgba(var(--yb-c-sky-rgb), 0.20);
  border-radius: var(--yb-radius-pill);
  background: rgba(var(--yb-c-sky-rgb), 0.07);
  color: var(--yb-accent);
  font: inherit;
  font-size: 9.5px;
  cursor: pointer;
}
.execution-actions button.stop {
  border-color: color-mix(in srgb, var(--yb-intent-pending-ink) 24%, transparent);
  background: color-mix(in srgb, var(--yb-intent-pending-soft) 58%, transparent);
  color: var(--yb-intent-pending-ink);
}
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
  flex: 1;
  min-width: 0;
  overflow: hidden;
  font-size: var(--yb-fs-sm);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.row-action {
  flex: none;
  color: var(--yb-accent);
  font-size: 9.5px;
}
.row:hover .row-name { color: var(--yb-accent); }
.empty button:focus-visible,
.current:focus-visible,
.row:focus-visible {
  outline: 2px solid var(--yb-accent);
  outline-offset: 1px;
}
</style>
