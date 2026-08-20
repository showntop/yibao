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
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 28px 32px 20px;
  border-radius: 0 var(--yb-widget-radius) var(--yb-widget-radius) 0;
  background:
    var(--yb-widget-glaze, linear-gradient(158deg, #fff 8%, var(--yb-surface-1) 100%)),
    var(--yb-widget-bg);
  border: 1px solid var(--yb-widget-border);
  box-shadow: var(--yb-widget-shadow);
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
  margin: 6px 0 14px;
  font-size: 20px;
  font-weight: var(--yb-fw-medium);
  letter-spacing: -0.03em;
  line-height: 1.3;
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
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-top: 8px;
  padding-top: 12px;
  border-top: 1px solid var(--yb-line);
  color: var(--yb-text-faint);
  font-size: 11px;
}
.foot-nav {
  display: flex;
  align-items: center;
  gap: 8px;
}
.foot-nav button {
  height: auto;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--yb-text-dim);
  font: inherit;
  cursor: pointer;
}
.foot-nav button:disabled {
  opacity: 0.35;
  cursor: default;
}
.text-btn {
  color: var(--yb-accent-deep);
}

@media (max-width: 720px) {
  .stamps { display: none; }
}
</style>
