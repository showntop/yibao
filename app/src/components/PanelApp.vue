<script setup lang="ts">
// 面板窗根组件：标题栏（可拖动 + 面板名 + 关闭）/ 内嵌确认条 / 错误细条 / SchemaPanel 撑满。
// 事件与命令复用 app 级 brain-event / invoke（与宠物窗同通道，无新协议）。
import { onMounted, onUnmounted, ref } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import SchemaPanel from "./SchemaPanel.vue";
import { onBrainEvent, panelAction, sendConfirm, type BrainEvent } from "../lib/brain";

// 当前面板：kind="panel" 事件整体替换刷新
const current = ref<{ panel: string; schema: any; data: Record<string, unknown> } | null>(null);
const errorText = ref(""); // 面板内顶部错误细条（不进对话气泡）
const pending = ref<{ id: string; skill: string; desc: string } | null>(null); // 内嵌确认条
let unlisten: (() => void) | null = null;
let unlistenFocus: (() => void) | null = null;

function onEvent(e: BrainEvent) {
  switch (e.kind) {
    case "panel":
      current.value = {
        panel: e.payload?.panel ?? "",
        schema: (e.payload?.schema as any) ?? null,
        data: e.payload?.data ?? {},
      };
      break;
    case "confirmation_needed":
      // 面板 action 触发的 L2+ 确认在面板内解决，不跳宠物窗
      pending.value = {
        id: e.confirmation_id ?? "",
        skill: e.action?.skill_id ?? "",
        desc: e.action?.description ?? "",
      };
      break;
    case "action_result":
      pending.value = null; // 确认流结束（批准路径：执行结果回来了）
      break;
    case "error":
      pending.value = null; // 确认流结束（拒绝路径）或执行失败
      errorText.value = e.text ?? "出错了";
      break;
  }
}

async function decide(approved: boolean) {
  if (!pending.value) return;
  const { id } = pending.value;
  pending.value = null;
  try {
    await sendConfirm(id, approved);
  } catch (err) {
    errorText.value = "确认失败：" + String(err);
  }
}

async function onAction(a: { method: string; params: Record<string, unknown> }) {
  errorText.value = "";
  try {
    await panelAction(a.method, a.params);
  } catch (err) {
    errorText.value = "面板操作失败：" + String(err);
  }
}

function close() {
  void invoke("close_panel_window");
}

async function pullCache() {
  try {
    const cached = await invoke<{ panel: string; schema: any; data: Record<string, unknown> } | null>(
      "get_current_panel"
    );
    if (cached && current.value === null) current.value = cached;
  } catch (err) {
    // 命令缺失（旧壳进程）等问题要看得见，不能静默停在占位页
    errorText.value = "面板数据拉取失败：" + String(err);
  }
}

onMounted(async () => {
  unlisten = await onBrainEvent(onEvent);
  // 首开竞态：panel 事件先于本窗口订阅发出，从 Rust 缓存补拉最近一次面板
  await pullCache();
  // 兜底：窗口再聚焦时若仍是占位页，重拉一次（覆盖旧壳残留窗口等边角）
  unlistenFocus = await getCurrentWindow().onFocusChanged(({ payload: focused }) => {
    if (focused && current.value === null) void pullCache();
  });
});
onUnmounted(() => {
  unlisten?.();
  unlistenFocus?.();
});
</script>

<template>
  <div class="panel-shell">
    <div class="titlebar" data-tauri-drag-region>
      <span class="name">{{ current?.panel ?? "面板" }}</span>
      <button class="x" title="关闭" @click="close">×</button>
    </div>

    <div v-if="pending" class="confirm-bar">
      <span class="c-text">⚠️ {{ pending.skill }}{{ pending.desc ? " · " + pending.desc : "" }}</span>
      <span class="c-btns">
        <button class="deny" @click="decide(false)">拒绝</button>
        <button class="ok" @click="decide(true)">允许</button>
      </span>
    </div>

    <div v-if="errorText" class="error-bar">⚠️ {{ errorText }}</div>

    <div class="content">
      <SchemaPanel
        v-if="current"
        :panel="current.panel"
        :schema="current.schema"
        :data="current.data"
        @action="onAction"
      />
      <div v-else class="placeholder">暂无面板内容</div>
    </div>
  </div>
</template>

<style scoped>
.panel-shell {
  height: 100vh;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  font-family: -apple-system, "PingFang SC", system-ui, sans-serif;
  color: var(--yb-text);
  background: var(--yb-bg);
  -webkit-backdrop-filter: var(--yb-blur);
  backdrop-filter: var(--yb-blur);
  border: 1px solid var(--yb-glass-border);
  border-radius: var(--yb-radius-xl);
  box-shadow: var(--yb-shadow);
}
.titlebar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--yb-space-3) var(--yb-space-4);
  user-select: none;
}
.name {
  font-size: var(--yb-fs-lg);
  font-weight: 600;
}
.x {
  border: none;
  background: transparent;
  font-size: 18px;
  line-height: 1;
  cursor: pointer;
  color: var(--yb-text-dim);
  padding: 2px 8px;
  border-radius: var(--yb-radius-sm);
}
.x:hover {
  background: rgba(0, 0, 0, 0.06);
}
.confirm-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--yb-space-2);
  margin: 0 var(--yb-space-4) var(--yb-space-2);
  padding: var(--yb-space-2) var(--yb-space-3);
  border-radius: var(--yb-radius-md);
  background: rgba(255, 255, 255, 0.96);
  border: 1px solid var(--yb-danger-soft);
  font-size: var(--yb-fs-md);
}
.c-text {
  line-height: 1.4;
}
.c-btns {
  display: flex;
  gap: var(--yb-space-2);
  flex-shrink: 0;
}
.c-btns button {
  padding: 5px 14px;
  border-radius: var(--yb-radius-sm);
  border: none;
  cursor: pointer;
  font-size: var(--yb-fs-md);
  font-weight: 500;
}
.ok {
  background: var(--yb-accent);
  color: #fff;
}
.deny {
  background: rgba(0, 0, 0, 0.06);
  color: var(--yb-text-dim);
}
.error-bar {
  margin: 0 var(--yb-space-4) var(--yb-space-2);
  padding: 6px var(--yb-space-3);
  border-radius: var(--yb-radius-sm);
  background: var(--yb-danger-soft);
  color: var(--yb-danger);
  font-size: var(--yb-fs-md);
}
.content {
  flex: 1;
  min-height: 0;
  margin: 0 var(--yb-space-2) var(--yb-space-2);
  border-radius: var(--yb-radius-md);
  background: rgba(255, 255, 255, 0.6);
}
.placeholder {
  height: 100%;
  display: grid;
  place-items: center;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-lg);
}
</style>
