<script setup lang="ts">
// 唤起条：⌘⇧U 划词后光标旁弹出——团子探出 + 动作直达。点按钮广播 invoke-action 后自隐；
// Esc / 失焦自隐。主窗 App.vue 负责真正的动作（解释/翻译走 run，存素材走 panel_action）。
import { onMounted, onUnmounted } from "vue";
import { emit } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import Avatar from "./Avatar.vue";
import YbIcon from "./YbIcon.vue";

const acts = [
  { id: "explain", label: "解释", icon: "chat" },
  { id: "translate", label: "翻译", icon: "doc" },
  { id: "save", label: "存素材", icon: "pin" },
] as const;

async function pick(id: string) {
  await emit("invoke-action", { action: id });
  await getCurrentWindow().hide();
}

function hide() {
  void getCurrentWindow().hide();
}

function onKey(e: KeyboardEvent) {
  if (e.key === "Escape") hide();
}

let unlistenBlur: (() => void) | null = null;
onMounted(async () => {
  window.addEventListener("keydown", onKey);
  unlistenBlur = await getCurrentWindow().listen("tauri://blur", hide);
});
onUnmounted(() => {
  window.removeEventListener("keydown", onKey);
  unlistenBlur?.();
});
</script>

<template>
  <div class="ib">
    <div class="ib-av"><Avatar state="notify" :size="30" /></div>
    <button v-for="a in acts" :key="a.id" class="ib-btn" @click="pick(a.id)">
      <YbIcon :name="a.icon" :size="14" />{{ a.label }}
    </button>
    <button class="ib-x" title="忽略 (Esc)" @click="hide"><YbIcon name="x" :size="13" /></button>
  </div>
</template>

<style scoped>
.ib {
  height: 100vh;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0 10px;
  border-radius: var(--yb-radius-pill);
  background: var(--yb-glass);
  backdrop-filter: blur(18px);
  border: 1px solid var(--yb-border-base);
  box-shadow: var(--yb-shadow-3);
  font-family: var(--yb-font);
}
.ib-av {
  width: 30px;
  height: 30px;
  flex: none;
  margin-right: 2px;
  overflow: hidden;
}
.ib-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: none;
  border-radius: var(--yb-radius-pill);
  padding: 5px 10px;
  background: transparent;
  color: var(--yb-text);
  font-size: var(--yb-fs-md);
  cursor: pointer;
  transition: background var(--yb-dur-fast);
  white-space: nowrap;
}
.ib-btn:hover {
  background: var(--yb-surface-2);
}
.ib-btn:active {
  transform: scale(0.97);
}
.ib-x {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--yb-text-dim);
  cursor: pointer;
}
.ib-x:hover {
  background: var(--yb-surface-2);
  color: var(--yb-text);
}
</style>
