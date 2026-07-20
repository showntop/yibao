<script setup lang="ts">
// 面板窗根组件：标题栏（可拖动 + 面板名 + 关闭）/ 内嵌确认条 / 错误细条 / SchemaPanel 撑满。
// 工作台条（v2 §5）：面板是手、译宝是脑——条上有团子（状态同步）+ 上下文 chip + 输入条，
// 对话走同一大脑；面板内容作为 focus 上报，注入 LLM 上下文（「这个/它」有解）。
import { computed, onMounted, onUnmounted, ref } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import SchemaPanel from "./SchemaPanel.vue";
import WebviewPanel from "./WebviewPanel.vue";
import Avatar from "./Avatar.vue";
import InputBar from "./InputBar.vue";
import {
  onBrainEvent,
  panelAction,
  sendConfirm,
  runInput,
  voiceStart,
  interrupt,
  reportPanelContext,
  type BrainEvent,
  type PanelFocus,
} from "../lib/brain";

// 当前面板：kind="panel" 事件整体替换刷新（webview 非空 → webview 面板，否则 schema 面板）
const current = ref<{
  panel: string;
  title: string;
  schema: any;
  webview: { html?: string } | null;
  data: Record<string, unknown>;
} | null>(null);
const errorText = ref(""); // 面板内顶部错误细条（不进对话气泡）
const pending = ref<{ id: string; skill: string; desc: string } | null>(null); // 内嵌确认条
let unlisten: (() => void) | null = null;
let unlistenFocus: (() => void) | null = null;

// ---- 工作台条状态 ----
type AvatarState = "idle" | "listen" | "think" | "work" | "say";
const state = ref<AvatarState>("idle");
const busy = computed(() => state.value !== "idle");
const focus = ref<PanelFocus | null>(null); // 当前面板焦点（同步给大脑）
const chipText = computed(() => {
  const t = focus.value?.item?.title;
  return t ? `在看：${t}` : "";
});
// 流式回复气泡：浮在工作台条上方，final 后几秒淡出；完整历史留在宠物窗
const replyText = ref("");
const replyVisible = ref(false);
let fadeTimer: ReturnType<typeof setTimeout> | null = null;

function showReply() {
  replyVisible.value = true;
  if (fadeTimer !== null) {
    clearTimeout(fadeTimer);
    fadeTimer = null;
  }
}

function fadeReply(ms: number) {
  if (fadeTimer !== null) clearTimeout(fadeTimer);
  fadeTimer = setTimeout(() => {
    replyVisible.value = false;
    fadeTimer = null;
  }, ms);
}

/** 面板内容 → 焦点：rows 恰好一条 = 选中条目（详情页）；多条/没有 = 只有面板。 */
function computeFocus(cur: typeof current.value): PanelFocus | null {
  if (!cur?.panel) return null;
  const [plugin, panel] = cur.panel.split(":");
  if (!plugin) return null;
  const rows = (cur.data as any)?.rows;
  const r0 = Array.isArray(rows) && rows.length === 1 ? rows[0] : null;
  return {
    plugin,
    panel: panel ?? "",
    item: r0 ? { id: r0.id, title: r0.title, status: r0.status } : null,
  };
}

/** 面板内容统一入口：赋值 + 重算焦点 + 上报大脑。 */
function setCurrent(v: typeof current.value) {
  current.value = v;
  focus.value = computeFocus(v);
  void reportPanelContext(focus.value).catch(() => {});
}

