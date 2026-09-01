<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import { deskWho, type DeskKind } from "../lib/home/home-desk-presence.ts";

const props = defineProps<{
  plugin: string;
  title: string;
  workspaceTitle?: string;
  missionTitle?: string;
  objectTitle?: string;
  busy?: boolean;
  focused?: boolean;
  lendEar?: boolean;
  kind?: DeskKind;
}>();
const emit = defineEmits<{
  close: [];
  shrink: [];
  focus: [];
  ask: [];
  body: [el: HTMLElement | null];
}>();

const bodyEl = ref<HTMLElement | null>(null);
watch(bodyEl, (el) => emit("body", el), { immediate: true });
onUnmounted(() => emit("body", null));

const face = computed<DeskKind>(() => props.kind ?? (props.lendEar ? "worker" : "host"));
const label = computed(() => deskWho({
  plugin: props.plugin,
  title: props.title,
  objectTitle: props.objectTitle,
}));
</script>

<template>
  <section class="desk-work" :aria-label="`工作台：${objectTitle || title}`">
    <header class="bar">
      <div class="scope">
        <span class="scope-line">
          <span class="scope-kicker">工作语境</span>
          <strong>{{ workspaceTitle || "未绑定" }}</strong>
          <span class="slash" aria-hidden="true">/</span>
          <span class="object">{{ objectTitle || title }}</span>
        </span>
        <span class="provenance">
          <template v-if="face === 'worker'">译宝请来 · </template>{{ label }}
          <template v-if="missionTitle && missionTitle !== '新对话'"> · {{ missionTitle }}</template>
        </span>
      </div>
      <span class="spacer" />
      <span v-if="face === 'worker'" class="live" :data-busy="busy || undefined">{{ busy ? "正在干" : "在场" }}</span>
      <button v-if="lendEar" class="act" type="button" title="跟译宝说" @click="$emit('ask')">问译宝</button>
      <button class="act" type="button" :title="focused ? '退出专注' : '进入专注'" @click="$emit('focus')">
        {{ focused ? "退出专注" : "专注" }}
      </button>
      <button class="act" type="button" title="暂放到活动区，不中断当前工作" @click="$emit('shrink')">暂放</button>
      <button class="act" type="button" title="收起工位" @click="$emit('close')">收起</button>
    </header>
    <div ref="bodyEl" id="yb-desk-work-body" class="body" />
  </section>
</template>

<style scoped>
.desk-work {
  box-sizing: border-box;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex: 1;
  flex-direction: column;
  overflow: hidden;
  color: var(--yb-paper-ink);
  background: var(--yb-content-bg);
}
.bar {
  flex: none;
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  min-height: 52px;
  padding: 7px 12px;
  border-bottom: 1px solid var(--yb-border-base);
  background: color-mix(in srgb, var(--yb-widget-bg) 88%, transparent);
}
.scope {
  display: grid;
  gap: 2px;
  min-width: 0;
}
.scope-line {
  display: flex;
  align-items: baseline;
  gap: 7px;
  min-width: 0;
  font-size: 12px;
}
.scope-kicker {
  flex: none;
  color: var(--yb-text-faint);
  font-size: 9px;
  font-weight: var(--yb-fw-medium);
  letter-spacing: 0.08em;
}
.scope-line strong {
  overflow: hidden;
  max-width: 190px;
  color: var(--yb-text-strong);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.slash { color: var(--yb-border-strong); }
.object {
  min-width: 0;
  overflow: hidden;
  color: var(--yb-paper-ink);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.provenance {
  overflow: hidden;
  color: var(--yb-text-faint);
  font-size: 9.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.spacer { flex: 1; min-width: 8px; }
.live {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--yb-text-faint);
  font-size: 10px;
  letter-spacing: 0.02em;
}
.live::before {
  content: "";
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--yb-success);
}
.live[data-busy]::before {
  background: var(--yb-accent);
  box-shadow: 0 0 0 4px rgba(var(--yb-c-sky-rgb), 0.12);
}
.act {
  flex: none;
  height: 24px;
  padding: 0 8px;
  border: 1px solid var(--yb-card-border);
  border-radius: var(--yb-radius-sm);
  background: var(--yb-card-bg);
  color: var(--yb-text-dim);
  font: inherit;
  font-size: 11px;
  cursor: pointer;
}
.act:hover {
  border-color: rgba(var(--yb-c-sky-rgb), 0.28);
  color: var(--yb-accent);
}
.act:focus-visible {
  outline: 2px solid var(--yb-accent);
  outline-offset: 2px;
}
.body {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.body :deep(.panel-grow),
.body :deep(.content) {
  flex: 1;
  min-height: 0;
  height: 100%;
}
@media (max-width: 900px) {
  .provenance { display: none; }
  .scope-line strong { max-width: 120px; }
  .live { display: none; }
  .act { padding: 0 6px; }
}
</style>
