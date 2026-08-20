<script setup lang="ts">
import { computed, inject, ref, watch } from "vue";
import { HOME_CHAT_SESSION } from "../lib/home-chat-session";
import { talkBeats, talkTurns } from "../lib/work-thread";

const chat = inject(HOME_CHAT_SESSION);
if (!chat) throw new Error("HomeChatTalk needs a chat session");
const { bubbles, state, greeting, showTyping, thinkNote, streamingIdx } = chat;

const page = ref(0);

const turns = computed(() => talkTurns(bubbles.value, 8));
const last = computed(() => {
  const idxs = turns.value;
  const tail = idxs[idxs.length - 1];
  if (tail === undefined) return { ai: null as number | null, user: null as number | null };
  const lastBubble = bubbles.value[tail];
  if (lastBubble.role === "ai") {
    const prev = [...idxs].reverse().find((i) => bubbles.value[i].role === "user" && i < tail);
    return { ai: tail, user: prev ?? null };
  }
  return { ai: null as number | null, user: tail };
});

const live = computed(() => {
  const i = last.value.ai;
  return i !== null && streamingIdx.value === i;
});

const beats = computed(() => {
  if (last.value.ai === null) return [] as string[];
  return talkBeats(bubbles.value[last.value.ai]?.text ?? "");
});

watch(
  () => [last.value.ai, last.value.user, bubbles.value[last.value.ai ?? -1]?.text],
  () => { page.value = live.value ? Math.max(0, beats.value.length - 1) : 0; },
);

const beat = computed(() => beats.value[Math.min(page.value, Math.max(0, beats.value.length - 1))] ?? "");
const more = computed(() => !live.value && page.value < beats.value.length - 1);
const waiting = computed(() => showTyping.value || state.value === "think" || state.value === "work");
const echo = computed(() => {
  const i = last.value.user;
  if (i === null) return "";
  const text = bubbles.value[i]?.text ?? "";
  return text.length > 36 ? `${text.slice(0, 36)}…` : text;
});

function advance() {
  if (more.value) page.value += 1;
}

function onKey(e: KeyboardEvent) {
  if (e.key === " " || e.key === "Enter") {
    if (more.value) {
      e.preventDefault();
      advance();
    }
  }
}
</script>

<template>
  <div class="surface-talk" tabindex="0" @keydown="onKey">
    <p v-if="echo" class="echo">你：{{ echo }}</p>
    <button class="box" type="button" :disabled="!more" @click="advance">
      <span class="who">译宝</span>
      <span v-if="waiting && !beat" class="copy wait">{{ thinkNote || "……" }}</span>
      <span v-else-if="beat" class="copy">{{ beat }}</span>
      <span v-else class="copy">{{ greeting }}</span>
      <span v-if="more" class="next">▼</span>
    </button>
  </div>
</template>

<style scoped>
.surface-talk {
  box-sizing: border-box;
  width: 100%;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  align-items: stretch;
  padding: 4px 0 0;
  outline: none;
}
.echo {
  flex: none;
  align-self: flex-start;
  max-width: 22em;
  margin: 0 0 10px 8px;
  padding: 6px 12px;
  border-radius: 14px 14px 14px 4px;
  background: color-mix(in srgb, var(--yb-widget-bg) 88%, transparent);
  box-shadow: var(--yb-glaze-hi);
  color: var(--yb-paper-ink-dim);
  font-size: 12px;
  line-height: 1.4;
}
.box {
  position: relative;
  flex: none;
  width: 100%;
  min-height: 108px;
  margin: 0;
  padding: 22px 40px 18px 18px;
  border: 1px solid var(--yb-widget-border);
  border-radius: 18px;
  background:
    var(--yb-widget-glaze),
    var(--yb-widget-bg);
  box-shadow: var(--yb-widget-shadow);
  color: var(--yb-paper-ink);
  font: inherit;
  text-align: left;
  cursor: default;
}
.box:not(:disabled) { cursor: pointer; }
.who {
  position: absolute;
  top: -10px;
  left: 14px;
  padding: 2px 10px 3px;
  border-radius: 999px;
  background: var(--yb-accent);
  color: var(--yb-text-on-accent);
  font-size: 10px;
  font-weight: var(--yb-fw-medium);
  letter-spacing: 0.12em;
}
.copy {
  display: block;
  min-height: 3.6em;
  white-space: pre-wrap;
  font-size: 16px;
  line-height: 1.55;
}
.copy.wait {
  color: var(--yb-text-faint);
  font-size: 15px;
}
.next {
  position: absolute;
  right: 14px;
  bottom: 10px;
  color: var(--yb-accent);
  font-size: 11px;
  animation: hop 900ms var(--yb-ease-out) infinite;
}
@keyframes hop {
  0%, 100% { transform: translateY(0); opacity: 0.7; }
  50% { transform: translateY(3px); opacity: 1; }
}
@media (prefers-reduced-motion: reduce) {
  .next { animation: none; }
}
</style>