function onEvent(e: BrainEvent) {
  switch (e.kind) {
    case "panel":
      setCurrent({
        panel: e.payload?.panel ?? "",
        title: e.payload?.title ?? e.payload?.panel ?? "",
        schema: (e.payload?.schema as any) ?? null,
        webview: (e.payload?.webview as { html?: string } | null) ?? null,
        data: e.payload?.data ?? {},
      });
      break;
    case "confirmation_needed":
      // 面板 action 触发的 L2+ 确认在面板内解决，不跳宠物窗
      pending.value = {
        id: e.confirmation_id ?? "",
        skill: e.action?.skill_id ?? "",
        desc: e.action?.description ?? "",
      };
      state.value = "idle";
      break;
    case "action_proposed":
      state.value = "work";
      break;
    case "action_result":
      pending.value = null; // 确认流结束（批准路径：执行结果回来了）
      break;
    case "final_reply_chunk":
      replyText.value += e.text ?? "";
      showReply();
      break;
    case "final_reply":
      replyText.value = e.text ?? replyText.value;
      showReply();
      fadeReply(6000);
      if (state.value !== "say") state.value = "idle";
      break;
    case "interrupted":
      if (replyText.value) {
        replyText.value += " ⛔";
        showReply();
        fadeReply(3000);
      }
      state.value = "idle";
      break;
    case "listening":
      state.value = "listen";
      break;
    case "listening_done":
      state.value = "think";
      replyText.value = "";
      replyVisible.value = false;
      break;
    case "speaking":
      state.value = "say";
      break;
    case "speaking_done":
      state.value = "idle";
      break;
    case "error":
      pending.value = null; // 确认流结束（拒绝路径）或执行失败
      errorText.value = e.text ?? "出错了";
      state.value = "idle";
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

// 工作台条交互：提交走同一 runInput（focus 已在大脑上下文里）；mic/长按团子 = 语音
const barRef = ref<HTMLElement | null>(null);

function submit(text: string) {
  errorText.value = "";
  replyText.value = "";
  replyVisible.value = false;
  void runInput(text).catch((err) => {
    errorText.value = "发送失败：" + String(err);
  });
}

function onMic() {
  void voiceStart().catch((err) => {
    errorText.value = "语音失败：" + String(err);
  });
}

function onInterrupt() {
  if (!busy.value) return;
  void interrupt().catch(() => {});
}

function focusInput() {
  barRef.value?.querySelector("input")?.focus();
}

function close() {
  void reportPanelContext(null).catch(() => {});
  void invoke("close_panel_window");
}

async function pullCache() {
  try {
    const cached = await invoke<{
      panel: string;
      title?: string;
      schema: any;
      webview: { html?: string } | null;
      data: Record<string, unknown>;
    } | null>("get_current_panel");
    if (cached && current.value === null) {
      setCurrent({ ...cached, title: cached.title ?? cached.panel });
    }
  } catch (err) {
    // 命令缺失（旧壳进程）等问题要看得见，不能静默停在占位页
    errorText.value = "面板数据拉取失败：" + String(err);
  }
}

// webview 面板 html（空串 → 走 schema 面板/占位）
const webviewHtml = computed(() => current.value?.webview?.html ?? "");

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
  if (fadeTimer !== null) clearTimeout(fadeTimer);
  // 窗口销毁（重载等）也清焦点，避免大脑留着旧上下文
  void reportPanelContext(null).catch(() => {});
});
</script>

<template>
  <div class="panel-shell">
    <div class="titlebar" data-tauri-drag-region>
      <span class="name">{{ current?.title ?? "面板" }}</span>
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
      <WebviewPanel
        v-if="current && webviewHtml"
        :key="current.panel"
        :panel="current.panel"
        :html="webviewHtml"
        :data="current.data"
      />
      <SchemaPanel
        v-else-if="current"
        :panel="current.panel"
        :schema="current.schema"
        :data="current.data"
        @action="onAction"
      />
      <div v-else class="placeholder">这里还空空的，喊我一声试试？</div>
    </div>

    <!-- 工作台条：流式回复浮气泡 + 团子 + 上下文 chip + 输入条 -->
    <div ref="barRef" class="bench">
      <transition name="pop">
        <div v-if="replyVisible && replyText" class="reply-bubble" @click="replyVisible = false">
          {{ replyText }}
        </div>
      </transition>
      <div class="bench-bar">
        <Avatar class="pet" :state="state" :size="30" @click="focusInput" @longpress="onMic" />
        <span v-if="chipText" class="chip" :title="chipText">{{ chipText }}</span>
        <InputBar class="bench-input" :busy="busy" :listening="state === 'listen'" @submit="submit" @mic="onMic" @interrupt="onInterrupt" />
      </div>
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
  background: var(--yb-shell-bg);
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
  background: var(--yb-btn-neutral);
}
.confirm-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--yb-space-2);
  margin: 0 var(--yb-space-4) var(--yb-space-2);
  padding: var(--yb-space-2) var(--yb-space-3);
  border-radius: var(--yb-radius-md);
  background: var(--yb-surface-solid);
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
  background: var(--yb-btn-neutral);
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
  background: var(--yb-surface);
}
.placeholder {
  height: 100%;
  display: grid;
  place-items: center;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-lg);
}

/* ---- 工作台条 ---- */
.bench {
  position: relative;
  margin: 0 var(--yb-space-2) var(--yb-space-2);
}
.reply-bubble {
  position: absolute;
  left: 4px;
  right: 4px;
  bottom: calc(100% + 6px);
  max-height: 200px;
  overflow-y: auto;
  padding: var(--yb-space-3) var(--yb-space-4);
  border-radius: var(--yb-radius-lg);
  background: var(--yb-surface-solid);
  border: 1px solid var(--yb-surface-border);
  box-shadow: var(--yb-shadow-soft);
  font-size: var(--yb-fs-md);
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  cursor: pointer;
}
.pop-enter-active,
.pop-leave-active {
  transition: opacity 0.25s ease, transform 0.25s ease;
}
.pop-enter-from,
.pop-leave-to {
  opacity: 0;
  transform: translateY(6px);
}
.bench-bar {
  display: flex;
  align-items: center;
  gap: var(--yb-space-2);
}
.pet {
  flex-shrink: 0;
  cursor: pointer;
}
.chip {
  flex-shrink: 1;
  min-width: 0;
  max-width: 40%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding: 3px var(--yb-space-3);
  border-radius: var(--yb-radius-lg);
  background: var(--yb-accent-soft);
  color: var(--yb-accent);
  font-size: var(--yb-fs-md);
  user-select: none;
}
.bench-input {
  flex: 1;
  min-width: 0;
}
</style>
