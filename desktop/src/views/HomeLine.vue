<script setup lang="ts">
import { onMounted, ref } from "vue";
import HomeWidget from "./HomeWidget.vue";
import { panelAction } from "../lib/brain";
import { lineFace, readLineCache, writeLineCache, type LineFace } from "../lib/home/home-line-face.ts";
import { runPanelAction } from "../lib/home/home-panel-run.ts";
import { setDeskOrigin } from "../lib/home/home-desk-presence.ts";

const face = ref<LineFace | null>(null);
const busy = ref(false);

async function pull() {
  if (busy.value) return;
  busy.value = true;
  try {
    const data = await runPanelAction("fun.quote", { count: 1 }, "panel:fun");
    const next = lineFace(data);
    if (next) {
      face.value = next;
      writeLineCache(next, window.localStorage);
    }
  } finally {
    busy.value = false;
  }
}

function openFun(event: MouseEvent) {
  setDeskOrigin(event.currentTarget as Element);
  void panelAction("fun.open", { tab: "quote" }, undefined, "panel:fun").catch(() => {});
}

onMounted(() => {
  face.value = readLineCache(window.localStorage);
  void pull();
});
</script>

<template>
  <aside class="line">
    <HomeWidget id="line" aria-label="一句">
      <article v-if="face" class="verse">
        <p @click="openFun">{{ face.text }}</p>
        <footer>
          <cite v-if="face.from" :title="face.from">{{ face.from }}</cite>
          <button type="button" :disabled="busy" @click="pull">{{ busy ? "在换…" : "换一句" }}</button>
        </footer>
      </article>
      <p v-else class="rest">
        <button type="button" :disabled="busy" @click="pull">{{ busy ? "在取…" : "这会儿没接到一句" }}</button>
      </p>
    </HomeWidget>
  </aside>
</template>

<style scoped>
.line { display: contents; }

.verse {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
  margin: 12px 12px 10px;
}

.verse p {
  margin: 0;
  color: var(--yb-paper-ink);
  font-size: 13px;
  line-height: 1.55;
  cursor: pointer;
}

.verse footer {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.verse cite {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--yb-paper-ink-dim);
  font-size: 10px;
  font-style: normal;
}

.verse button,
.rest button {
  flex: none;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--yb-accent-deep);
  font: inherit;
  font-size: 11px;
  white-space: nowrap;
  cursor: pointer;
}

.verse button:disabled,
.rest button:disabled { color: var(--yb-text-faint); cursor: default; }

.rest {
  margin: 10px 12px 12px;
}

.rest button {
  color: var(--yb-paper-ink-dim);
}
</style>
