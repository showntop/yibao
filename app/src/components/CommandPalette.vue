<script setup lang="ts">
/* CommandPalette — ⌘K 全局命令面板（Raycast / Linear / Arc 风格）。
 *
 * AI 原生 OS 的核心动作：找东西用「搜/说」而不是「点 tab」。
 * 输入即过滤（页面/命令/插件），↑↓ 选择，Enter 执行，Esc 关闭。
 * 页面切换 emit 给父组件（Home.vue 管 tab）；命令就地执行（调 brain.ts）。
 */
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import YbIcon from "./YbIcon.vue";
import { clearBrainData, openDataDir, restartBrain } from "../lib/brain";

export type PaletteTab = "home" | "plugins" | "data" | "settings";

const props = defineProps<{ open: boolean }>();
const emit = defineEmits<{ close: []; navigate: [tab: PaletteTab]; collapse: [] }>();

// 与 YbIcon 的 IconName 保持一致（模板 :name 需要精确联合）
type PaletteIcon =
  | "clock" | "chat" | "gear" | "spinner" | "check" | "x" | "stop"
  | "lock" | "pin" | "doc" | "alert" | "inbox" | "sparkle" | "plug"
  | "dumpling" | "mic" | "wave" | "thumb-up" | "thumb-down" | "search";

interface Cmd {
  id: string;
  label: string;
  hint?: string;
  icon: PaletteIcon;
  group: string;
  run: () => void;
}

const PAGES: { id: PaletteTab; label: string; icon: PaletteIcon; shortcut: string }[] = [
  { id: "home", label: "主屏", icon: "inbox", shortcut: "⌘1" },
  { id: "plugins", label: "插件", icon: "plug", shortcut: "⌘2" },
  { id: "data", label: "数据", icon: "doc", shortcut: "⌘3" },
  { id: "settings", label: "设置", icon: "gear", shortcut: "⌘," },
];

const cmds = computed<Cmd[]>(() => [
  ...PAGES.map((p) => ({
    id: `page:${p.id}`,
    label: p.label,
    hint: p.shortcut,
    icon: p.icon,
    group: "页面",
    run: () => emit("navigate", p.id),
  })),
  {
    id: "restart", label: "重启大脑", icon: "spinner", group: "命令",
    run: () => void restartBrain().catch(() => {}),
  },
  {
    id: "clear-history", label: "清空对话历史", icon: "x", group: "命令",
    run: () => void clearBrainData("history").catch(() => {}),
  },
  {
    id: "clear-memory", label: "清空长期记忆", icon: "x", group: "命令",
    run: () => void clearBrainData("memory").catch(() => {}),
  },
  {
    id: "open-data", label: "打开数据目录", icon: "doc", group: "命令",
    run: () => void openDataDir().catch(() => {}),
  },
  {
    id: "collapse", label: "收起为小窗", icon: "dumpling", group: "命令",
    run: () => emit("collapse"),
  },
]);

const query = ref("");
const activeId = ref<string | null>(null);
const inputEl = ref<HTMLInputElement | null>(null);

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase();
  if (!q) return cmds.value;
  return cmds.value.filter((c) => c.label.toLowerCase().includes(q));
});

const groups = computed(() => {
  const map = new Map<string, Cmd[]>();
  for (const c of filtered.value) {
    const arr = map.get(c.group) ?? [];
    arr.push(c);
    map.set(c.group, arr);
  }
  return [...map.entries()];
});

// 打开时重置搜索 + 聚焦输入
watch(
  () => props.open,
  (o) => {
    if (!o) return;
    query.value = "";
    activeId.value = filtered.value[0]?.id ?? null;
    void nextTick(() => inputEl.value?.focus());
  },
);

function move(delta: number) {
  const l = filtered.value;
  if (!l.length) return;
  const idx = l.findIndex((c) => c.id === activeId.value);
  const next = Math.min(Math.max(idx + delta, 0), l.length - 1);
  activeId.value = l[next].id;
}

function runCmd(c: Cmd) {
  emit("close");
  c.run();
}

function onKeydown(e: KeyboardEvent) {
  if (!props.open) return;
  if (e.key === "Escape") {
    emit("close");
    return;
  }
  if (e.key === "ArrowDown") {
    e.preventDefault();
    move(1);
    return;
  }
  if (e.key === "ArrowUp") {
    e.preventDefault();
    move(-1);
    return;
  }
  if (e.key === "Enter") {
    e.preventDefault();
    const c = filtered.value.find((x) => x.id === activeId.value) ?? filtered.value[0];
    if (c) runCmd(c);
  }
}

