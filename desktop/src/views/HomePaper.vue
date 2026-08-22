<script setup lang="ts">
defineProps<{
  empty: boolean;
  duty: boolean;
  title: string;
  pageLabel: string;
  canPrev: boolean;
  canNext: boolean;
  peekOpen: boolean;
  greeting: string;
}>();

const emit = defineEmits<{
  prev: [];
  next: [];
  togglePeek: [];
}>();
</script>

<template>
  <article class="sheet" :class="{ empty, duty, write: !empty && !duty }">
    <div id="home-paper-duty" class="paper-duty" />
    <template v-if="empty">
      <div class="empty-hint">
        <p class="eh-line">桌上是空的。</p>
        <p class="eh-sub">{{ greeting }}。说的下一句写在这一页。</p>
        <div class="chips">
          <slot name="chips" />
        </div>
      </div>
      <div class="empty-peek">
        <button type="button" class="text-btn" @click="emit('togglePeek')">
          {{ peekOpen ? "收起本次" : "本次" }}
        </button>
      </div>
      <slot />
    </template>
    <template v-else>
      <header class="sheet-head">
        <p class="kicker">{{ duty ? "此刻" : "你刚才说" }}</p>
        <div class="title-row">
          <h1 class="title">{{ duty ? "桌上有事" : title }}</h1>
          <div v-if="!duty" class="title-actions">
            <slot name="title-actions" />
          </div>
        </div>
        <aside v-if="$slots.stamps" class="stamps">
          <slot name="stamps" />
        </aside>
      </header>
      <div class="body">
        <slot />
      </div>
      <footer class="foot">
        <span>{{ pageLabel }}</span>
        <div class="foot-nav">
          <slot name="foot-actions" />
          <button type="button" :disabled="!canPrev" @click="emit('prev')">上一页</button>
          <button type="button" :disabled="!canNext" @click="emit('next')">下一页</button>
          <button type="button" class="text-btn" @click="emit('togglePeek')">
            {{ peekOpen ? "收起本次" : "本次" }}
          </button>
        </div>
      </footer>
    </template>
  </article>
</template>

<style scoped>
.sheet {
  position: relative;
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 28px 32px 20px 28px;
  border-radius: 0 var(--yb-widget-radius) var(--yb-widget-radius) 0;
  background:
    var(--yb-widget-glaze, linear-gradient(158deg, #fff 8%, var(--yb-surface-1) 100%)),
    var(--yb-widget-bg);
  border: 1px solid var(--yb-widget-border);
  border-left-color: color-mix(in srgb, var(--yb-widget-border) 55%, transparent);
  box-shadow: none;
}
.sheet::before {
  content: "";
  position: absolute;
  top: 18px;
  bottom: 18px;
  left: 12px;
  width: 1px;
  pointer-events: none;
  background: color-mix(in srgb, var(--yb-widget-border) 80%, transparent);
}
.sheet.write {
  position: relative;
}
.sheet.empty {
  justify-content: center;
}
.paper-duty:empty {
  display: none;
}
.paper-duty:not(:empty) {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-bottom: 16px;
}
.sheet:has(.paper-duty:not(:empty)) .empty-hint {
  display: none;
}
.empty-peek {
  display: flex;
  justify-content: flex-end;
  margin-top: auto;
  padding-top: 12px;
}
.kicker {
  margin: 0;
  color: var(--yb-text-faint);
  font-size: 11px;
  letter-spacing: 0.08em;
}
.title-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}
.title {
  flex: 0 1 auto;
  min-width: 0;
  max-width: 22ch;
  margin: 4px 0 12px;
  font-size: 22px;
  font-weight: var(--yb-fw-medium);
  letter-spacing: -0.036em;
  line-height: 1.28;
  color: var(--yb-text-strong);
  text-wrap: pretty;
}
.title-actions {
  flex: none;
  margin-top: 8px;
  opacity: 0;
  transition: opacity var(--yb-dur-fast) var(--yb-ease-out);
}
.sheet-head:hover .title-actions,
.sheet-head:focus-within .title-actions {
  opacity: 1;
}
.empty-hint {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  text-align: center;
  min-height: 280px;
}
.eh-line {
  margin: 0;
  font-size: 16px;
  color: var(--yb-text-strong);
}
.eh-sub {
  margin: 0 auto 8px;
  max-width: 280px;
  color: var(--yb-text-dim);
  font-size: 12.5px;
  line-height: 1.55;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: var(--yb-space-2);
}
.body {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  font-size: 14px;
  line-height: 1.75;
}
.stamps {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 0 0 14px;
}
.stamps :deep(span) {
  padding: 3px 8px;
  border-radius: 6px;
  background: var(--yb-note-mute);
  color: var(--yb-text-dim);
  font-size: 10.5px;
}
.foot {
  flex: none;
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: 8px 12px;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid color-mix(in srgb, var(--yb-border-base) 80%, transparent);
  color: var(--yb-text-dim);
  font-size: 11.5px;
}
.foot-nav {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  align-items: center;
  gap: 4px 8px;
}
.foot-nav button {
  height: auto;
  padding: 2px 6px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--yb-text-dim);
  font: inherit;
  white-space: nowrap;
  cursor: pointer;
  transition: color 140ms var(--yb-ease-out), background 140ms var(--yb-ease-out);
}
.foot-nav button:hover:not(:disabled) {
  background: var(--yb-accent-soft);
  color: var(--yb-accent-deep);
}
.foot-nav button:disabled {
  opacity: 0.55;
  cursor: default;
}
.text-btn {
  color: var(--yb-accent-deep);
}

@media (max-width: 720px) {
  .stamps { display: none; }
}
</style>
