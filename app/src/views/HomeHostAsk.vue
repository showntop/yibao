<script setup lang="ts">
import { ref } from "vue";
import InputBar from "../components/common/InputBar.vue";

defineProps<{
  busy?: boolean;
  listening?: boolean;
  notes?: { role: string; text: string }[];
}>();
const emit = defineEmits<{
  submit: [text: string];
  close: [];
}>();

const draft = ref<string | undefined>(undefined);

function onSubmit(text: string) {
  emit("submit", text);
  draft.value = "";
}
</script>

<template>
  <aside class="host-ask" role="dialog" aria-label="跟译宝说">
    <header class="bar">
      <span class="ask">跟译宝说</span>
      <span class="hint">这块活仍在工位上</span>
      <button class="x" type="button" title="收起" @click="$emit('close')">×</button>
    </header>
    <div v-if="notes?.length" class="notes">
      <p v-for="(n, i) in notes" :key="i" class="note" :data-role="n.role">{{ n.text }}</p>
    </div>
    <InputBar
      class="bar-input"
      :busy="busy"
      :listening="listening"
      :draft="draft"
      @submit="onSubmit"
    />
  </aside>
</template>

<style scoped>
.host-ask {
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: min(420px, 100%);
  padding: 10px 12px 12px;
  border: 1px solid var(--yb-widget-border);
  border-radius: var(--yb-widget-radius);
  background:
    var(--yb-widget-glaze),
    var(--yb-widget-bg);
  box-shadow: var(--yb-shadow-3);
  color: var(--yb-paper-ink);
}
.bar {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}
.ask {
  color: var(--yb-paper-ink);
  font-size: 12px;
  font-weight: var(--yb-fw-medium);
}
.hint {
  flex: 1;
  min-width: 0;
  color: var(--yb-paper-ink-dim);
  font-size: 11px;
}
.x {
  margin-left: auto;
  padding: 0 6px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--yb-text-faint);
  font: inherit;
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
}
.x:hover { color: var(--yb-paper-ink); }
.notes {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 140px;
  overflow: auto;
}
.note {
  margin: 0;
  color: var(--yb-paper-ink);
  font-size: 12px;
  line-height: 1.4;
}
.note[data-role="user"] { color: var(--yb-paper-ink-dim); }
.bar-input { width: 100%; }
</style>