onMounted(() => window.addEventListener("keydown", onKeydown));
onUnmounted(() => window.removeEventListener("keydown", onKeydown));
</script>

<template>
  <div v-if="open" class="palette-mask" @mousedown.self="emit('close')">
    <div class="palette" role="dialog" aria-modal="true">
      <div class="palette-input">
        <YbIcon name="search" :size="16" />
        <input
          ref="inputEl"
          v-model="query"
          class="palette-field"
          placeholder="搜索页面、命令…"
          @keydown="onKeydown"
        />
        <kbd class="palette-esc">esc</kbd>
      </div>
      <div v-if="filtered.length" class="palette-list">
        <template v-for="[g, items] in groups" :key="g">
          <div class="palette-group">{{ g }}</div>
          <button
            v-for="c in items"
            :key="c.id"
            class="palette-item"
            :class="{ on: activeId === c.id }"
            @mouseenter="activeId = c.id"
            @click="runCmd(c)"
          >
            <YbIcon class="pi-ic" :name="c.icon" :size="15" />
            <span class="pi-label">{{ c.label }}</span>
            <span v-if="c.hint" class="pi-hint">{{ c.hint }}</span>
          </button>
        </template>
      </div>
      <div v-else class="palette-empty">没有匹配的结果</div>
    </div>
  </div>
</template>

<style scoped>
/* 遮罩：极淡 + 整屏，点击空白关闭 */
.palette-mask {
  position: fixed;
  inset: 0;
  z-index: 1000;
  background: rgba(16, 22, 29, 0.28);
  -webkit-backdrop-filter: blur(2px);
  backdrop-filter: blur(2px);
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding-top: 12vh;
  animation: pm-in 0.14s var(--yb-ease-out);
}
@keyframes pm-in {
  from { opacity: 0; }
}
/* 面板：居中浮层，macOS 毛玻璃 + 深阴影 */
.palette {
  width: min(520px, 86vw);
  max-height: 60vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid var(--yb-card-border);
  border-radius: var(--yb-radius-xl);
  background: var(--yb-card-bg);
  box-shadow:
    0 24px 64px -8px rgba(16, 22, 29, 0.35),
    0 4px 16px rgba(16, 22, 29, 0.12);
  animation: p-in 0.16s var(--yb-ease-out);
}
@keyframes p-in {
  from { transform: translateY(-8px) scale(0.98); opacity: 0; }
}
/* 输入行 */
.palette-input {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--yb-card-row-line);
  color: var(--yb-text-dim);
}
.palette-field {
  flex: 1;
  min-width: 0;
  border: none;
  background: transparent;
  outline: none;
  color: var(--yb-text);
  font-size: var(--yb-fs-lg);
  font-family: inherit;
}
.palette-field::placeholder {
  color: var(--yb-text-faint);
}
.palette-esc {
  flex-shrink: 0;
  padding: 1px 6px;
  border: 1px solid var(--yb-border-strong);
  border-radius: var(--yb-radius-xs);
  background: var(--yb-surface-2);
  color: var(--yb-text-faint);
  font-size: 10px;
  font-family: var(--yb-font);
}
/* 结果列表 */
.palette-list {
  overflow-y: auto;
  scrollbar-width: thin;
  padding: 6px;
}
.palette-group {
  padding: 8px 10px 4px;
  font-size: var(--yb-fs-xs);
  font-weight: var(--yb-fw-bold);
  color: var(--yb-text-faint);
  letter-spacing: 0.05em;
}
.palette-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 7px 10px;
  border: none;
  border-radius: var(--yb-radius-xs);
  background: transparent;
  color: var(--yb-text);
  font-size: var(--yb-fs-lg);
  font-family: inherit;
  text-align: left;
  cursor: pointer;
}
.palette-item .pi-ic {
  flex-shrink: 0;
  color: var(--yb-text-dim);
}
.palette-item .pi-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.palette-item .pi-hint {
  flex-shrink: 0;
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-faint);
}
.palette-item.on {
  background: var(--yb-accent);
  color: var(--yb-text-on-accent);
}
.palette-item.on .pi-ic,
.palette-item.on .pi-hint {
  color: var(--yb-text-on-accent);
  opacity: 0.85;
}
.palette-empty {
  padding: 28px;
  text-align: center;
  color: var(--yb-text-faint);
  font-size: var(--yb-fs-md);
}
</style>
